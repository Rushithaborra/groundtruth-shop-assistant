"""Catalogue index: vocabularies and inverted postings for query parsing, constraint filtering and ranking.

Everything is built once at start-up and is read-only afterwards, so one index can serve any number of threads.
"""

from __future__ import annotations

import bisect
import difflib
import heapq
import math
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Callable

from ..config import get_settings
from ..text import STOPWORDS, normalise_word, tokenize
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
    alternatives: dict[str, list[str]] = field(default_factory=dict)  # "grey or black": other values a filter accepts

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
            {k: v[:] for k, v in self.alternatives.items()},
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


class RankedHits(Sequence):
    """Search matches in rank order, sorted only as far as the caller looks. A listing needs the count and the top
    few, which cost one pass over the matches; the full order is built the first time something walks all of them,
    and is then exactly the order a plain sort gives."""

    def __init__(self, hits: list[Product], key: Callable[[Product], tuple]) -> None:
        self._hits, self._key = hits, key
        self._top: list[Product] = []
        self._full: list[Product] | None = None

    def __len__(self) -> int:
        return len(self._hits)

    def _ordered(self) -> list[Product]:
        if self._full is None:
            self._full = sorted(self._hits, key=self._key)
        return self._full

    def __getitem__(self, i):  # type: ignore[no-untyped-def]
        """A product by rank, or a slice of ranks; a short prefix is found without sorting every match."""
        if self._full is None and isinstance(i, slice) and i.start in (None, 0) and i.step is None:
            wanted = i.stop if i.stop is not None and i.stop >= 0 else None
            if wanted is not None and wanted * 4 < len(self._hits):
                if len(self._top) < wanted:
                    self._top = heapq.nsmallest(wanted, self._hits, key=self._key)
                return self._top[:wanted]
        if self._full is None and isinstance(i, int) and i == 0 and self._hits:
            if not self._top:
                self._top = heapq.nsmallest(1, self._hits, key=self._key)
            return self._top[0]
        return self._ordered()[i]

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter(self._ordered())

    def __eq__(self, other: object) -> bool:
        return list(self) == list(other) if isinstance(other, (list, tuple, RankedHits)) else NotImplemented

    __hash__ = None  # type: ignore[assignment]


