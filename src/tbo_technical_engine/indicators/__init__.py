"""The indicator library: one trusted implementation of every indicator, in a batch and a streaming form.

Every strategy gets its indicators through `get`, never by importing a class, so a name and its parameters mean the
same thing everywhere (the API, the candidates and the backtester):

    ema = indicators.get("ema", period=9)
    ema.batch(bars)["ema_9"]          # NumPy array
    stream = ema.stream()             # live: stream.update(bar)["ema_9"]
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from tbo_technical_engine.indicators.base import Indicator
from tbo_technical_engine.indicators.levels import Camarilla, Cpr, OpeningRange, Pivots
from tbo_technical_engine.indicators.momentum import Rsi
from tbo_technical_engine.indicators.moving import Ema, Sma
from tbo_technical_engine.indicators.relative import RelativeStrength
from tbo_technical_engine.indicators.suite import Suite
from tbo_technical_engine.indicators.trend import Adx
from tbo_technical_engine.indicators.volatility import Atr, Supertrend
from tbo_technical_engine.indicators.volume import VolumeAverage
from tbo_technical_engine.indicators.vwap import Vwap

CATALOGUE: dict[str, Callable[..., Indicator]] = {
    "sma": Sma,
    "ema": Ema,
    "atr": Atr,
    "adx": Adx,
    "rsi": Rsi,
    "supertrend": Supertrend,
    "vwap": Vwap,
    "pivots": Pivots,
    "camarilla": Camarilla,
    "cpr": Cpr,
    "opening_range": OpeningRange,
    "volume_average": VolumeAverage,
}


def get(name: str, **params: Any) -> Indicator:
    """The indicator called `name` with `params`; ValueError for an unknown name or an unusable parameter."""
    try:
        factory = CATALOGUE[name]
    except KeyError:
        raise ValueError(f"unknown indicator {name!r}; known: {', '.join(sorted(CATALOGUE))}") from None
    return factory(**params)


__all__ = ["CATALOGUE", "RelativeStrength", "Suite", "get"]
