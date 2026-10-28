"""Sizes in shopper text: "size 9", "size: M", "M size", "king size", or a bare word like "medium"."""

from __future__ import annotations

import re

from ..catalogue import CatalogueIndex
from ..text import SIZE_WORDS, tokenize

_SIZE = re.compile(r"\bsize(?:\s*[:\-]\s*|\s+)([a-z]{1,6}|\d{1,2})\b")  # "size 9", "size: M"; not "sizes"
_SIZE_LETTERS = {"XXS", "XS", "S", "M", "L", "XL", "XXL", "XXXL", "3XL", "4XL"}
_SIZE_AFTER = re.compile(r"\b([a-z]{1,6}|\d{1,2})[\s-]*size(?:d)?\b")
_SIZE_WORD = re.compile(r"\b(" + "|".join(sorted(SIZE_WORDS, key=len, reverse=True)) + r")\b")


def _size_value(word: str, index: CatalogueIndex) -> str | None:
    """Map a size word to a size the catalogue actually has (\"medium\" -> \"M\", \"9\" -> \"9\")."""
    for cand in (word, SIZE_WORDS.get(word, "")):
        if cand and cand.lower() in index.size_values:
            return cand
    return None


def _names_other_attribute(word: str, index: CatalogueIndex) -> bool:
    """True when the word isn't a size the catalogue stocks but is a value of another attribute ("king" bed_size).

    "medium" stays a size (it maps to M); it is also a luggage_size, but a clothing size wins.
    """
    if _size_value(word, index) is not None:
        return False
    toks = tuple(tokenize(word))
    return any(toks in values for values in index.attr_values.values())


def extract_size(low: str, index: CatalogueIndex) -> tuple[str | None, str, bool]:
    """Find \"size 9\", \"size medium\", \"M size\", \"king size\", or a bare size word like \"medium\".

    Returns (size, text with the size removed, soft) where a soft size is dropped for products that have no sizes.
    """
    for pattern in (_SIZE, _SIZE_AFTER):
        for m in pattern.finditer(low):
            word = m.group(1)
            if _names_other_attribute(word, index):
                continue  # "king size bedsheet": king is a bed_size, read by the attribute filters instead
            value = _size_value(word, index)
            if value is None and pattern is _SIZE and (word.isdigit() or word.upper() in _SIZE_LETTERS):
                value = word  # an explicit "size 50" the catalogue doesn't stock must still constrain (-> refuse)
            if value is None and word in SIZE_WORDS:
                # "queen size" when nothing comes in queen: still a constraint (-> "no queen size"); soft, so it's
                # ignored for products that have no sizes at all ("large size bottle")
                return SIZE_WORDS[word], low[: m.start()] + " " + low[m.end() :], True
            if value:  # spoken size words ("large size") stay soft: ignored for products that have no sizes
                return value, low[: m.start()] + " " + low[m.end() :], word in SIZE_WORDS
    m = _SIZE_WORD.search(low)
    if m and not _names_other_attribute(m.group(1), index) and (value := _size_value(m.group(1), index)):
        return value, low[: m.start()] + " " + low[m.end() :], True
    return None, low, False
