"""Catalogue index: vocabularies and inverted postings for query parsing, constraint filtering and ranking.

Everything is built once at start-up and is read-only afterwards, so one index can serve any number of threads.
"""

from __future__ import annotations

import bisect
import difflib
import heapq
import math
import operator
from collections import Counter, defaultdict
from collections.abc import Collection, Iterator, Sequence
from dataclasses import dataclass, field
from functools import lru_cache, partial
from itertools import compress
from typing import Callable

from ..config import get_settings
from ..text import STOPWORDS, SYNONYMS, normalise_word, stem, tokenize
from .loader import Product

SIZE_KEY = "size"  # looked up with an explicit "size X" pattern, not as a free-text filter
# Unit suffixes in attribute names ("thickness_mm", "wattage_w"); they say how a value is measured, not what it is
UNIT_WORDS = frozenset({"mm", "cm", "m", "ft", "in", "w", "l", "ml", "mah", "kg", "g", "c", "min", "hour", "hours"})
GENDER_KEY = "gender"
KIND_KEY = "sub_category"  # the attribute that names the kind of product ("Running Shoes")
SHORT_TYPO_LENGTHS = range(3, 8)  # a word this long is also mended by one vowel slip, if exactly one product word fits
VOWELS = frozenset("aeiou")
SHORT_TARGET_MIN = 4  # ... and that product word has at least this many letters ("jen" -> "jean", never "jen" -> "pen")
SUGGEST_CUTOFF = 0.8  # similarity for a "did you mean" offer; looser than the 0.85 of a correction, since it only asks
SUGGEST_MIN_LENGTH = 5  # a shorter word is too easily a real word of its own ("saal", "chawal") to be called a typo


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


_EMPTY: frozenset = frozenset()
# the part of each ranking that doesn't depend on the query: ids sorted by it once, then reused for every search
_TAIL_KEYS: dict[str, Callable[[Product], tuple]] = {
    "default": lambda p: (-(p.rating or 0), p.price, p.id),
    "price_asc": lambda p: (p.price, -(p.rating or 0), p.id),
    "price_desc": lambda p: (-p.price, -(p.rating or 0), p.id),
    "rating_desc": lambda p: (-(p.rating or 0), -(p.rating_count or 0), p.price, p.id),
}


