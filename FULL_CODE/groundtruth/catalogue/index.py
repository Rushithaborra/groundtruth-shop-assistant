"""Catalogue index: vocabularies and inverted postings for query parsing, constraint filtering and ranking.

Everything is built once at start-up and is read-only afterwards, so one index can serve any number of threads.
"""

from __future__ import annotations

import difflib
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from functools import lru_cache

from ..config import get_settings
from ..text import normalise_word, tokenize
from .loader import Product

SIZE_KEY = "size"  # looked up with an explicit "size X" pattern, not as a free-text filter
# Unit suffixes in attribute names ("thickness_mm", "wattage_w"); they say how a value is measured, not what it is
UNIT_WORDS = frozenset({"mm", "cm", "m", "ft", "in", "w", "l", "ml", "mah", "kg", "g", "c", "min", "hour", "hours"})
GENDER_KEY = "gender"
KIND_KEY = "sub_category"  # the attribute that names the kind of product ("Running Shoes")


@dataclass
class Spec:
    """A hard-constraint search request."""

    type_tokens: list[str] = field(default_factory=list)  # must appear in title/category/attributes
    desc_tokens: list[str] = field(default_factory=list)  # must appear in description (relaxed if nothing matches)
    filters: dict[str, str] = field(default_factory=dict)  # attribute key -> value
    exclude: dict[str, list[str]] = field(default_factory=dict)  # attribute key -> values to avoid ("not cotton")
    price_min: float | None = None
    price_max: float | None = None
    size: str | None = None
    sort: str | None = None  # price_asc | price_desc | rating_desc
    size_soft: bool = False  # size came from a bare word ("large"), not "size X"; dropped for unsized products
    measures: list[tuple[str, float]] = field(default_factory=list)  # ("w", 65.0) from "65W": unit suffix, value
    in_stock_only: bool = False  # "cheapest one that's in stock"

    def copy(self) -> Spec:
        """An independent copy that can be changed without touching this one."""
        return Spec(
            self.type_tokens[:],
            self.desc_tokens[:],
            dict(self.filters),
            {k: v[:] for k, v in self.exclude.items()},
            self.price_min,
            self.price_max,
            self.size,
            self.sort,
            self.size_soft,
            self.measures[:],
            self.in_stock_only,
        )

    def is_empty(self) -> bool:
        """No product, filter, budget, size or exclusion at all."""
        return not (
            self.type_tokens
            or self.desc_tokens
            or self.filters
            or self.exclude
            or self.size
            or self.measures
            or self.price_min is not None
            or self.price_max is not None
        )


