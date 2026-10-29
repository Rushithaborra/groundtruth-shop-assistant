"""Text helpers shared by the index, the query parser and the evaluator."""

from __future__ import annotations

import json
import math
import re

from .config import get_settings

# Hand-written vocabulary (stopwords, synonyms, brands the shop may not carry, places abroad, size words, related
# products), kept in a data file so it can be extended for the real catalogue without code changes.
_VOCAB = json.loads(get_settings().paths.vocabulary.read_text(encoding="utf-8"))

# Function words (English and Hinglish) that carry no product meaning.
STOPWORDS = set(_VOCAB["stopwords"])

# Synonyms and common Hinglish product words; the catalogue-derived vocabulary does the rest.
SYNONYMS: dict[str, str] = _VOCAB["synonyms"]


_COD = re.compile(r"\bcod\b")


def stem(tok: str) -> str:
    """A light English stemmer: plurals and -ed endings."""
    if len(tok) > 4 and tok.endswith("ies"):
        return tok[:-3] + "y"
    if len(tok) > 4 and tok.endswith("es") and (tok[-3] in "sxz" or tok.endswith(("ches", "shes"))):
        return tok[:-2]
    if len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss"):
        return tok[:-1]
    if len(tok) > 5 and tok.endswith("ed") and not tok.endswith("eed"):
        return tok[:-2]
    return tok


def normalise_word(word: str) -> str:
    """Lower-case, map synonyms and stem; function words are left as they are."""
    word = word.lower()
    if word in SYNONYMS:
        return stem(SYNONYMS[word])
    if word in STOPWORDS:  # don't stem function words ("does" -> "doe")
        return word
    return stem(word)


# Well-known brands and products the shop may not carry. Used only for words the catalogue's own vocabulary does
# not contain, so a real catalogue that sells one of these brands parses it as a normal brand filter instead.
# Stored in tokenised form, because query words are stemmed before this check ("adidas" -> "adida").
EXTERNAL_DISPLAY = {
    stem(w): _VOCAB["brand_display"].get(w, w.capitalize()) for w in frozenset(_VOCAB["external_brands"])
}  # stemmed -> shown name
EXTERNAL_BRANDS = frozenset(EXTERNAL_DISPLAY)

# Places outside India: a question about delivering there is answered by the "outside India" shipping sentence.
ABROAD = frozenset(normalise_word(w) for w in _VOCAB["abroad"])

# Spoken size words -> catalogue size values (only applied when the value exists in the catalogue).
SIZE_WORDS: dict[str, str] = _VOCAB["size_words"]

# Product words the catalogue may not sell, mapped to the nearest thing it does sell. Used only to suggest a cited
# alternative alongside a refusal, never to answer as if the shopper asked for the alternative.
RELATED = {normalise_word(k): v for k, v in _VOCAB["related"].items()}

# Shopper words for an attribute (Hinglish or plain), mapped to a word in the attribute's name: "moti" (thick) ->
# thickness_mm, "paani" -> water_resistant. Used only when the catalogue has an attribute with that word.
ATTRIBUTE_WORDS = {normalise_word(k): normalise_word(v) for k, v in _VOCAB["attribute_words"].items()}

# Delivery days and times ("sunday", "same day", "express"): if the policy never mentions one, it is silent on it.
POLICY_TIME_WORDS = frozenset(normalise_word(w) for w in _VOCAB["policy_time_words"])


def tokenize(text: str) -> list[str]:
    """Normalised word tokens of a text."""
    text = text.lower().replace("t-shirt", "tshirt").replace("t shirt", "tshirt")
    text = _COD.sub("cash on delivery", text)
    return [normalise_word(w) for w in re.findall(r"[a-z0-9]+", text)]


def content_tokens(text: str) -> list[str]:
    """Tokens without function words or plain numbers."""
    return [t for t in tokenize(text) if t not in STOPWORDS and not t.isdigit()]


def format_inr(amount: float) -> str:
    """Rupees the Indian way: ₹1,23,456, with paise only when there are any."""
    whole, _, frac = f"{float(amount):.2f}".partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        head = re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", head)
        whole = f"{head},{tail}"
    return f"₹{whole}" + ("" if frac == "00" else f".{frac}")


_UNITS = {
    "mm": "mm",
    "cm": "cm",
    "m": "m",
    "ft": "ft",
    "in": "inches",
    "l": "L",
    "ml": "ml",
    "g": "g",
    "kg": "kg",
    "w": "W",
    "mah": "mAh",
    "c": "°C",
    "hours": "hours",
    "min": "minutes",
    "months": "months",
}


def field_label(key: str) -> str:
    """A catalogue field name as a shopper reads it: "water_resistance_rating" -> "water resistance rating",
    "thickness_mm" -> "thickness (mm)"."""
    words = key.split("_")
    if len(words) > 1 and words[-1] in _UNITS:
        return f"{' '.join(words[:-1])} ({_UNITS[words[-1]]})"
    return " ".join(words)


def value_label(value: str) -> str:
    """A catalogue value as a shopper reads it: booleans become Yes / No, everything else is unchanged."""
    return {"True": "Yes", "False": "No"}.get(value, value)


def approx_tokens(text: str) -> int:
    """Rough LLM token estimate (about four characters per token)."""
    return max(1, math.ceil(len(text) / 4))


def split_sentences(text: str) -> list[str]:
    """Sentences of a text, split after . ! or ?."""
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]