class RankedHits(Sequence):
    """Search matches in rank order, found only as far as the caller looks. A listing needs the count and the top few,
    which cost a pass over a few sets; the full order (a sort of every match) is built only the first time something
    walks all of them, and is then exactly the order a plain sort gives."""

    def __init__(
        self,
        ids: Collection[str],
        top: Callable[[int], list[Product]],
        full: Callable[[], list[Product]],
        unordered: Callable[[], list[Product]],
    ) -> None:
        self.ids = ids  # every matching product id, in no particular order
        self._top_fn, self._full_fn, self._unordered_fn = top, full, unordered
        self._top: list[Product] = []
        self._full: list[Product] | None = None

    def __len__(self) -> int:
        return len(self.ids)

    def unordered(self) -> list[Product]:
        """Every match, in no particular order (for callers that only test or count them)."""
        return self._full if self._full is not None else self._unordered_fn()

    def _ordered(self) -> list[Product]:
        if self._full is None:
            self._full = self._full_fn()
        return self._full

    def _first(self, n: int) -> list[Product]:
        """The first n matches in rank order."""
        if n > len(self._top) and len(self._top) < len(self.ids):
            self._top = self._top_fn(n)
        return self._top[:n]

    def __getitem__(self, i):  # type: ignore[no-untyped-def]
        """A product by rank, or a slice of ranks; a short prefix is found without sorting every match."""
        if self._full is None:
            if isinstance(i, slice) and i.start in (None, 0) and i.step is None and i.stop is not None and i.stop >= 0:
                return self._first(i.stop)
            if isinstance(i, int) and i == 0 and len(self.ids):
                return self._first(1)[0]
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
        self._unit_tables: dict[str, tuple] = {}  # per-unit measurement tables, built when first used
        self._filter_sets: dict[tuple, set[str]] = {}  # merged filter postings, reused between searches
        self.name_pairs: set[tuple[str, str]] = set()  # adjacent words in product names: ("hair", "dryer")
        self._written: defaultdict[str, Counter] = defaultdict(Counter)  # word -> the spellings titles use
        self._text_postings: defaultdict[str, set[str]] = defaultdict(set)  # token in title/category/attrs -> pids
        self._title_postings: defaultdict[str, set[str]] = defaultdict(set)  # token in title -> pids
        self._attr_postings: defaultdict[tuple, set[str]] = defaultdict(set)  # (key, lower value) -> pids
        # (key, value tokens) -> pids: "Kurtas" and "Kurtis" read the same, so a filter on one matches both
        self._attr_token_postings: defaultdict[tuple, set[str]] = defaultdict(set)

    def _index_product(self, p: Product) -> set[str]:
        """Add one product to every lookup table; returns its words, for the document frequencies."""
        title = frozenset(tokenize(p.title))
        for raw in p.title.split():  # how the shop writes each word ("Jeans"), to quote a product word back
            word = raw.strip("()-,.")
            if word.isalpha() and len(word) >= 3:
                self._written[normalise_word(word)][word] += 1
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
        self._price_of = {p.id: p.price for p in ordered}
        self._budget_sets: dict[tuple, set[str]] = {}  # price band -> its ids, kept for repeated budgets
        self._budgets_seen: set[tuple] = set()
        self._in_stock = {p.id for p in ordered if p.in_stock is True}
        self._orders: dict[str, tuple[list[str], dict[str, int]]] = {}  # tail orders, built when first used

    def _ids_in_budget(self, low: float | None, high: float | None) -> set[str]:
        """Ids of the products priced within [low, high] (either end may be open)."""
        prices, ids = self._price_table
        start = bisect.bisect_left(prices, low) if low is not None else 0
        end = bisect.bisect_right(prices, high) if high is not None else len(prices)
        return set(ids[start:end])

    def _build_title_anchors(self) -> None:
        """File each product under its rarest title word. A title can only appear in a
        query if all its words do, so the query's own words find every candidate without walking the long postings
        of common words like "shoes"."""
        anchors: defaultdict[str, list[str]] = defaultdict(list)
        for pid, title in self.title_tokens.items():
            if title:
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
        # product words only: not a colour, material, brand or gender word ("cases" must never become the brand Casa)
        attr_words = {t for k, vals in self.attr_values.items() if k != KIND_KEY for toks in vals for t in toks}
        by_first: defaultdict[str, list[str]] = defaultdict(list)
        for word in self.type_vocab:
            if len(word) >= SHORT_TARGET_MIN and word not in attr_words:
                by_first[word[0]].append(word)
        self._vocab_by_first = by_first
        self.closest = lru_cache(maxsize=cache_size)(self._closest)
        self.closest_short = lru_cache(maxsize=cache_size)(self._closest_short)
        self.suggest = lru_cache(maxsize=cache_size)(self._suggest)
        # "did you mean" offers come from the same product words, and from the shopper words the synonym list knows
        # ("sneakers" -> shoes)
        offers = {w: w for w in self.type_vocab if len(w) >= 4 and w not in attr_words and w.isalpha()}
        for word in SYNONYMS:
            target = normalise_word(word)
            if word.isalpha() and stem(word) not in offers and target in self.type_vocab and target not in attr_words:
                offers[stem(word)] = target
        offers_by_len: defaultdict[int, list[str]] = defaultdict(list)
        for word in offers:
            offers_by_len[len(word)].append(word)
        self._offers, self._offers_by_len = offers, offers_by_len

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
            (self._in_stock.add if in_stock is True else self._in_stock.discard)(pid)
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
        self._unit_tables.clear()
        self._filter_sets.clear()
        self.products[p.id] = p
        self.ids_upper[p.id.upper()] = p.id
        self._doc_freq.update(self._index_product(p))
        title = self.title_tokens[p.id]
        if title:
            self._title_anchors.setdefault(min(title, key=lambda t: (len(self._title_postings[t]), t)), []).append(p.id)

    def remove_product(self, pid: str) -> bool:
        """Take a product out of every lookup table. (Attribute vocabularies keep values only it had; that is
        harmless, and a full rebuild tidies it.)"""
        p = self.products.pop(pid, None)
        if p is None:
            return False
        self._unit_tables.clear()
        self._filter_sets.clear()
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

    def _closest_short(self, token: str) -> str | None:
        """The one product word a short misspelling (3-7 letters after plurals are trimmed) can mean, or None.

        "jeens" -> "jeen" and "jens" -> "jen" are too short for the similarity ratio (jeen/jean scores 0.75), and a
        loose ratio on short words would turn real words into other products. So the rule is strict instead: the
        product word must start with the same letter and differ by one vowel slip (a vowel swapped for another, added
        or dropped, or two neighbours swapped), and it must be the only such word. A changed consonant is never
        mended: "band" is a real word, not a slip for "bank". Two candidates mean it is a guess, so None.
        """
        if len(token) not in SHORT_TYPO_LENGTHS or not token.isalpha() or token in self.type_vocab:
            return None  # not a slip: too short or long, not letters, or already a product word
        pool = self._vocab_by_first.get(token[0], ())
        found = [w for w in pool if abs(len(w) - len(token)) <= 1 and _vowel_slip(token, w)]
        return found[0] if len(found) == 1 else None

    def _suggest(self, token: str) -> list[str]:
        """Up to two product words a word might have meant, as the shop writes them, for a "did you mean" offer.
        It only ever asks; a suggestion is never searched for."""
        if len(token) < SUGGEST_MIN_LENGTH or not token.isalpha():
            return []
        # a real slip keeps the first letter and the length (within one); "saal" -> "Sandals" is a guess
        lengths = range(len(token) - 1, len(token) + 2)
        pool = [w for n in lengths for w in self._offers_by_len.get(n, ()) if w[0] == token[0]]
        near = difflib.get_close_matches(token, pool, n=2, cutoff=SUGGEST_CUTOFF)
        return list(dict.fromkeys(self.display_word(self._offers[w]) for w in near))

    def display_word(self, token: str) -> str:
        """A product word as the shop writes it ("jean" -> "Jeans"), for replies."""
        kind = self.attr_values.get(KIND_KEY, {}).get((token,))
        if kind:
            return str(kind)
        written = self._written.get(token)
        return written.most_common(1)[0][0] if written else token

    # -- constraint matching -------------------------------------------------------------------
    def _posting_ids(self, spec: Spec) -> set[str] | None:
        """Products carrying the spec's type words and filters, from the postings alone (None: no such constraint,
        so every product)."""
        sets = [self._text_postings.get(t, _EMPTY) for t in spec.type_tokens]
        for k, v in spec.filters.items():
            sets.append(self._filter_ids(k, v, tuple(spec.alternatives.get(k, ()))))
        if not sets:
            return None
        sets.sort(key=len)
        return sets[0] if len(sets) == 1 else sets[0].intersection(*sets[1:])

    def _filter_ids(self, key: str, value: str, alternatives: tuple[str, ...]) -> set[str]:
        """Products having this attribute value (or one of its alternatives); merged once, then reused."""
        cache_key = (key, value, alternatives)
        found = self._filter_sets.get(cache_key)
        if found is None:
            found = set()
            for v in (value, *alternatives):
                found |= self._attr_postings.get((key, v.lower()), _EMPTY)
                found |= self._attr_token_postings.get((key, tuple(tokenize(v))), _EMPTY)
                if key == GENDER_KEY and v.lower() in ("men", "women"):  # unisex items suit everyone
                    found |= self._attr_postings.get((key, "unisex"), _EMPTY)
            if len(self._filter_sets) >= 2048:
                self._filter_sets.clear()
            self._filter_sets[cache_key] = found
        return found

    def _candidate_ids(self, spec: Spec, postings_only: bool = False) -> Collection[str]:
        """Narrow the catalogue with set operations before any per-product check: postings, then size, stock,
        exclusions and budget (the last by whichever of two methods is cheaper). The result must not be changed."""
        ids = self._posting_ids(spec)
        if postings_only:
            return set(self.products) if ids is None else ids
        extra = []
        if spec.size:
            extra.append(self._attr_postings.get((SIZE_KEY, spec.size.lower()), _EMPTY))
        if spec.in_stock_only:
            extra.append(self._in_stock)
        if extra:
            sets = ([ids] if ids is not None else []) + extra
            sets.sort(key=len)
            ids = sets[0].intersection(*sets[1:]) if len(sets) > 1 else sets[0]
        if spec.exclude and ids is not None:
            banned: set[str] = set()
            for key, values in spec.exclude.items():
                for value in values:
                    banned |= self._attr_postings.get((key, value.lower()), _EMPTY)
            ids = ids - banned if banned else ids
        elif spec.exclude:  # "not cotton" alone: everything but those
            banned = set()
            for key, values in spec.exclude.items():
                for value in values:
                    banned |= self._attr_postings.get((key, value.lower()), _EMPTY)
            ids = set(self.products) - banned
        for unit, value in spec.measures:
            listed = self._unit_table(unit)[1].get(value, _EMPTY)
            ids = listed if ids is None else ids & listed
        if spec.price_min is not None or spec.price_max is not None:
            ids = self._within_budget(ids, spec.price_min, spec.price_max)
        return set(self.products) if ids is None else ids

    def _within_budget(self, ids: set[str] | None, low: float | None, high: float | None) -> set[str]:
        """The ids priced within [low, high]. A budget seen before is a stored set; otherwise, when the ids are fewer
        than the products in the price band, each id's price is checked in one C-level pass (building a set of the
        whole band costs more), else the band's set is built and kept."""
        key = (low, high)
        known = self._budget_sets.get(key)
        if known is not None:
            return known if ids is None else ids & known
        prices, _ = self._price_table
        start = bisect.bisect_left(prices, low) if low is not None else 0
        end = bisect.bisect_right(prices, high) if high is not None else len(prices)
        first_time = key not in self._budgets_seen
        self._budgets_seen.add(key)
        if ids is not None and first_time and len(ids) * 1.2 < end - start:
            return self._priced_between(ids, low, high)  # a budget not seen before: don't pay to build its set yet
        in_band = self._ids_in_budget(low, high)
        if len(self._budget_sets) >= 256:
            self._budget_sets.clear()
        self._budget_sets[key] = in_band
        return in_band if ids is None else ids & in_band

    def _priced_between(self, ids: Collection[str], low: float | None, high: float | None) -> set[str]:
        """The ids whose price is within [low, high], checked by chained C functions rather than a Python loop."""
        listed = list(ids)
        values = list(map(self._price_of.__getitem__, listed))
        mask = map(partial(operator.ge, high), values) if high is not None else None
        if low is not None:
            above = map(partial(operator.le, low), values)
            mask = above if mask is None else map(operator.and_, mask, above)
        return set(compress(listed, mask)) if mask is not None else set(listed)

    def _unit_table(self, unit: str) -> tuple[set[str], dict[float | None, set[str]]]:
        """For a unit ("w", "mah"): every product with an attribute measured in it, and those products grouped by the
        number they list. Built by one pass the first time a unit is asked about, then reused."""
        if unit not in self._unit_tables:
            everyone: set[str] = set()
            by_value: dict[float | None, set[str]] = {}
            for p in self.products.values():
                values = _unit_values(p, unit)
                if values:
                    everyone.add(p.id)
                    for number in {_numeric(v) for v in values}:
                        by_value.setdefault(number, set()).add(p.id)
            self._unit_tables[unit] = (everyone, by_value)
        return self._unit_tables[unit]

    def _usable_measures(self, spec: Spec) -> Spec:
        """The spec without measurements no product of the requested kind lists ("65W kettle" when kettles list no
        wattage: the number can't be checked, so it is dropped)."""
        pool = self._candidate_ids(spec, postings_only=True)
        usable = [m for m in spec.measures if not pool.isdisjoint(self._unit_table(m[0])[0])]
        if usable == spec.measures:
            return spec
        spec = spec.copy()
        spec.measures = usable
        return spec

    def _residual(self, spec: Spec, use_desc: bool) -> Callable[[Product], bool] | None:
        """The checks the set operations can't do: description words (None: nothing left)."""
        measures = spec.measures
        desc_tokens = spec.desc_tokens if use_desc else []
        if not (measures or desc_tokens):
            return None
        desc_index, postings = self.desc_tokens, self._text_postings

        def matches(p: Product) -> bool:
            """Whether one product meets the measurements and description words (checked on the product itself)."""
            if any(not any(_numeric(v) == value for v in _unit_values(p, unit)) for unit, value in measures):
                return False
            if desc_tokens:
                text_hit = desc_index[p.id]
                return all(t in text_hit or p.id in postings.get(t, ()) for t in desc_tokens)
            return True

        return matches

    def _matcher(self, spec: Spec, use_desc: bool) -> Callable[[Product], bool]:
        """A test for "meets every hard constraint", all of them checked on the product itself."""
        price_min, price_max = spec.price_min, spec.price_max
        banned = [(k, {b.lower() for b in values}) for k, values in spec.exclude.items()]
        size = spec.size.lower() if spec.size else None
        residual = self._residual(spec, use_desc)
        attr_lower = self.attr_lower

        def matches(p: Product) -> bool:
            """Whether one product meets the constraints."""
            if (price_max is not None and p.price > price_max) or (price_min is not None and p.price < price_min):
                return False
            attrs = attr_lower[p.id]
            if any(attrs.get(k, _EMPTY) & values for k, values in banned):
                return False
            if size and size not in attrs.get(SIZE_KEY, ()):
                return False
            if spec.in_stock_only and p.in_stock is not True:
                return False
            return residual is None or residual(p)

        return matches

    def _matches(self, p: Product, spec: Spec, use_desc: bool) -> bool:
        """Whether a product meets every hard constraint: budget, exclusions, size, stock, units, filters."""
        return self._matcher(spec, use_desc)(p)

    def match_ids(self, spec: Spec) -> tuple[Collection[str], bool]:
        """(ids of every product that meets the spec, in no particular order; description words were relaxed). No
        product objects are built and nothing is ranked."""
        if spec.measures:
            spec = self._usable_measures(spec)
        ids = self._candidate_ids(spec)  # includes the measurements, as sets
        desc_only = spec.copy()
        desc_only.measures = []  # already enforced by the sets
        hits = self._keep(ids, self._residual(desc_only, True))
        relaxed = False
        if not hits and spec.desc_tokens:
            hits = self._keep(ids, self._residual(desc_only, False))
            relaxed = bool(hits)
        return hits, relaxed

    def _keep(self, ids: Collection[str], check: Callable[[Product], bool] | None) -> Collection[str]:
        """The ids whose products pass the check (all of them when there is nothing left to check)."""
        if check is None:
            return ids
        products = self.products
        return [i for i in ids if check(products[i])]

    def matching(self, spec: Spec) -> tuple[list[Product], bool]:
        """(every product that meets the spec, in no particular order; description words were relaxed)."""
        ids, relaxed = self.match_ids(spec)
        return [self.products[i] for i in ids], relaxed

    def search(
        self, spec: Spec, query_tokens: set[str] | frozenset[str] = frozenset()
    ) -> tuple[Sequence[Product], bool]:
        """Return (ranked matches, description_tokens_were_relaxed)."""
        ids, relaxed = self.match_ids(spec)
        if not ids and spec.size and spec.size_soft:
            pool = self._candidate_ids(spec, postings_only=True)
            if pool and not any(self.attr_lower[i].get(SIZE_KEY) for i in pool):
                # "large water bottle": bottles have no sizes at all, so the size word was descriptive, not a filter
                unsized = spec.copy()
                unsized.size = None
                return self.search(unsized, query_tokens)
        return self.rank(ids, spec, query_tokens), relaxed

    # -- ranking -------------------------------------------------------------------------------
    def _sort_key(self, spec: Spec, query_tokens: set[str] | frozenset[str]) -> Callable[[Product], tuple]:
        """The full ranking key: the requested sort, else in stock first, then query-word relevance, then rating."""
        if spec.sort in _TAIL_KEYS:
            return _TAIL_KEYS[spec.sort]
        relevance = self._relevance(query_tokens)
        return lambda p: (not p.in_stock, -relevance.get(p.id, 0.0), -(p.rating or 0), p.price, p.id)

    def rank(self, ids: Collection[str], spec: Spec, query_tokens: set[str] | frozenset[str]) -> RankedHits:
        """Matches in rank order, found lazily: `[:n]` and `[0]` come from set operations over the matches, and only
        walking all of them builds the full order."""
        products = self.products
        return RankedHits(
            ids,
            lambda n: self._top_products(ids, n, spec, query_tokens),
            lambda: sorted((products[i] for i in ids), key=self._sort_key(spec, query_tokens)),  # built only if needed
            lambda: [products[i] for i in ids],
        )

    def prepare(self) -> None:
        """Build the rank orders now (about a second at 200,000 products), so no first question pays for them."""
        for mode in _TAIL_KEYS:
            self._order(mode)
        for key in self._named_keys:  # measurement tables ("mah", "w"): one pass each, so no shopper waits for it
            unit = key.rsplit("_", 1)[-1]
            if unit in UNIT_WORDS:
                self._unit_table(unit)

    def _order(self, mode: str) -> tuple[list[str], dict[str, int]]:
        """Every product id in the order of one tail key, and each id's place in it (built when first needed, and
        again after a price or product change)."""
        if mode not in self._orders:
            order = [p.id for p in sorted(self.products.values(), key=_TAIL_KEYS[mode])]
            self._orders[mode] = (order, {pid: i for i, pid in enumerate(order)})
        return self._orders[mode]

    def _first_by(self, group: Collection[str], n: int, mode: str) -> list[str]:
        """The first n ids of a group in one tail order: by walking the global order from the front when the group is
        big enough to be met quickly, else by sorting the group's places."""
        order, pos = self._order(mode)
        if len(group) <= n:
            return sorted(group, key=pos.__getitem__)
        expected = n * len(order) / len(group)  # places walked, if the group were spread evenly
        if expected * 3 < len(group):
            found: list[str] = []
            limit = int(expected * 4) + 200
            for i, pid in enumerate(order):
                if pid in group:
                    found.append(pid)
                    if len(found) == n:
                        return found
                if i > limit:
                    break  # the group sits at the far end (a price band, say): sort it instead
        places = map(pos.__getitem__, group)
        if n == 1:
            return [order[min(places)]]
        return [order[i] for i in (heapq.nsmallest(n, places) if n <= 16 else sorted(places)[:n])]

    def _top_products(
        self, ids: Collection[str], n: int, spec: Spec, query_tokens: set[str] | frozenset[str]
    ) -> list[Product]:
        """The first n matches in rank order, without ranking the rest."""
        group = ids if isinstance(ids, (set, frozenset)) else set(ids)
        if spec.sort in _TAIL_KEYS:
            picked = self._first_by(group, n, spec.sort)
        else:
            picked = []
            for part in self._stock_halves(group):
                for tier in self._relevance_tiers(part, query_tokens):
                    picked += self._first_by(tier, n - len(picked), "default")
                    if len(picked) >= n:
                        break
                if len(picked) >= n:
                    break
        return [self.products[i] for i in picked[:n]]

    def _stock_halves(self, group: set[str]) -> Iterator[set[str]]:
        """The in-stock ids, then (only if asked for) the rest: in stock always ranks first."""
        in_stock = group & self._in_stock
        yield in_stock
        if len(in_stock) < len(group):
            yield group - in_stock

    def _relevance_tiers(self, part: set[str], query_tokens: set[str] | frozenset[str]) -> list[set[str]]:
        """Split ids by how much of the query their titles share (summed word weight), best tier first. Equal sums
        are one tier, as they tie in a plain sort."""
        tiers: dict[float, set[str]] = {0.0: part}
        for t in sorted(query_tokens):  # the same order and sums as `_relevance`, so ties are bit-for-bit equal
            posting = self._title_postings.get(t)
            if not posting:
                continue
            weight = self.idf.get(t, 1.0)
            moved: dict[float, set[str]] = {}
            for rel, ids in tiers.items():
                inside = ids & posting
                for key, chunk in ((rel + weight, inside), (rel, ids - inside if len(inside) < len(ids) else None)):
                    if chunk:
                        moved[key] = (moved[key] | chunk) if key in moved else chunk
            tiers = moved
        return [tiers[rel] for rel in sorted(tiers, reverse=True)]

    def _relevance(self, query_tokens: set[str] | frozenset[str]) -> dict[str, float]:
        """Product id -> summed weight of the query words in its title; products with none are left out."""
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
        found = [
            pid
            for t in first_at
            for pid in self._title_anchors.get(t, ())
            if len(self.title_tokens[pid]) >= 2 and self.title_tokens[pid] <= words
        ]
        found = [pid for pid in found if not any(self.title_tokens[pid] < self.title_tokens[o] for o in found)]
        return sorted(found, key=lambda pid: (min(first_at[t] for t in self.title_tokens[pid]), pid))

    def exact_title_matches(self, hits: Sequence[Product], query_tokens: set[str]) -> list[Product]:
        """Products whose whole title is present in the query (the most specific mention wins)."""
        if isinstance(hits, RankedHits):  # find them by their title words, not by walking every match
            found = {pid for t in query_tokens for pid in self._title_anchors.get(t, ()) if pid in hits.ids}
            candidates = [self.products[pid] for pid in sorted(found)]
        else:
            candidates = list(hits)
        full = [p for p in candidates if self.title_tokens[p.id] and self.title_tokens[p.id] <= query_tokens]
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


