"""Catalogue index: vocabularies for query parsing, plus constraint filtering and ranking."""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

from .catalogue import Product
from .text import tokenize

# Attribute keys that are looked up with an explicit "size X" pattern, not as free-text filters.
SIZE_KEYS = {"size", "sizes"}


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

    def copy(self) -> "Spec":
        return Spec(list(self.type_tokens), list(self.desc_tokens), dict(self.filters),
                    {k: list(v) for k, v in self.exclude.items()}, self.price_min, self.price_max, self.size, self.sort)

    def is_empty(self) -> bool:
        return not (self.type_tokens or self.desc_tokens or self.filters or self.exclude or self.price_min is not None
                    or self.price_max is not None or self.size)


class CatalogueIndex:
    def __init__(self, products: list[Product]):
        self.products = {p.id: p for p in products}
        self.ids_upper = {pid.upper(): pid for pid in self.products}
        self.text_tokens: dict[str, set[str]] = {}
        self.title_tokens: dict[str, set[str]] = {}
        self.desc_tokens: dict[str, set[str]] = {}
        self.attr_values: dict[str, dict[tuple, str]] = {}  # key -> {token tuple: original value}
        self.attr_keys: dict[str, str] = {}                 # normalised key token -> key
        df: Counter = Counter()
        for p in products:
            title, cat = set(tokenize(p.title)), set(tokenize(p.category))
            attr_toks: set[str] = set()
            for key, raw in p.attributes.items():
                if key.lower() in SIZE_KEYS:
                    continue
                for val in p.attr_values(key):
                    toks = tuple(tokenize(val))
                    if toks:
                        self.attr_values.setdefault(key, {})[toks] = val
                        attr_toks.update(toks)
                for ktok in tokenize(key):
                    self.attr_keys.setdefault(ktok, key)
            self.title_tokens[p.id] = title
            self.text_tokens[p.id] = title | cat | attr_toks
            self.desc_tokens[p.id] = set(tokenize(p.description))
            df.update(self.text_tokens[p.id] | self.desc_tokens[p.id])
        n = len(products)
        self.idf = {t: math.log((n + 1) / (c + 0.5)) + 1 for t, c in df.items()}
        self.type_vocab = set().union(*self.text_tokens.values()) if products else set()
        self.desc_vocab = set().union(*self.desc_tokens.values()) - self.type_vocab if products else set()

    # -- constraint matching -------------------------------------------------------------------
    def _matches(self, p: Product, spec: Spec, use_desc: bool) -> bool:
        if spec.price_max is not None and p.price > spec.price_max:
            return False
        if spec.price_min is not None and p.price < spec.price_min:
            return False
        for key, value in spec.filters.items():
            if value.lower() not in (v.lower() for v in p.attr_values(key)):
                return False
        for key, banned in spec.exclude.items():
            have = {v.lower() for v in p.attr_values(key)}
            if have & {b.lower() for b in banned}:
                return False
        if spec.size and spec.size.lower() not in (s.lower() for k in SIZE_KEYS for s in p.attr_values(k)):
            return False
        text = self.text_tokens[p.id]
        if not all(t in text for t in spec.type_tokens):
            return False
        if use_desc and not all(t in text or t in self.desc_tokens[p.id] for t in spec.desc_tokens):
            return False
        return True

    def search(self, spec: Spec, query_tokens: set[str] | None = None) -> tuple[list[Product], bool]:
        """Return (ranked matches, description_tokens_were_relaxed)."""
        pool = list(self.products.values())
        hits = [p for p in pool if self._matches(p, spec, use_desc=True)]
        relaxed = False
        if not hits and spec.desc_tokens:
            hits = [p for p in pool if self._matches(p, spec, use_desc=False)]
            relaxed = bool(hits)
        return self.rank(hits, spec, query_tokens or set()), relaxed

    def rank(self, hits: list[Product], spec: Spec, query_tokens: set[str]) -> list[Product]:
        if spec.sort == "price_asc":
            return sorted(hits, key=lambda p: (p.price, -(p.rating or 0)))
        if spec.sort == "price_desc":
            return sorted(hits, key=lambda p: (-p.price, -(p.rating or 0)))
        if spec.sort == "rating_desc":
            return sorted(hits, key=lambda p: (-(p.rating or 0), -(p.rating_count or 0), p.price))

        def score(p: Product):
            rel = sum(self.idf.get(t, 1.0) for t in query_tokens & self.title_tokens[p.id])
            return (-p.in_stock, -rel, -(p.rating or 0), p.price)
        return sorted(hits, key=score)

    def mentioned(self, query_tokens: set[str]) -> list[str]:
        """Products whose full title appears in the query (longest title wins over a contained shorter one)."""
        found = [pid for pid, tt in self.title_tokens.items() if len(tt) >= 2 and tt <= query_tokens]
        return [pid for pid in found if not any(self.title_tokens[pid] < self.title_tokens[o] for o in found)]

    def exact_title_matches(self, hits: list[Product], query_tokens: set[str]) -> list[Product]:
        """Products whose whole title is present in the query (the most specific mention wins)."""
        full = [p for p in hits if self.title_tokens[p.id] and self.title_tokens[p.id] <= query_tokens]
        if not full:
            return []
        longest = max(len(self.title_tokens[p.id]) for p in full)
        return [p for p in full if len(self.title_tokens[p.id]) == longest]
