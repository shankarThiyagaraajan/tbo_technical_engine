"""The closed candles the engine works from, in memory, per (stock, timeframe).

Candles are not stored in this service's database (the market feed owns them); this buffer is rebuilt from the
feed's history at warm-up and kept current by `candle.closed` events. Both paths go through `add`, so a candle seen
twice (a redelivered event, or one that is in the history and the stream) is stored once, and a correction with a
higher `revision` replaces the older one. That is what makes the buffer after a restart equal the buffer of a
process that never stopped.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime

from tbo_technical_engine.schemas.candle import Candle
from tbo_technical_engine.utils.timeframes import length

CAPACITY = {"1m": 1000, "5m": 1000, "15m": 500, "60m": 300, "1d": 400, "1w": 120}
"""Most candles kept per series: about 13 sessions of 5-minute candles, 20 of 15-minute ones, 400 daily candles."""


class CandleBuffer:
    """In-memory closed candles per (stock, timeframe), bounded by CAPACITY."""

    def __init__(self) -> None:
        self._series: dict[tuple[str, str], dict[datetime, Candle]] = defaultdict(dict)

    def add(self, candle: Candle) -> bool:
        """Store `candle`; False when an equal or newer revision of it is already here."""
        series = self._series[(candle.isin, candle.timeframe)]
        held = series.get(candle.start_at)
        if held is not None and held.revision >= candle.revision:
            return False
        series[candle.start_at] = candle
        limit = CAPACITY[candle.timeframe]
        if len(series) > limit:
            for start in sorted(series)[: len(series) - limit]:
                del series[start]
        return True

    def add_many(self, candles: Iterable[Candle]) -> int:
        """Add several candles; returns how many were new or newer revisions."""
        return sum(self.add(c) for c in candles)

    def series(self, isin: str, timeframe: str) -> list[Candle]:
        """The candles, oldest first."""
        held = self._series.get((isin, timeframe), {})
        return [held[start] for start in sorted(held)]

    def before(self, isin: str, timeframe: str, cutoff: datetime) -> list[Candle]:
        """Candles that had closed by `cutoff` (their start plus their length is not after it), oldest first.

        Strategies read the slower timeframes through this with the entry candle's start as the cutoff: whether the
        15-minute candle that closes together with a 5-minute one has arrived yet then cannot change a result, so a
        live run and a replay see the same inputs.
        """
        span = length(timeframe)
        return [c for c in self.series(isin, timeframe) if c.start_at + span <= cutoff]

    def count(self, isin: str, timeframe: str) -> int:
        """How many candles are held for the series."""
        return len(self._series.get((isin, timeframe), ()))

    def last(self, isin: str, timeframe: str) -> Candle | None:
        """The newest candle of the series, or None when it is empty."""
        held = self._series.get((isin, timeframe))
        return held[max(held)] if held else None

    def forget(self, isins: Iterable[str]) -> None:
        """Drop stocks that left the universe."""
        gone = set(isins)
        for key in [k for k in self._series if k[0] in gone]:
            del self._series[key]
