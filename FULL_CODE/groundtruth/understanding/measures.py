"""Measurements in shopper text: "65W charger", "6 mm yoga mat", "5.5 litre air fryer", "500 piece puzzle".

Each becomes (unit suffix, value), checked against attributes named with that unit (wattage_w, thickness_mm,
capacity_l, pieces). Read before budgets, so "65W" is a wattage and never a price.
"""

from __future__ import annotations

import re

# shopper spellings -> the unit suffix used in attribute names
_UNITS = {
    "w": "w", "watt": "w", "watts": "w",
    "mm": "mm", "cm": "cm",
    "l": "l", "ltr": "l", "ltrs": "l", "litre": "l", "litres": "l", "liter": "l", "liters": "l",
    "ml": "ml", "mah": "mah", "kg": "kg", "g": "g", "gm": "g", "gram": "g", "grams": "g",
    "inch": "in", "inches": "in", "m": "m", "metre": "m", "metres": "m", "meter": "m", "meters": "m",
    "ft": "ft", "feet": "ft", "pcs": "pieces", "piece": "pieces", "pieces": "pieces",
}  # fmt: skip
_MEASURE = re.compile(
    r"\b(\d+(?:\.\d+)?)\s*(" + "|".join(sorted(_UNITS, key=len, reverse=True)) + r")\b", re.IGNORECASE
)


def extract_measures(text: str) -> tuple[str, list[tuple[str, float]]]:
    """Pull measurements out of the text; returns (remaining text, [(unit suffix, value), ...])."""
    found: list[tuple[str, float]] = []

    def take(m: re.Match) -> str:
        found.append((_UNITS[m.group(2).lower()], float(m.group(1))))
        return " "

    return _MEASURE.sub(take, text), found
