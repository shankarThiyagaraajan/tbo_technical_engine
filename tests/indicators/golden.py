"""Golden values for regression: every indicator's output at a few bars of the seeded synthetic year.

The fixture is synthetic (the seeded generator in data.py): there are no recorded sessions yet, since nothing has run
against the real feed. When recorded sessions exist, add them here as further fixtures; this one stays as the numeric
tripwire. Regenerate only on purpose, after a deliberate change to an indicator:

    uv run python -m tests.indicators.golden
"""

from __future__ import annotations

import json
from pathlib import Path

from tbo_technical_engine import indicators
from tests.indicators.data import random_walk

PATH = Path(__file__).resolve().parents[1] / "fixtures" / "golden_indicators.json"
AT = (300, 5000, 12345, 18749)
SET: list[tuple[str, dict[str, float]]] = [
    ("ema", {"period": 9}),
    ("ema", {"period": 21}),
    ("sma", {"period": 20}),
    ("atr", {"period": 14}),
    ("adx", {"period": 14}),
    ("rsi", {"period": 14}),
    ("supertrend", {"period": 10, "multiplier": 3.0}),
    ("vwap", {}),
    ("pivots", {}),
    ("camarilla", {}),
    ("cpr", {}),
    ("opening_range", {}),
    ("volume_average", {"period": 20}),
]


def compute() -> dict[str, list[float | None]]:
    bars = random_walk(days=250)
    out: dict[str, list[float | None]] = {}
    for name, params in SET:
        for key, series in indicators.get(name, **params).batch(bars).items():
            out[key] = [None if v != v else round(float(v), 6) for v in (series[i] for i in AT)]
    return out


if __name__ == "__main__":
    PATH.parent.mkdir(exist_ok=True)
    PATH.write_text(json.dumps({"at": list(AT), "values": compute()}, indent=1, sort_keys=True) + "\n")
    print(f"wrote {PATH}")