class CatalogueIndex:
    """The catalogue made searchable: products by id, inverted indexes over words and attribute values, and typo
    matching against the catalogue's own vocabulary."""

    def __init__(
        self, products: list[Product], typo_cutoff: float | None = None, typo_cache_size: int | None = None
    ) -> None:
        search = get_settings().search
        self.typo_cutoff = typo_cutoff if typo_cutoff is not None else search.typo_cutoff
        self.products = {p.id: p for p in products}
        self.ids_upper = {pid.upper(): pid for pid in self.products}
        self._declare_tables()
        self._doc_freq: Counter = Counter()
        for p in products:
            self._doc_freq.update(self._index_product(p))
        self._build_vocabulary(self._doc_freq, len(products))
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
        self._named_keys: set[str] = set()  # attribute names already made askable
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
            if key not in self._named_keys:  # each attribute name is indexed once, not once per product
                self._named_keys.add(key)
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
        self._build_kind_tables()
        self._build_title_anchors()
        self._build_price_index()

    def _build_price_index(self) -> None:
        """Products in price order, so a budget is two binary searches instead of a check on every product."""
        ordered = sorted(self.products.values(), key=lambda p: p.price)
        self._price_table = ([p.price for p in ordered], [p.id for p in ordered])  # replaced whole, never edited

    def _ids_in_budget(self, low: float | None, high: float | None) -> set[str]:
        """Ids of the products priced within [low, high] (either end may be open)."""
        prices, ids = self._price_table
        start = bisect.bisect_left(prices, low) if low is not None else 0
        end = bisect.bisect_right(prices, high) if high is not None else len(prices)
        return set(ids[start:end])

    def _build_title_anchors(self) -> None:
        """File each product (of two or more title words) under its rarest title word. A title can only appear in a
        query if all its words do, so the query's own words find every candidate without walking the long postings
        of common words like "shoes"."""
        anchors: defaultdict[str, list[str]] = defaultdict(list)
        for pid, title in self.title_tokens.items():
            if len(title) >= 2:
                anchors[min(title, key=lambda t: (len(self._title_postings[t]), t))].append(pid)
        self._title_anchors = dict(anchors)

    def _build_kind_tables(self) -> None:
        """For each kind of product: the attributes its products have and the words in their titles and values.
        A word is then read against the kind the shopper named: "oily" is skin_type for sunscreen, hair_type for
        shampoo."""
        # keyed by the kind's words, as a query reads it: "Kurtas" and "Kurtis" are both ("kurta",) and share a table
        keys: defaultdict[tuple, set[str]] = defaultdict(set)
        words: defaultdict[tuple, set[str]] = defaultdict(set)
        for pid, p in self.products.items():
            name = p.attributes.get(KIND_KEY)
            if not isinstance(name, str):
                continue
            kind = tuple(tokenize(name))
            keys[kind].update(p.attributes)
            words[kind].update(self.title_tokens[pid])
            for key in p.attributes:
                for value in p.attr_values(key):
                    words[kind].update(tokenize(value))
        self._kind_keys = {k: frozenset(v) for k, v in keys.items()}
        self._kind_words = {k: frozenset(v) for k, v in words.items()}

    def product_count(self, token: str) -> int:
        """How many products have this word in their title, category or attribute values."""
        return len(self._text_postings.get(token, ()))

    def kind_keys(self, kind: str | None) -> frozenset[str]:
        """The attributes products of this kind have (empty for an unknown kind)."""
        return self._kind_keys.get(tuple(tokenize(kind or "")), frozenset())

    def kind_words(self, kind: str | None) -> frozenset[str]:
        """The words in the titles and attribute values of products of this kind (empty for an unknown kind)."""
        return self._kind_words.get(tuple(tokenize(kind or "")), frozenset())

    def _build_typo_lookup(self, cache_size: int) -> None:
        """Group the product-type words by length (typo matching only compares similar lengths) and cache matches."""
        by_len: defaultdict[int, list[str]] = defaultdict(list)
        for word in self.type_vocab:
            by_len[len(word)].append(word)
        self._vocab_by_len = by_len
        self.closest = lru_cache(maxsize=cache_size)(self._closest)

    def _index_key_name(self, key: str) -> None:
        """Make an attribute's name askable: by any of its words, and by its full name when that has several."""
        # not by a function word: "price of" doesn't ask about pack_of
        words = [t for t in tokenize(key) if not t.isdigit() and t not in UNIT_WORDS and t not in STOPWORDS]
        for t in words:
            self.attr_keys.setdefault(t, key)
            group = self.attr_key_groups.setdefault(t, [])
            if key not in group:
                group.append(key)
        if len(words) >= 2:
            self.attr_key_phrases.setdefault(tuple(words), key)

    # -- live updates --------------------------------------------------------------------------
    # Not thread-safe on their own: `ShoppingAssistant.apply_updates` runs them under a write lock that every
    # question also takes (as a reader), so a question never sees a half-changed index.
    def update_price_stock(
        self, pid: str, price: float | None = None, in_stock: bool | None = None, stock_status: str | None = None
    ) -> bool:
        """Change a product's price or stock in place: the fields that change all day and need no re-indexing."""
        p = self.products.get(pid)
        if p is None:
            return False
        if in_stock is not None:
            p.in_stock = in_stock
        if stock_status is not None:
            p.stock_status = stock_status
        if price is not None and price != p.price:
            p.price = price
            self._build_price_index()
        return True

    def _words(self, p: Product) -> tuple[set[str], set[str]]:
        """(the product's title/category/attribute words, its description words): what `_index_product` posts."""
        text = set(tokenize(p.title)) | set(tokenize(p.category))
        for key in p.attributes:
            if key != SIZE_KEY:
                for val in p.attr_values(key):
                    text.update(tokenize(val))
        return text, set(tokenize(p.description))

    def add_product(self, p: Product) -> None:
        """Index a new product, or replace one with the same id."""
        if p.id in self.products:
            self.remove_product(p.id)
        self.products[p.id] = p
        self.ids_upper[p.id.upper()] = p.id
        self._doc_freq.update(self._index_product(p))
        title = self.title_tokens[p.id]
        if len(title) >= 2:
            self._title_anchors.setdefault(min(title, key=lambda t: (len(self._title_postings[t]), t)), []).append(p.id)

    def remove_product(self, pid: str) -> bool:
        """Take a product out of every lookup table. (Attribute vocabularies keep values only it had; that is
        harmless, and a full rebuild tidies it.)"""
        p = self.products.pop(pid, None)
        if p is None:
            return False
        self.ids_upper.pop(pid.upper(), None)
        text, desc = self._words(p)
        self._doc_freq.subtract(text | desc)
        for tok in text:
            _discard(self._text_postings, tok, pid)
        for tok in self.title_tokens.pop(pid, ()):
            _discard(self._title_postings, tok, pid)
            if pid in self._title_anchors.get(tok, ()):
                self._title_anchors[tok].remove(pid)
        for key, lowered in self.attr_lower.pop(pid, {}).items():
            for low in lowered:
                _discard(self._attr_postings, (key, low), pid)
        for key in p.attributes:
            for val in p.attr_values(key):
                _discard(self._attr_token_postings, (key, tuple(tokenize(val))), pid)
        self.desc_tokens.pop(pid, None)
        return True

    def refresh(self) -> None:
        """Rebuild what depends on the whole catalogue (word weights, vocabularies, kind tables, price order) after a
        batch of adds and removes. Costs the number of distinct words, not of products, except the price order."""
        self._doc_freq = Counter({t: c for t, c in self._doc_freq.items() if c > 0})
        self._build_vocabulary(self._doc_freq, len(self.products))
        self._build_typo_lookup(self.closest.cache_info().maxsize or 0)

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
            hit: set[str] = set()
            for value in (v, *spec.alternatives.get(k, ())):
                hit |= self._attr_postings.get((k, value.lower()), set())
                hit |= self._attr_token_postings.get((k, tuple(tokenize(value))), set())
                if k == GENDER_KEY and value.lower() in ("men", "women"):  # unisex items suit everyone
                    hit |= self._attr_postings.get((k, "unisex"), set())
            sets.append(hit)
        if spec.price_min is not None or spec.price_max is not None:
            sets.append(self._ids_in_budget(spec.price_min, spec.price_max))
        if not sets:
            return set(self.products)
        sets.sort(key=len)
        return set.intersection(*sets)

    def _matcher(self, spec: Spec, use_desc: bool) -> Callable[[Product], bool]:
        """A test for "meets every hard constraint" with everything that depends only on the spec worked out once,
        not once per product (the per-product call is what dominates a search over a large catalogue)."""
        price_min, price_max = spec.price_min, spec.price_max
        banned = [(k, {b.lower() for b in values}) for k, values in spec.exclude.items()]
        size = spec.size.lower() if spec.size else None
        in_stock_only, measures = spec.in_stock_only, spec.measures
        desc_tokens = spec.desc_tokens if use_desc else []
        attr_lower, desc_index, postings = self.attr_lower, self.desc_tokens, self._text_postings
        empty: frozenset = frozenset()

        def matches(p: Product) -> bool:
            """Whether one product meets the constraints."""
            if (price_max is not None and p.price > price_max) or (price_min is not None and p.price < price_min):
                return False
            attrs = attr_lower[p.id]
            if any(attrs.get(k, empty) & values for k, values in banned):
                return False
            if size and size not in attrs.get(SIZE_KEY, ()):
                return False
            if in_stock_only and p.in_stock is not True:
                return False
            if any(not any(_numeric(v) == value for v in _unit_values(p, unit)) for unit, value in measures):
                return False
            if desc_tokens:
                text_hit = desc_index[p.id]
                return all(t in text_hit or p.id in postings.get(t, ()) for t in desc_tokens)
            return True

        return matches

    def _matches(self, p: Product, spec: Spec, use_desc: bool) -> bool:
        """Whether a product meets every hard constraint: budget, exclusions, size, stock, units, filters."""
        return self._matcher(spec, use_desc)(p)

    def matching(self, spec: Spec) -> tuple[list[Product], bool]:
        """(every product that meets the spec, in no particular order; description words were relaxed). Cheaper than
        `search` when only the set matters, because nothing is ranked."""
        pool = [self.products[i] for i in self._candidate_ids(spec)]
        usable = [m for m in spec.measures if any(_unit_values(p, m[0]) for p in pool)]
        if usable != spec.measures:  # "65W kettle" when kettles list no wattage: the number can't be checked, drop it
            spec = spec.copy()
            spec.measures = usable
        hits = self._apply(spec, pool, True)
        relaxed = False
        if not hits and spec.desc_tokens:
            hits = self._apply(spec, pool, False)
            relaxed = bool(hits)
        return hits, relaxed

    def _apply(self, spec: Spec, pool: list[Product], use_desc: bool) -> list[Product]:
        """The pool's products that meet the spec. The postings and the budget index have already enforced the
        type words, filters and price, so with nothing else to check the whole pool qualifies."""
        nothing_left = not (
            spec.exclude or spec.size or spec.in_stock_only or spec.measures or (use_desc and spec.desc_tokens)
        )
        return pool if nothing_left else list(filter(self._matcher(spec, use_desc), pool))

    def search(
        self, spec: Spec, query_tokens: set[str] | frozenset[str] = frozenset()
    ) -> tuple[Sequence[Product], bool]:
        """Return (ranked matches, description_tokens_were_relaxed)."""
        hits, relaxed = self.matching(spec)
        if not hits and spec.size and spec.size_soft:
            pool = [self.products[i] for i in self._candidate_ids(spec)]
            if pool and not any(self.attr_lower[p.id].get(SIZE_KEY) for p in pool):
                # "large water bottle": bottles have no sizes at all, so the size word was descriptive, not a filter
                unsized = spec.copy()
                unsized.size = None
                return self.search(unsized, query_tokens)
        return self.rank(hits, spec, query_tokens), relaxed

    def rank(self, hits: list[Product], spec: Spec, query_tokens: set[str] | frozenset[str]) -> Sequence[Product]:
        """Order matches: by the requested sort, else in stock first, then query-word relevance and rating."""
        if spec.sort == "price_asc":
            return RankedHits(hits, lambda p: (p.price, -(p.rating or 0), p.id))
        if spec.sort == "price_desc":
            return RankedHits(hits, lambda p: (-p.price, -(p.rating or 0), p.id))
        if spec.sort == "rating_desc":
            return RankedHits(hits, lambda p: (-(p.rating or 0), -(p.rating_count or 0), p.price, p.id))
        relevance = self._relevance(query_tokens)
        return RankedHits(hits, lambda p: (not p.in_stock, -relevance.get(p.id, 0.0), -(p.rating or 0), p.price, p.id))

    def _relevance(self, query_tokens: set[str] | frozenset[str]) -> dict[str, float]:
        """Product id -> summed weight of the query words in its title; products with none are left out. Built from
        the title postings, so it costs the number of products that share a word with the query, not the catalogue."""
        scores: dict[str, float] = {}
        for t in sorted(query_tokens):  # a fixed order, so equal sums are bit-for-bit equal
            weight = self.idf.get(t, 1.0)
            for pid in self._title_postings.get(t, ()):
                scores[pid] = scores.get(pid, 0.0) + weight
        return scores

    def has_text_token(self, pid: str, token: str) -> bool:
        """Whether a token appears in this product's title, category or attribute values."""
        return pid in self._text_postings.get(token, ())

    def mentioned(self, query_tokens: list[str]) -> list[str]:
        """Products whose full title appears in the query (a longer title wins over a contained shorter one),
        in the order the shopper named them; ties go by product id so the order never depends on hashing."""
        first_at: dict[str, int] = {}
        for i, t in enumerate(query_tokens):
            first_at.setdefault(t, i)
        words = first_at.keys()
        found = [pid for t in first_at for pid in self._title_anchors.get(t, ()) if self.title_tokens[pid] <= words]
        found = [pid for pid in found if not any(self.title_tokens[pid] < self.title_tokens[o] for o in found)]
        return sorted(found, key=lambda pid: (min(first_at[t] for t in self.title_tokens[pid]), pid))

    def exact_title_matches(self, hits: list[Product], query_tokens: set[str]) -> list[Product]:
        """Products whose whole title is present in the query (the most specific mention wins)."""
        full = [p for p in hits if self.title_tokens[p.id] and self.title_tokens[p.id] <= query_tokens]
        if not full:
            return []
        longest = max(len(self.title_tokens[p.id]) for p in full)
        return [p for p in full if len(self.title_tokens[p.id]) == longest]


def _discard(postings: dict, key: object, pid: str) -> None:
    """Remove a product from one posting list, dropping the list when it empties (so no word outlives its products)."""
    ids = postings.get(key)
    if ids is not None:
        ids.discard(pid)
        if not ids:
            del postings[key]


def _slips(token: str) -> list[str]:
    """The word with one typing slip undone: two neighbouring letters swapped back, or a doubled letter single."""
    swapped = [token[:i] + token[i + 1] + token[i] + token[i + 2 :] for i in range(len(token) - 1)]
    single = [token[:i] + token[i + 1 :] for i in range(1, len(token)) if token[i] == token[i - 1]]
    return [w for w in dict.fromkeys(swapped + single) if w != token]


def _unit_values(p: Product, unit: str) -> list:
    """Values of the product's attributes measured in `unit`: "w" -> wattage_w, output_w; "pieces" -> pieces."""
    return [v for k, v in p.attributes.items() if k == unit or k.endswith(f"_{unit}")]


def _numeric(value: object) -> float | None:
    """A measurement as a number ("65" -> 65.0); booleans and text that isn't a number give None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except ValueError:
        return None
