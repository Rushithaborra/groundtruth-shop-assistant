"""Query understanding: rule-based, so it runs offline and every decision is inspectable.

Handles short, ungrammatical, code-mixed (English/Hinglish) shopper queries.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

from .index import CatalogueIndex, Spec
from .text import STOPWORDS, tokenize

PRICE_W = {"price", "kitna", "kitne", "kitni", "much"}
STOCK_W = {"stock", "available", "availability", "milega", "milegi", "milenge", "instock", "left"}
RATING_W = {"rating", "rated", "review", "star", "feedback"}
POLICY_W = {"return", "cash", "refund", "exchange", "replace", "replacement", "warranty", "guarantee", "shipping", "ship",
            "delivery", "deliver", "cod", "cancel", "cancellation", "wapas", "policy", "courier", "dispatch"}
CHEAP_W = {"cheapest", "cheap", "sasta", "sasti", "lowest", "affordable"}
EXPENSIVE_W = {"costliest", "expensive", "priciest", "mehnga", "mehnge", "premium"}
BEST_W = {"best", "top", "highest", "popular"}
GREET_W = {"hi", "hello", "hey", "hii", "namaste", "helo"}
WH_W = {"what", "whats", "which", "kya", "kaun", "kaunsa", "kaunsi", "kis"}
RESET_PHRASES = ("start over", "new search", "reset", "forget it", "clear all", "never mind", "nevermind")

_CUR = r"(?:₹|rs\.?|inr|rupees?|rupaye)?"
_NUM = r"(\d[\d,]*(?:\.\d+)?)\s*(k|thousand|hazar|hajar)?(?![a-z])"
_AMT = rf"{_CUR}\s*{_NUM}"
_BETWEEN = re.compile(rf"(?:between|from)\s*{_AMT}\s*(?:and|to|-|se)\s*{_AMT}")
_MAX = [
    re.compile(rf"(?:under|below|less than|lesser than|upto|up to|within|max(?:imum)?|not more than|at most|"
               rf"budget(?: of| is)?|<=?)\s*{_AMT}"),
    re.compile(rf"{_AMT}\s*(?:tak|ke andar|ke ander|se kam|ke niche|or less|or below|and below|max)\b"),
]
_MIN = [
    re.compile(rf"(?:above|over|more than|greater than|at least|min(?:imum)?|>=?)\s*{_AMT}"),
    re.compile(rf"{_AMT}\s*(?:se upar|se zyada|se jyada|or more|or above|and above|plus)\b"),
]
_BARE = re.compile(rf"(?:₹|rs\.?|inr)\s*{_NUM}")
_SIZE = re.compile(r"\bsize\s*[:\-]?\s*([a-z]{1,3}|\d{1,2})\b")


@dataclass
class Parsed:
    raw: str
    intents: set[str] = field(default_factory=set)
    product_id: str | None = None
    spec: Spec = field(default_factory=Spec)
    unknown: list[str] = field(default_factory=list)
    detail_keys: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)   # tokens left after stopword removal (policy matching)
    all_tokens: set[str] = field(default_factory=set)   # every token (exact title matching)

    @property
    def has_product_ref(self) -> bool:
        s = self.spec
        return bool(self.product_id or s.type_tokens or s.desc_tokens or s.filters or s.size)

    @property
    def has_constraint(self) -> bool:
        s = self.spec
        return self.has_product_ref or s.price_min is not None or s.price_max is not None


def _amount(num: str, unit: str | None) -> float:
    value = float(num.rstrip(",").replace(",", ""))
    return value * 1000 if unit else value


def _extract_money(text: str):
    """Pull budget expressions out of the text; returns (remaining_text, price_min, price_max)."""
    lo = hi = None

    def sub(pattern, text, handler):
        def repl(m):
            handler(m)
            return " "
        return pattern.sub(repl, text)

    def set_between(m):
        nonlocal lo, hi
        lo, hi = _amount(m.group(1), m.group(2)), _amount(m.group(3), m.group(4))
        if lo > hi:
            lo, hi = hi, lo

    def set_max(m):
        nonlocal hi
        hi = _amount(m.group(1), m.group(2))

    def set_min(m):
        nonlocal lo
        lo = _amount(m.group(1), m.group(2))

    text = sub(_BETWEEN, text, set_between)
    for pat in _MAX:
        text = sub(pat, text, set_max)
    for pat in _MIN:
        text = sub(pat, text, set_min)
    text = sub(_BARE, text, set_max if hi is None else (lambda m: None))
    return text, lo, hi


def understand(query: str, index: CatalogueIndex) -> Parsed:
    parsed = Parsed(raw=query)
    low = " ".join(query.lower().replace("t-shirt", "tshirt").split())

    if any(phrase in low for phrase in RESET_PHRASES):
        parsed.intents.add("reset")
        return parsed

    # explicit product id (e.g. "P0042")
    for word in re.findall(r"[a-z0-9_-]+", low):
        if word.upper() in index.ids_upper:
            parsed.product_id = index.ids_upper[word.upper()]
            low = re.sub(rf"\b{re.escape(word)}\b", " ", low, count=1)
            break

    size = _SIZE.search(low)
    if size:
        parsed.spec.size = size.group(1)
        low = _SIZE.sub(" ", low)

    low, parsed.spec.price_min, parsed.spec.price_max = _extract_money(low)

    tokens = tokenize(low)
    parsed.all_tokens = set(tokens)
    token_set = set(tokens)
    consumed = [False] * len(tokens)

    # attribute-value filters (colour, material, brand, ...); longest values first
    candidates = [(len(toks), toks, key, val) for key, vals in index.attr_values.items() for toks, val in vals.items()]
    for _, toks, key, val in sorted(candidates, key=lambda c: -c[0]):
        n = len(toks)
        for i in range(len(tokens) - n + 1):
            if tuple(tokens[i:i + n]) == toks and not any(consumed[i:i + n]):
                if key not in parsed.spec.filters:
                    parsed.spec.filters[key] = val
                for j in range(i, i + n):
                    consumed[j] = True
                break

    # intents
    if token_set & PRICE_W:
        parsed.intents.add("price")
    if token_set & STOCK_W:
        parsed.intents.add("stock")
    if token_set & RATING_W:
        parsed.intents.add("rating")
    wh = bool(token_set & WH_W) or "?" in query
    for ktok, key in index.attr_keys.items():
        if ktok in token_set and key not in parsed.spec.filters and wh and ktok not in PRICE_W:
            parsed.detail_keys.append(key)
    if parsed.detail_keys:
        parsed.intents.add("detail")

    if token_set & CHEAP_W:
        parsed.spec.sort = "price_asc"
    elif token_set & EXPENSIVE_W:
        parsed.spec.sort = "price_desc"
    elif token_set & BEST_W:
        parsed.spec.sort = "price_desc" if ("price" in token_set and "highest" in token_set) else "rating_desc"
        if "price" in token_set and "highest" in token_set:
            parsed.intents.discard("rating")

    # remaining content tokens -> type / description / unknown
    ignore = (PRICE_W | STOCK_W | RATING_W | POLICY_W | CHEAP_W | EXPENSIVE_W | BEST_W | GREET_W | WH_W
              | set(index.attr_keys) | STOPWORDS)
    for i, tok in enumerate(tokens):
        if tok not in STOPWORDS and not tok.isdigit():
            parsed.keywords.append(tok)
        if consumed[i] or tok in ignore or tok.isdigit() or len(tok) < 2:
            continue
        if tok not in index.type_vocab and tok not in index.desc_vocab and len(tok) >= 5:
            close = difflib.get_close_matches(tok, index.type_vocab, n=1, cutoff=0.85)  # typos: "runing"
            tok = close[0] if close else tok
        if tok in index.type_vocab:
            if tok not in parsed.spec.type_tokens:
                parsed.spec.type_tokens.append(tok)
        elif tok in index.desc_vocab:
            if tok not in parsed.spec.desc_tokens:
                parsed.spec.desc_tokens.append(tok)
        else:
            parsed.unknown.append(tok)

    explicit_price = bool(token_set & {"price", "much"})
    if (token_set & POLICY_W and not explicit_price and "rating" not in parsed.intents
            and not ("stock" in parsed.intents and parsed.has_product_ref)):
        parsed.intents.add("policy")
    if token_set & GREET_W and not (parsed.has_constraint or parsed.intents):
        parsed.intents.add("greet")
    if not parsed.intents:
        parsed.intents.add("search")
    return parsed
