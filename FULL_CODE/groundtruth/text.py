"""Text helpers shared by the index, the query parser and the evaluator."""
from __future__ import annotations

import json
import math
import re

from .config import get_settings

STOPWORDS = set(
    """
    a an the is are was were am be do does did you your yours u ur have has had any me my mine we our us i it its
    this that these those one ones some there here of for to in on at by with from and or but not no also please
    pls plz can could would should will want need looking look find show get give tell let see about as if so than
    then too very just only what whats which who how when where why kya hai hain ho ka ki ke ko se mein mujhe mere
    meri chahiye chahie dikhao dikha batao bata de dena aur ya bhi koi kuch yeh ye woh wo wala wali wale ek par pe
    liye lie sell selling buy buying sale under below above over upto within max maximum min minimum tak andar
    ander rs inr rupee rupees rupaye ruppees stock available availability already said told mentioned earlier before still ones now instead
    rather kitne kitna kitni din abhi something anything good nice great cool decent quality product most least actually anyway okay ok yes yeah hmm except without excluding nahi
    products item items thing things recommend suggestion
    compare comparison vs versus between kab kaise kaisa kahan kidhar kyun kyu sabse sab
    aap aapke aapka aapki apke paas pas hai hain ho come comes
    """.split()
)

# Hand-written vocabulary (synonyms, brands the shop may not carry, places abroad, size words, related products),
# kept in a data file so it can be extended for the real catalogue without code changes.
_VOCAB = json.loads(get_settings().paths.vocabulary.read_text(encoding="utf-8"))

# Synonyms and common Hinglish product words; the catalogue-derived vocabulary does the rest.
SYNONYMS: dict[str, str] = _VOCAB["synonyms"]


_COD = re.compile(r"\bcod\b")


def stem(tok: str) -> str:
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
    word = word.lower()
    if word in SYNONYMS:
        return stem(SYNONYMS[word])
    if word in STOPWORDS:  # don't stem function words ("does" -> "doe")
        return word
    return stem(word)


# Well-known brands and products the shop may not carry. Used only for words the catalogue's own vocabulary does
# not contain, so a real catalogue that sells one of these brands parses it as a normal brand filter instead.
# Stored in tokenised form, because query words are stemmed before this check ("adidas" -> "adida").
EXTERNAL_DISPLAY = {stem(w): _VOCAB["brand_display"].get(w, w.capitalize())
                    for w in frozenset(_VOCAB["external_brands"])}  # stemmed -> shown name
EXTERNAL_BRANDS = frozenset(EXTERNAL_DISPLAY)

# Places outside India: a question about delivering there is answered by the "outside India" shipping sentence.
ABROAD = frozenset(normalise_word(w) for w in _VOCAB["abroad"])

# Spoken size words -> catalogue size values (only applied when the value exists in the catalogue).
SIZE_WORDS: dict[str, str] = _VOCAB["size_words"]

# Product words the catalogue may not sell, mapped to the nearest thing it does sell. Used only to suggest a cited
# alternative alongside a refusal, never to answer as if the shopper asked for the alternative.
RELATED = {normalise_word(k): v for k, v in _VOCAB["related"].items()}


def tokenize(text: str) -> list[str]:
    text = text.lower().replace("t-shirt", "tshirt").replace("t shirt", "tshirt")
    text = _COD.sub("cash on delivery", text)
    return [normalise_word(w) for w in re.findall(r"[a-z0-9]+", text)]


def content_tokens(text: str) -> list[str]:
    return [t for t in tokenize(text) if t not in STOPWORDS and not t.isdigit()]


def format_inr(amount: float) -> str:
    whole, _, frac = f"{float(amount):.2f}".partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        head = re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", head)
        whole = f"{head},{tail}"
    return f"₹{whole}" + ("" if frac == "00" else f".{frac}")


def approx_tokens(text: str) -> int:
    """Rough LLM token estimate (about four characters per token)."""
    return max(1, math.ceil(len(text) / 4))


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]