def _vowel_slip(a: str, b: str) -> bool:
    """Whether two different words differ by one vowel slip: a vowel swapped for another ("jeen"/"jean"), added or
    dropped ("jen"/"jean"), or two neighbouring letters swapped. This is how short words are misspelt in practice;
    a changed consonant ("band"/"bank") is a different word."""
    if a == b:
        return False
    if len(a) == len(b):
        diff = [i for i in range(len(a)) if a[i] != b[i]]
        if len(diff) == 1:
            return a[diff[0]] in VOWELS and b[diff[0]] in VOWELS
        return len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]]
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    if len(long_) - len(short) != 1:
        return False
    i = next((k for k in range(len(short)) if short[k] != long_[k]), len(short))
    return short[i:] == long_[i + 1 :] and long_[i] in VOWELS


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


def head_nouns(index: CatalogueIndex) -> frozenset[str]:
    """Words that name what a product is: the last word of each title (\"... Running Shoes (Blue)\" -> \"shoe\"), and
    the last word of each kind sold (\"cable\" in USB Cables), since titles may end in a colour (\"... - White\")."""
    nouns = set(index.kind_nouns)
    for p in index.products.values():
        words = [w.replace("-", "") for w in p.title.split("(")[0].split()]  # "T-Shirt" -> "TShirt"
        words = [w for w in words if w.isalpha()]
        if words:
            toks = tokenize(words[-1])
            nouns.update(toks[-1:])
    return frozenset(nouns)
