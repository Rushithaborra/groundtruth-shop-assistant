"""Summary statistics shared by the reports, so every page computes them the same way."""

from __future__ import annotations

import math


def percentile(values: list[float], q: float) -> float:
    """Nearest-rank percentile: the smallest value with at least a share q of the values at or below it."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]
