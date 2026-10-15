"""Text helpers shared by the index, the query parser and the evaluator."""
from __future__ import annotations

import math
import re

STOPWORDS = set(
    """
    a an the is are was were am be do does did you your yours u ur have has had any me my mine we our us i it its
    this that these those one ones some there here of for to in on at by with from and or but not no also please
    pls plz can could would should will want need looking look find show get give tell let see about as if so than
    then too very just only what whats which who how when where why kya hai hain ho ka ki ke ko se mein mujhe mere
    meri chahiye chahie dikhao dikha batao bata de dena aur ya bhi koi kuch yeh ye woh wo wala wali wale ek par pe
    liye lie sell selling buy buying sale under below above over upto within max maximum min minimum tak andar
    ander rs inr rupee rupees rupaye ruppees stock available availability already said told mentioned earlier before still ones now instead
    rather kitne kitna kitni din abhi something anything good nice great cool decent quality product
    products item items thing things recommend suggestion
    """.split()
)

# Hand-written normalisations for synonyms and common Hinglish product words. The catalogue-derived
# vocabulary does the rest; extend this table when the real catalogue uses other words.
SYNONYMS = {
    "sneakers": "shoe", "sneaker": "shoe", "joota": "shoe", "joote": "shoe", "jute": "shoe", "juta": "shoe",
    "tee": "tshirt", "tees": "tshirt", "tshirts": "tshirt",
    "colour": "color", "colours": "color", "colors": "color",
    "cost": "price", "costs": "price", "keemat": "price", "kimat": "price",
    "daam": "price", "din": "day", "prices": "price", "pricing": "price",
    "suti": "cotton", "sooti": "cotton",
    "run": "running", "runner": "running", "runners": "running",
    "lal": "red", "neela": "blue", "nila": "blue", "kala": "black", "safed": "white", "hara": "green",
    "peela": "yellow",
}


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
    return stem(SYNONYMS.get(word, word))


def tokenize(text: str) -> list[str]:
    text = text.lower().replace("t-shirt", "tshirt").replace("t shirt", "tshirt")
    text = re.sub(r"\bcod\b", "cash on delivery", text)
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
