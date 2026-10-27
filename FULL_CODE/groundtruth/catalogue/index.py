"""Catalogue index: vocabularies and inverted postings for query parsing, constraint filtering and ranking.

Everything is built once at start-up and is read-only afterwards, so one index can serve any number of threads.
"""
from __future__ import annotations

import difflib
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from functools import lru_cache

from .loader import Product
from ..text import tokenize

SIZE_KEY = "size"  # looked up with an explicit "size X" pattern, not as a free-text filter
GENDER_KEY = "gender"


@dataclass
class Spec:
    """A hard-constraint search request."""
    type_tokens: list[str] = field(default_factory=list)   # must appear in title/category/attributes
    desc_tokens: list[str] = field(default_factory=list)   # must appear in description (relaxed if nothing matches)
    filters: dict[str, str] = field(default_factory=dict)  # attribute key -> value
    exclude: dict[str, list[str]] = field(default_factory=dict)  # attribute key -> values to avoid ("not cotton")
    price_min: float | None = None
    price_max: float | None = None
    size: str | None = None
    sort: str | None = None  # price_asc | price_desc | rating_desc
    size_soft: bool = False  # size came from a bare word ("large"), not "size X"; dropped for unsized products

    def copy(self) -> "Spec":
        return Spec(self.type_tokens[:], self.desc_tokens[:], dict(self.filters),
                    {k: v[:] for k, v in self.exclude.items()}, self.price_min, self.price_max, self.size, self.sort,
                    self.size_soft)

    def is_empty(self) -> bool:
        return not (self.type_tokens or self.desc_tokens or self.filters or self.exclude or self.size
                    or self.price_min is not None or self.price_max is not None)


class CatalogueIndex:
    def __init__(self, products: list[Product]):
        self.products = {p.id: p for p in products}
        self.ids_upper = {pid.upper(): pid for pid in self.products}
        self.title_tokens: dict[str, frozenset[str]] = {}
        self.desc_tokens: dict[str, frozenset[str]] = {}
        self.attr_lower: dict[str, dict[str, frozenset[str]]] = {}   # pid -> key -> lower-cased values
        self.attr_values: dict[str, dict[tuple, str]] = {}           # key -> {token tuple: original value}
        self.attr_keys: dict[str, str] = {}                          # normalised key token -> key
        self._text_postings: defaultdict[str, set[str]] = defaultdict(set)   # token in title/category/attrs -> pids
        self._title_postings: defaultdict[str, set[str]] = defaultdict(set)  # token in title -> pids
        self._attr_postings: defaultdict[tuple, set[str]] = defaultdict(set)  # (key, lower value) -> pids
        df: Counter = Counter()
        for p in products:
            title = frozenset(tokenize(p.title))
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
                        text.update(toks)
                self.attr_keys.update({t: key for t in tokenize(key) if t not in self.attr_keys})
            desc = frozenset(tokenize(p.description))
            self.title_tokens[p.id], self.desc_tokens[p.id], self.attr_lower[p.id] = title, desc, lowered
            for tok in text:
                self._text_postings[tok].add(p.id)
            for tok in title:
                self._title_postings[tok].add(p.id)
            df.update(text | desc)
        self.size_values = {low for (key, low) in self._attr_postings if key == SIZE_KEY}
        n = len(products)
        self.idf = {t: math.log((n + 1) / (c + 0.5)) + 1 for t, c in df.items()}
        self.type_vocab = set(self._text_postings)
        self.desc_vocab = {t for t in df if t not in self.type_vocab}
        self.vocab = self.type_vocab | self.desc_vocab
        # longest attribute values first, so "sky blue" wins over "blue"; used by the query parser
        self.attr_candidates = sorted(((len(toks), toks, key, val) for key, vals in self.attr_values.items()
                                       for toks, val in vals.items()), key=lambda c: -c[0])
        by_len: defaultdict[int, list[str]] = defaultdict(list)
        for word in self.type_vocab:
            by_len[len(word)].append(word)
        self._vocab_by_len = by_len
        self.closest = lru_cache(maxsize=4096)(self._closest)

    # -- typo tolerance ------------------------------------------------------------------------
    def _closest(self, token: str) -> str | None:
        pool = [w for n in range(len(token) - 2, len(token) + 3) for w in self._vocab_by_len.get(n, ())]
        match = difflib.get_close_matches(token, pool, n=1, cutoff=0.85)
        return match[0] if match else None

    # -- constraint matching -------------------------------------------------------------------
    def _candidate_ids(self, spec: Spec) -> set[str]:
        """Narrow the catalogue with postings before the per-product checks."""
        sets = [self._text_postings.get(t, set()) for t in spec.type_tokens]
        for k, v in spec.filters.items():
            hit = self._attr_postings.get((k, v.lower()), set())
            if k == GENDER_KEY and v.lower() in ("men", "women"):  # unisex items suit everyone
                hit = hit | self._attr_postings.get((k, "unisex"), set())
            sets.append(hit)
        if not sets:
            return set(self.products)
        sets.sort(key=len)
        return set.intersection(*sets)

    def _matches(self, p: Product, spec: Spec, use_desc: bool) -> bool:
        if (spec.price_max is not None and p.price > spec.price_max) or (
                spec.price_min is not None and p.price < spec.price_min):
            return False
        attrs = self.attr_lower[p.id]
        if any(attrs.get(k, frozenset()) & {b.lower() for b in banned} for k, banned in spec.exclude.items()):
            return False
        if spec.size and spec.size.lower() not in attrs.get(SIZE_KEY, ()):
            return False
        if use_desc:
            text_hit = self.desc_tokens[p.id]
            return all(t in text_hit or p.id in self._text_postings.get(t, ()) for t in spec.desc_tokens)
        return True

    def search(self, spec: Spec, query_tokens: set[str] | frozenset[str] = frozenset()) -> tuple[list[Product], bool]:
        """Return (ranked matches, description_tokens_were_relaxed)."""
        pool = [self.products[i] for i in self._candidate_ids(spec)]
        hits = [p for p in pool if self._matches(p, spec, use_desc=True)]
        relaxed = False
        if not hits and spec.desc_tokens:
            hits = [p for p in pool if self._matches(p, spec, use_desc=False)]
            relaxed = bool(hits)
        if not hits and spec.size and spec.size_soft and pool and not any(self.attr_lower[p.id].get(SIZE_KEY) for p in pool):
            # "large water bottle": bottles have no sizes at all, so the size word was descriptive, not a filter
            unsized = spec.copy()
            unsized.size = None
            return self.search(unsized, query_tokens)
        return self.rank(hits, spec, query_tokens), relaxed

    def rank(self, hits: list[Product], spec: Spec, query_tokens: set[str] | frozenset[str]) -> list[Product]:
        if spec.sort == "price_asc":
            return sorted(hits, key=lambda p: (p.price, -(p.rating or 0), p.id))
        if spec.sort == "price_desc":
            return sorted(hits, key=lambda p: (-p.price, -(p.rating or 0), p.id))
        if spec.sort == "rating_desc":
            return sorted(hits, key=lambda p: (-(p.rating or 0), -(p.rating_count or 0), p.price, p.id))

        def score(p: Product):
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
