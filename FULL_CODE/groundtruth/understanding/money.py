"""Budgets in shopper text: "under 2k", "₹500 se kam", "between 1000 and 2000", a bare "₹500"."""
from __future__ import annotations

import re

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


def _amount(num: str, unit: str | None) -> float:
    return float(num.replace(",", "").rstrip(".")) * (1000 if unit else 1)


def extract_money(text: str):
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
