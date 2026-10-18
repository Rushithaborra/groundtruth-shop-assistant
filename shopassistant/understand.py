"""Query understanding: rule-based, so it runs offline and every decision is inspectable.

Handles short, ungrammatical, code-mixed (English/Hinglish) shopper queries.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .index import CatalogueIndex, Spec
from .text import ABROAD, EXTERNAL_BRANDS, RELATED, SIZE_WORDS, STOPWORDS, tokenize

PRICE_W = {"price", "kitna", "kitne", "kitni", "much"}
STOCK_W = {"stock", "available", "availability", "milega", "milegi", "milenge", "instock", "left"}
RATING_W = {"rating", "rated", "review", "star", "feedback"}
POLICY_W = {"return", "cash", "refund", "exchange", "replace", "replacement", "warranty", "guarantee", "shipping", "ship",
            "delivery", "deliver", "cod", "cancel", "cancellation", "wapas", "policy", "courier", "dispatch"}
CHEAP_W = {"cheapest", "cheap", "cheaper", "sasta", "sasti", "lowest", "affordable"}
EXPENSIVE_W = {"costliest", "costlier", "expensive", "priciest", "pricier", "mehnga", "mehnge", "premium"}
NEG_W = {"not", "except", "without", "excluding", "nahi", "no", "non"}
BEST_W = {"best", "top", "highest", "popular"}
GREET_W = {"hi", "hello", "hey", "hii", "namaste", "helo"}
WH_W = {"what", "whats", "which", "kya", "kaun", "kaunsa", "kaunsi", "kis"}
RESET_PHRASES = ("start over", "new search", "reset", "forget it", "clear all", "never mind", "nevermind")

_IGNORE = (PRICE_W | STOCK_W | RATING_W | POLICY_W | CHEAP_W | EXPENSIVE_W | BEST_W | GREET_W | WH_W | STOPWORDS)

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
_SIZE = re.compile(r"\bsize\s*[:\-]?\s*([a-z]{1,6}|\d{1,2})\b")
_SIZE_AFTER = re.compile(r"\b([a-z]{1,6}|\d{1,2})[\s-]*size(?:d)?\b")
_SIZE_WORD = re.compile(r"\b(" + "|".join(sorted(SIZE_WORDS, key=len, reverse=True)) + r")\b")


@dataclass
class Parsed:
    raw: str
    intents: set[str] = field(default_factory=set)
    product_id: str | None = None
    spec: Spec = field(default_factory=Spec)
    unknown: list[str] = field(default_factory=list)
    detail_keys: list[str] = field(default_factory=list)
    mentioned: list[str] = field(default_factory=list)  # products whose full title is in the query
    keywords: list[str] = field(default_factory=list)   # tokens left after stopword removal (policy matching)
    external: list[str] = field(default_factory=list)   # well-known brands/products the catalogue doesn't contain
    alternatives: list[str] = field(default_factory=list)  # catalogue type words related to an unknown product word
    mapped: list[tuple[str, str]] = field(default_factory=list)  # (shopper word, catalogue word) from the fallback
    refined: bool = False  # set by the assistant when this turn was merged into the previous search
    all_tokens: set[str] = field(default_factory=set)   # every token (exact title matching)

    @property
    def has_product_ref(self) -> bool:
        s = self.spec
        return bool(self.product_id or self.mentioned or s.type_tokens or s.desc_tokens or s.filters or s.size)

    @property
    def has_constraint(self) -> bool:
        s = self.spec
        return self.has_product_ref or s.price_min is not None or s.price_max is not None


def _amount(num: str, unit: str | None) -> float:
    return float(num.replace(",", "").rstrip(".")) * (1000 if unit else 1)


def _extract_money(text: str):
    """Pull budget expressions out of the text; returns (remaining_text, price_min, price_max)."""
    if not any(c.isdigit() for c in text):
        return text, None, None
    found: dict[str, float] = {}

    def between(m):
        a, b = _amount(m[1], m[2]), _amount(m[3], m[4])
        found["lo"], found["hi"] = min(a, b), max(a, b)
        return " "

    def strip(pattern, key):
        def repl(m):
            found[key] = _amount(m[1], m[2])
            return " "
        return pattern.sub(repl, text)

    text = _BETWEEN.sub(between, text)
    for patterns, key in ((_MAX, "hi"), (_MIN, "lo")):
        for pattern in patterns:
            text = strip(pattern, key)
    explicit_max = found.get("hi")
    text = strip(_BARE, "hi")  # a bare "₹500" is a budget unless a limit was already given
    if explicit_max is not None:
        found["hi"] = explicit_max
    return text, found.get("lo"), found.get("hi")


def _repair_tokens(tokens: list[str], index: CatalogueIndex) -> list[str]:
    """Fix word-boundary mistakes: "smart watch" -> smartwatch, "powerbank" -> power bank."""
    vocab = index.vocab
    out, i = [], 0
    while i < len(tokens):
        tok = tokens[i]
        if i + 1 < len(tokens) and tokens[i] + tokens[i + 1] in vocab and not (tok in vocab and tokens[i + 1] in vocab):
            out.append(tok + tokens[i + 1])
            i += 2
            continue
        if tok not in vocab and len(tok) >= 6 and not tok.isdigit():
            for k in range(3, len(tok) - 2):
                if tok[:k] in vocab and tok[k:] in vocab:
                    out += [tok[:k], tok[k:]]
                    break
            else:
                out.append(tok)
        else:
            out.append(tok)
        i += 1
    return out


def _size_value(word: str, index: CatalogueIndex) -> str | None:
    """Map a size word to a size the catalogue actually has (\"medium\" -> \"M\", \"9\" -> \"9\")."""
    for cand in (word, SIZE_WORDS.get(word, "")):
        if cand and cand.lower() in index.size_values:
            return cand
    return None


def _extract_size(low: str, index: CatalogueIndex) -> tuple[str | None, str, bool]:
    """Find \"size 9\", \"size medium\", \"M size\", \"king size\", or a bare size word like \"medium\"."""
    for pattern in (_SIZE, _SIZE_AFTER):
        for m in pattern.finditer(low):
            word = m.group(1)
            value = _size_value(word, index)
            if value is None and pattern is _SIZE and (word.isdigit() or len(word) <= 3):
                value = word  # an explicit "size 50" the catalogue doesn't stock must still constrain (-> refuse)
            if value:
                return value, low[:m.start()] + " " + low[m.end():], False
    m = _SIZE_WORD.search(low)
    if m and (value := _size_value(m.group(1), index)):
        return value, low[:m.start()] + " " + low[m.end():], True
    return None, low, False


def understand(query: str, index: CatalogueIndex) -> Parsed:
    parsed = Parsed(raw=query)
    low = " ".join(query.lower().replace("t-shirt", "tshirt").split())

    if any(phrase in low for phrase in RESET_PHRASES):
        parsed.intents.add("reset")
        return parsed

    # explicit product ids ("P0042"); two or more ("compare P0042 and P0057") are a comparison
    ids = []
    for word in re.findall(r"[a-z0-9_-]+", low):
        pid = index.ids_upper.get(word.upper())
        if pid and pid not in ids:
            ids.append(pid)
            low = re.sub(rf"\b{re.escape(word)}\b", " ", low, count=1)
    if len(ids) == 1:
        parsed.product_id = ids[0]

    parsed.spec.size, low, parsed.spec.size_soft = _extract_size(low, index)

    low, parsed.spec.price_min, parsed.spec.price_max = _extract_money(low)

    tokens = _repair_tokens(tokenize(low), index)
    parsed.all_tokens = set(tokens)
    token_set = set(tokens)
    parsed.mentioned = ids + [m for m in index.mentioned(token_set) if m not in ids] if len(ids) > 1 \
        else index.mentioned(token_set)
    consumed = [False] * len(tokens)

    # attribute-value filters (colour, material, brand, ...); longest values first
    for n, toks, key, val in index.attr_candidates:
        for i in range(len(tokens) - n + 1):
            if tuple(tokens[i:i + n]) == toks and not any(consumed[i:i + n]):
                if i > 0 and tokens[i - 1] in NEG_W:
                    parsed.spec.exclude.setdefault(key, []).append(val)
                elif key not in parsed.spec.filters:
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
        if ktok in token_set and key not in parsed.spec.filters and key not in parsed.spec.exclude and wh and ktok not in PRICE_W:
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

    if "least" in token_set and token_set & EXPENSIVE_W:
        parsed.spec.sort = "price_asc"

    # remaining content tokens -> type / description / unknown
    for i, tok in enumerate(tokens):
        if tok not in STOPWORDS and not tok.isdigit():
            parsed.keywords.append(tok)
        if consumed[i] or tok in _IGNORE or tok in index.attr_keys or tok.isdigit() or len(tok) < 2:
            continue
        if tok not in index.type_vocab and tok not in index.desc_vocab and len(tok) >= 5:
            tok = index.closest(tok) or tok  # typos: "runing"
        if tok in index.type_vocab:
            if tok not in parsed.spec.type_tokens:
                parsed.spec.type_tokens.append(tok)
        elif tok in index.desc_vocab:
            if tok not in parsed.spec.desc_tokens:
                parsed.spec.desc_tokens.append(tok)
        else:
            parsed.unknown.append(tok)
            if tok in EXTERNAL_BRANDS:
                parsed.external.append(tok)
            elif tok in RELATED and RELATED[tok] in index.type_vocab:
                parsed.alternatives.append(RELATED[tok])

    if token_set & ABROAD:  # "delivery to dubai" is the "outside India" shipping question
        parsed.keywords += ["outside", "india"]

    explicit_price = bool(token_set & {"price", "much"})
    product_ref = parsed.has_product_ref
    asks_fact = (explicit_price or "rating" in parsed.intents
                 or (bool(parsed.intents & {"stock", "detail"}) and product_ref))
    if token_set & POLICY_W:
        parsed.intents.add("policy_q" if asks_fact and product_ref else "policy")
    if token_set & GREET_W and not (parsed.has_constraint or parsed.intents):
        parsed.intents.add("greet")
    if not parsed.intents:
        parsed.intents.add("search")
    return parsed
