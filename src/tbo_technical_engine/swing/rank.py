"""Percentile ranks within the universe."""

from __future__ import annotations

import numpy as np


def percentile_ranks(values: dict[str, float]) -> dict[str, int]:
    """Each key's rank among all values, 0-100 (ties count half; a lone stock is 50)."""
    if not values:
        return {}
    ordered = np.array(sorted(values.values()))
    n = len(ordered)
    out: dict[str, int] = {}
    for key, value in values.items():
        below = int(np.searchsorted(ordered, value, side="left"))
        through = int(np.searchsorted(ordered, value, side="right"))
        out[key] = min(100, max(0, round(100.0 * (below + (through - below) / 2.0) / n)))
    return out


def blend(r20: dict[str, int], r60: dict[str, int], weight_20: float, weight_60: float) -> dict[str, int]:
    """The weighted mean of the two rank sets, for the stocks that have both."""
    total = weight_20 + weight_60
    return {k: min(100, max(0, round((weight_20 * r20[k] + weight_60 * r60[k]) / total))) for k in r20 if k in r60}
