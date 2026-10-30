"""Low-token miss-path resolver: a short numbered menu instead of the whole catalogue in the prompt.

The older normaliser (`normaliser.py`) pasted every allowed type and attribute value into the prompt: about 3,800
tokens on the pack's catalogue, and growing with it. This resolver asks a model only to *pick a number*:

1. Cheap shortlist, no model: character-trigram similarity finds the product types that look like the shopper's
   word (typos, spellings: "kurtha", "earbud"). When one is a strong match it is the only menu offered.
2. Otherwise two small menus: the catalogue's categories, then the types inside the chosen category. Each menu has at
   most `MENU_SIZE` options, so the prompt is a hundred tokens or so however large the catalogue is.
3. The model's reply is one number (max 4 new tokens). Anything that isn't a listed option is dropped, so it can only
   ever choose a product type the catalogue has; it can't name a brand, colour or price, and it never writes text the
   shopper sees.

The assistant hands each answer to `LearnedSynonyms` as a proposal, so once a person approves it the word is never
asked again.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from itertools import islice
from typing import TYPE_CHECKING, Callable

from ..text import approx_tokens
from .calls import ModelCallError
from .semantic import type_words

if TYPE_CHECKING:
    from ..catalogue import CatalogueIndex
    from ..understanding import Parsed

MENU_SIZE = 16  # most options in one menu
STRONG_MATCH = 0.35  # trigram similarity at or above which the closest types are offered on their own
SAMPLE = 2000  # products looked at per word when finding its main category (keeps start-up flat on huge catalogues)
SYSTEM = (
    "Pick the option that is the same kind of product as the shopper's word (a related product is not enough). "
    "Reply with the option number only, or 0 if none fits. Never explain."
)

# (system, user, max_tokens=..., json_mode=...) -> (text, input_tokens, output_tokens)
Generate = Callable[..., tuple[str, int, int]]
_NUMBER = re.compile(r"\d+")


def trigrams(word: str) -> frozenset[str]:
    """Overlapping three-letter pieces of a word, with its ends marked: "tv" -> {"^tv", "tv$"}."""
    padded = f"^{word}$"
    return frozenset(padded[i : i + 3] for i in range(max(1, len(padded) - 2)))


class TypeMenus:
    """The catalogue's product types, grouped by category and searchable by spelling. Built once from the index; its
    size follows the number of distinct types and categories, not the number of products."""

    def __init__(self, index: CatalogueIndex) -> None:
        self.types = type_words(index)
        self._grams = {t: trigrams(t) for t in self.types}
        self._by_gram: defaultdict[str, list[str]] = defaultdict(list)
        for t, grams in self._grams.items():
            for g in grams:
                self._by_gram[g].append(t)
        self.category_of: dict[str, str] = {}
        sizes: Counter = Counter()
        for t in self.types:
            pids = islice(index._text_postings.get(t, ()), SAMPLE)
            cats = Counter(index.products[p].category for p in pids)
            if cats:
                self.category_of[t] = cats.most_common(1)[0][0]
                sizes[self.category_of[t]] += 1
        self.categories = [c for c, _ in sizes.most_common(MENU_SIZE)]  # biggest categories first
        self._popularity = {t: index.product_count(t) for t in self.types}

    def closest(self, word: str, limit: int = MENU_SIZE) -> list[tuple[str, float]]:
        """The types that look most like the word, with a 0-1 similarity (trigram Jaccard)."""
        grams = trigrams(word)
        shared: Counter = Counter(t for g in grams for t in self._by_gram.get(g, ()))
        scored = [(t, n / (len(grams) + len(self._grams[t]) - n)) for t, n in shared.items()]
        scored.sort(key=lambda s: (-s[1], -self._popularity[s[0]], s[0]))
        return scored[:limit]

    def in_category(self, category: str, word: str) -> list[str]:
        """The category's types: the ones that look like the word first, then the most popular."""
        own = {t for t, c in self.category_of.items() if c == category}
        look = [t for t, _ in self.closest(word, MENU_SIZE * 4) if t in own]
        rest = sorted(own - set(look), key=lambda t: (-self._popularity[t], t))
        return (look + rest)[:MENU_SIZE]


def _menu(word: str, options: list[str]) -> str:
    """The user message: the word and its numbered options (0 is implicitly "none")."""
    return f"WORD: {word}\n" + "\n".join(f"{i}. {o}" for i, o in enumerate(options, 1))


class ShortlistNormaliser:
    """Resolves the words the rules couldn't place with at most two tiny numbered-menu questions. Drop-in for
    `LLMNormaliser`: same `apply(parsed, index) -> (input_tokens, output_tokens)`."""

    def __init__(self, generate: Generate, max_words: int = 2) -> None:
        self.generate = generate
        self.max_words = max_words
        self._menus: dict[int, TypeMenus] = {}
        self.calls = 0  # model calls made, for the cost report

    def _menus_for(self, index: CatalogueIndex) -> TypeMenus:
        """The menus for this catalogue, built the first time they are needed."""
        key = id(index)
        if key not in self._menus:
            self._menus[key] = TypeMenus(index)
        return self._menus[key]

    def _pick(self, word: str, options: list[str]) -> tuple[str | None, int, int]:
        """Ask the model to choose one option. Returns (the option or None, input tokens, output tokens)."""
        if not options:
            return None, 0, 0
        user = _menu(word, options)
        self.calls += 1
        text, n_in, n_out = self.generate(SYSTEM, user, max_tokens=4, json_mode=False)
        n_in = n_in or approx_tokens(SYSTEM + user)  # a generator that reports no usage is estimated
        match = _NUMBER.search(text or "")
        number = int(match.group()) if match else 0
        return (options[number - 1] if 1 <= number <= len(options) else None), n_in, n_out

    def _resolve_word(self, word: str, menus: TypeMenus) -> tuple[str | None, int, int]:
        """Resolve one word to a product type: a strong spelling match, else category then type."""
        near = menus.closest(word, 6)
        if near and near[0][1] >= STRONG_MATCH:
            return self._pick(word, [t for t, _ in near])
        category, n_in, n_out = self._pick(word, menus.categories)
        if category is None:
            return None, n_in, n_out
        target, i2, o2 = self._pick(word, menus.in_category(category, word))
        return target, n_in + i2, n_out + o2

    def apply(self, parsed: Parsed, index: CatalogueIndex) -> tuple[int, int]:
        """Map unknown product words onto catalogue types in place. Returns (input_tokens, output_tokens)."""
        menus = self._menus_for(index)
        total_in = total_out = 0
        words = [w for w in parsed.unknown if w not in parsed.external and not w.isdigit() and len(w) > 2]
        for word in words[: self.max_words]:
            try:
                target, n_in, n_out = self._resolve_word(word, menus)
            except ModelCallError:  # a slow or failing model: keep the rules' answer
                break
            total_in, total_out = total_in + n_in, total_out + n_out
            if target is None:
                continue
            parsed.unknown.remove(word)
            if target not in parsed.spec.type_tokens:
                parsed.spec.type_tokens.append(target)
            parsed.mapped.append((word, target))
        return total_in, total_out