class CatalogueIndex:
    def __init__(
        self, products: list[Product], typo_cutoff: float | None = None, typo_cache_size: int | None = None
    ) -> None:
        search = get_settings().search
        self.typo_cutoff = typo_cutoff if typo_cutoff is not None else search.typo_cutoff
        self.products = {p.id: p for p in products}
        self.ids_upper = {pid.upper(): pid for pid in self.products}
        self._declare_tables()
        doc_freq: Counter = Counter()
        for p in products:
            doc_freq.update(self._index_product(p))
        self._build_vocabulary(doc_freq, len(products))
        self._build_typo_lookup(typo_cache_size if typo_cache_size is not None else search.typo_cache_size)

    def _declare_tables(self) -> None:
        """The empty lookup tables that `_index_product` fills."""
        self.title_tokens: dict[str, frozenset[str]] = {}
        self.desc_tokens: dict[str, frozenset[str]] = {}
        self.attr_lower: dict[str, dict[str, frozenset[str]]] = {}  # pid -> key -> lower-cased values
        self.attr_values: dict[str, dict[tuple, str]] = {}  # key -> {token tuple: original value}
        self.attr_keys: dict[str, str] = {}  # normalised key token -> key
        self.attr_key_groups: dict[str, list[str]] = {}  # key token -> every key containing it ("water" -> 2 keys)
        self.attr_key_phrases: dict[tuple, str] = {}  # multi-word key names: ("shoe", "compartment") -> key
        self.name_pairs: set[tuple[str, str]] = set()  # adjacent words in product names: ("hair", "dryer")
        self._text_postings: defaultdict[str, set[str]] = defaultdict(set)  # token in title/category/attrs -> pids
        self._title_postings: defaultdict[str, set[str]] = defaultdict(set)  # token in title -> pids
        self._attr_postings: defaultdict[tuple, set[str]] = defaultdict(set)  # (key, lower value) -> pids
        # (key, value tokens) -> pids: "Kurtas" and "Kurtis" read the same, so a filter on one matches both
        self._attr_token_postings: defaultdict[tuple, set[str]] = defaultdict(set)

    def _index_product(self, p: Product) -> set[str]:
        """Add one product to every lookup table; returns its words, for the document frequencies."""
        title = frozenset(tokenize(p.title))
        for words in (tokenize(p.title), tokenize(str(p.attributes.get("sub_category", "")))):
            self.name_pairs.update(zip(words, words[1:]))
        text = set(title) | set(tokenize(p.category))
        lowered = {}
        for key in p.attributes:
            values = p.attr_values(key)
            lowered[key] = frozenset(v.lower() for v in values)
            for low in lowered[key]:
                self._attr_postings[(key, low)].add(p.id)
            if key == SIZE_KEY:
                continue
            for val in values:
                toks = tuple(tokenize(val))
                if toks:
                    self.attr_values.setdefault(key, {})[toks] = val
                    self._attr_token_postings[(key, toks)].add(p.id)
                    text.update(toks)
            self._index_key_name(key)
        desc = frozenset(tokenize(p.description))
        self.title_tokens[p.id], self.desc_tokens[p.id], self.attr_lower[p.id] = title, desc, lowered
        for tok in text:
            self._text_postings[tok].add(p.id)
        for tok in title:
            self._title_postings[tok].add(p.id)
        return text | desc

    def _build_vocabulary(self, doc_freq: Counter, n_products: int) -> None:
        """Word weights, the product-type and description vocabularies, and the attribute values to look for."""
        self.size_values = {low for (key, low) in self._attr_postings if key == SIZE_KEY}
        self.idf = {t: math.log((n_products + 1) / (c + 0.5)) + 1 for t, c in doc_freq.items()}
        self.type_vocab = set(self._text_postings)
        self.desc_vocab = {t for t in doc_freq if t not in self.type_vocab}
        self.vocab = self.type_vocab | self.desc_vocab
        # longest attribute values first, so "sky blue" wins over "blue"; used by the query parser. Of equal length,
        # kinds of product come last: in "iron tawa", "iron" is the material and "tawa" the kind
        self.attr_candidates = sorted(
            ((len(toks), toks, key, val) for key, vals in self.attr_values.items() for toks, val in vals.items()),
            key=lambda c: (-c[0], c[2] == KIND_KEY),
        )
        # the nouns that name a kind of product sold ("saree" in Sarees, "cable" in USB Cables; not "water" in Water
        # Bottles): naming one names the product, so it doesn't by itself ask about an attribute that shares the
        # word (saree_length_m, cable_type)
        self.kind_nouns = frozenset(toks[-1] for toks in self.attr_values.get(KIND_KEY, {}))

    def _build_typo_lookup(self, cache_size: int) -> None:
        """Group the product-type words by length (typo matching only compares similar lengths) and cache matches."""
        by_len: defaultdict[int, list[str]] = defaultdict(list)
        for word in self.type_vocab:
            by_len[len(word)].append(word)
        self._vocab_by_len = by_len
        self.closest = lru_cache(maxsize=cache_size)(self._closest)

    def _index_key_name(self, key: str) -> None:
        """Make an attribute's name askable: by any of its words, and by its full name when that has several."""
        words = [t for t in tokenize(key) if not t.isdigit() and t not in UNIT_WORDS]
        for t in words:
            self.attr_keys.setdefault(t, key)
            group = self.attr_key_groups.setdefault(t, [])
            if key not in group:
                group.append(key)
        if len(words) >= 2:
            self.attr_key_phrases.setdefault(tuple(words), key)

    # -- typo tolerance ------------------------------------------------------------------------
    def _closest(self, token: str) -> str | None:
        """The catalogue word a misspelt word most likely means: a swapped or doubled letter first ("sihrt",
        "kurtti"), which a similarity ratio scores too low, then the closest word of a similar length."""
        for slip in _slips(token):
            word = normalise_word(slip)
            if word in self.type_vocab:
                return word
        pool = [w for n in range(len(token) - 2, len(token) + 3) for w in self._vocab_by_len.get(n, ())]
        match = difflib.get_close_matches(token, pool, n=1, cutoff=self.typo_cutoff)
        return match[0] if match else None

    # -- constraint matching -------------------------------------------------------------------
    def _candidate_ids(self, spec: Spec) -> set[str]:
        """Narrow the catalogue with postings before the per-product checks."""
        sets = [self._text_postings.get(t, set()) for t in spec.type_tokens]
        for k, v in spec.filters.items():
            hit = self._attr_postings.get((k, v.lower()), set()) | self._attr_token_postings.get(
                (k, tuple(tokenize(v))), set()
            )
            if k == GENDER_KEY and v.lower() in ("men", "women"):  # unisex items suit everyone
                hit = hit | self._attr_postings.get((k, "unisex"), set())
            sets.append(hit)
        if not sets:
            return set(self.products)
        sets.sort(key=len)
        return set.intersection(*sets)

    def _matches(self, p: Product, spec: Spec, use_desc: bool) -> bool:
        if (spec.price_max is not None and p.price > spec.price_max) or (
            spec.price_min is not None and p.price < spec.price_min
        ):
            return False
        attrs = self.attr_lower[p.id]
        if any(attrs.get(k, frozenset()) & {b.lower() for b in banned} for k, banned in spec.exclude.items()):
            return False
        if spec.size and spec.size.lower() not in attrs.get(SIZE_KEY, ()):
            return False
        if spec.in_stock_only and p.in_stock is not True:
            return False
        if any(not any(_numeric(v) == value for v in _unit_values(p, unit)) for unit, value in spec.measures):
            return False
        if use_desc:
            text_hit = self.desc_tokens[p.id]
            return all(t in text_hit or p.id in self._text_postings.get(t, ()) for t in spec.desc_tokens)
        return True

    def search(self, spec: Spec, query_tokens: set[str] | frozenset[str] = frozenset()) -> tuple[list[Product], bool]:
        """Return (ranked matches, description_tokens_were_relaxed)."""
        pool = [self.products[i] for i in self._candidate_ids(spec)]
        usable = [m for m in spec.measures if any(_unit_values(p, m[0]) for p in pool)]
        if usable != spec.measures:  # "65W kettle" when kettles list no wattage: the number can't be checked, drop it
            spec = spec.copy()
            spec.measures = usable
        hits = [p for p in pool if self._matches(p, spec, use_desc=True)]
        relaxed = False
        if not hits and spec.desc_tokens:
            hits = [p for p in pool if self._matches(p, spec, use_desc=False)]
            relaxed = bool(hits)
        if (
            not hits
            and spec.size
            and spec.size_soft
            and pool
            and not any(self.attr_lower[p.id].get(SIZE_KEY) for p in pool)
        ):
            # "large water bottle": bottles have no sizes at all, so the size word was descriptive, not a filter
            unsized = spec.copy()
            unsized.size = None
            return self.search(unsized, query_tokens)
        return self.rank(hits, spec, query_tokens), relaxed

    def rank(self, hits: list[Product], spec: Spec, query_tokens: set[str] | frozenset[str]) -> list[Product]:
        """Order matches: by the requested sort, else in stock first, then query-word relevance and rating."""
        if spec.sort == "price_asc":
            return sorted(hits, key=lambda p: (p.price, -(p.rating or 0), p.id))
        if spec.sort == "price_desc":
            return sorted(hits, key=lambda p: (-p.price, -(p.rating or 0), p.id))
        if spec.sort == "rating_desc":
            return sorted(hits, key=lambda p: (-(p.rating or 0), -(p.rating_count or 0), p.price, p.id))

        def score(p: Product) -> tuple[bool, float, float, float, str]:
            rel = sum(self.idf.get(t, 1.0) for t in query_tokens & self.title_tokens[p.id])
            return (not p.in_stock, -rel, -(p.rating or 0), p.price, p.id)

        return sorted(hits, key=score)

    def has_text_token(self, pid: str, token: str) -> bool:
        """Whether a token appears in this product's title, category or attribute values."""
        return pid in self._text_postings.get(token, ())

    def mentioned(self, query_tokens: list[str]) -> list[str]:
        """Products whose full title appears in the query (a longer title wins over a contained shorter one),
        in the order the shopper named them; ties go by product id so the order never depends on hashing."""
        first_at: dict[str, int] = {}
        for i, t in enumerate(query_tokens):
            first_at.setdefault(t, i)
        seen = Counter(pid for t in first_at for pid in self._title_postings.get(t, ()))
        found = [pid for pid, n in seen.items() if n == len(self.title_tokens[pid]) >= 2]
        found = [pid for pid in found if not any(self.title_tokens[pid] < self.title_tokens[o] for o in found)]
        return sorted(found, key=lambda pid: (min(first_at[t] for t in self.title_tokens[pid]), pid))

    def exact_title_matches(self, hits: list[Product], query_tokens: set[str]) -> list[Product]:
        """Products whose whole title is present in the query (the most specific mention wins)."""
        full = [p for p in hits if self.title_tokens[p.id] and self.title_tokens[p.id] <= query_tokens]
        if not full:
            return []
        longest = max(len(self.title_tokens[p.id]) for p in full)
        return [p for p in full if len(self.title_tokens[p.id]) == longest]


def _slips(token: str) -> list[str]:
    """The word with one typing slip undone: two neighbouring letters swapped back, or a doubled letter single."""
    swapped = [token[:i] + token[i + 1] + token[i] + token[i + 2 :] for i in range(len(token) - 1)]
    single = [token[:i] + token[i + 1 :] for i in range(1, len(token)) if token[i] == token[i - 1]]
    return [w for w in dict.fromkeys(swapped + single) if w != token]


def _unit_values(p: Product, unit: str) -> list:
    """Values of the product's attributes measured in `unit`: "w" -> wattage_w, output_w; "pieces" -> pieces."""
    return [v for k, v in p.attributes.items() if k == unit or k.endswith(f"_{unit}")]


def _numeric(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except ValueError:
        return None
