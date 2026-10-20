"""The closed candles the engine works from, in memory, per (stock, timeframe).

Candles are not stored in this service's database (the market feed owns them); this buffer is rebuilt from the
feed's history at warm-up and kept current by `candle.closed` events. Both paths go through `add`, so a candle seen
twice (a redelivered event, or one that is in the history and the stream) is stored once, and a correction with a
higher `revision` replaces the older one. That is what makes the buffer after a restart equal the buffer of a
process that never stopped.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right, insort
from collections import defaultdict
from collections.abc import Iterable, Iterator, Sequence
from datetime import datetime
from typing import overload

from tbo_technical_engine.schemas.candle import Candle
from tbo_technical_engine.utils.timeframes import length

CAPACITY = {"1m": 1000, "5m": 1000, "15m": 500, "60m": 300, "1d": 400, "1w": 120}
"""Most candles kept per series: about 13 sessions of 5-minute candles, 20 of 15-minute ones, 400 daily candles.
At 500 stocks that is 950,000 candles, about 390 MB with the compact `Candle` (review 9.20; it was 1.5 GB as pydantic
models, over the signal worker's 1 GB limit). `tests/synthetic/test_memory.py` bounds it; `scripts/benchmark.py memory`
measures it."""


class SeriesView(Sequence[Candle]):
    """A read-only, oldest-first window on a buffer series that copies nothing: indexing is a dict lookup, so the
    engine can ask for the first, the last and the second-to-last candle of a 1,000-candle series in constant time
    (a list of the whole series, built for every candle of every stock, was the largest cost in a replay)."""

    __slots__ = ("_held", "_starts", "_stop")

    def __init__(self, held: dict[datetime, Candle], starts: list[datetime], stop: int) -> None:
        self._held = held
        self._starts = starts
        self._stop = stop

    def __len__(self) -> int:
        return self._stop

    @overload
    def __getitem__(self, index: int) -> Candle: ...
    @overload
    def __getitem__(self, index: slice) -> list[Candle]: ...
    def __getitem__(self, index: int | slice) -> Candle | list[Candle]:
        if isinstance(index, slice):
            return [self._held[s] for s in self._starts[: self._stop][index]]
        n = self._stop
        if not -n <= index < n:
            raise IndexError("series index out of range")
        return self._held[self._starts[index if index >= 0 else n + index]]

    def __iter__(self) -> Iterator[Candle]:
        return (self._held[s] for s in self._starts[: self._stop])

    def count_through(self, start: datetime) -> int:
        """How many candles start at or before `start` (a bisection; nothing is copied)."""
        return bisect_right(self._starts, start, 0, self._stop)

    def __reversed__(self) -> Iterator[Candle]:
        return (self._held[self._starts[i]] for i in range(self._stop - 1, -1, -1))


class CandleBuffer:
    """In-memory closed candles per (stock, timeframe), bounded by CAPACITY. The start times are also kept sorted, so
    reading a series never sorts."""

    def __init__(self) -> None:
        self._series: dict[tuple[str, str], dict[datetime, Candle]] = defaultdict(dict)
        self._starts: dict[tuple[str, str], list[datetime]] = defaultdict(list)

    def add(self, candle: Candle, *, replace: bool = False) -> bool:
        """Store `candle`; False when an equal or newer revision of it is already here.

        `replace` (the warm-up's history read) also lets a copy of the same revision with other prices win: the
        feed's adjusted daily candle over the raw one the stream sent, and intraday candles rescaled after a split
        (review 9.23). A newer revision is never replaced by an older one either way."""
        key = (candle.isin, candle.timeframe)
        series = self._series[key]
        held = series.get(candle.start_at)
        if held is not None and (
            held.revision > candle.revision or (held.revision == candle.revision and (not replace or held == candle))
        ):
            return False
        if held is None:
            starts = self._starts[key]
            if not starts or candle.start_at > starts[-1]:
                starts.append(candle.start_at)  # the usual case: the newest candle
            else:
                insort(starts, candle.start_at)
        series[candle.start_at] = candle
        extra = len(series) - CAPACITY[candle.timeframe]
        if extra > 0:
            starts = self._starts[key]
            for start in starts[:extra]:
                del series[start]
            del starts[:extra]
        return True

    def add_many(self, candles: Iterable[Candle], *, replace: bool = False) -> int:
        """Add several candles; returns how many were new or newer revisions (or, with `replace`, changed)."""
        return sum(self.add(c, replace=replace) for c in candles)

    def series(self, isin: str, timeframe: str) -> list[Candle]:
        """The candles, oldest first."""
        held = self._series.get((isin, timeframe), {})
        return [held[start] for start in self._starts.get((isin, timeframe), ())]

    def view(self, isin: str, timeframe: str) -> SeriesView:
        """The same candles as `series`, without copying them (read it before the next `add`)."""
        key = (isin, timeframe)
        starts = self._starts.get(key, [])
        return SeriesView(self._series.get(key, {}), starts, len(starts))

    def view_before(self, isin: str, timeframe: str, cutoff: datetime) -> SeriesView:
        """`before` as a view: the candles that had closed by `cutoff`, found by bisection."""
        key = (isin, timeframe)
        starts = self._starts.get(key, [])
        stop = bisect_right(starts, cutoff - length(timeframe))
        return SeriesView(self._series.get(key, {}), starts, stop)

    def before(self, isin: str, timeframe: str, cutoff: datetime) -> list[Candle]:
        """Candles that had closed by `cutoff` (their start plus their length is not after it), oldest first.

        Strategies read the slower timeframes through this with the entry candle's start as the cutoff: whether the
        15-minute candle that closes together with a 5-minute one has arrived yet then cannot change a result, so a
        live run and a replay see the same inputs.
        """
        return list(self.view_before(isin, timeframe, cutoff))

    def count(self, isin: str, timeframe: str) -> int:
        """How many candles are held for the series."""
        return len(self._series.get((isin, timeframe), ()))

    def last(self, isin: str, timeframe: str) -> Candle | None:
        """The newest candle of the series, or None when it is empty."""
        starts = self._starts.get((isin, timeframe))
        return self._series[(isin, timeframe)][starts[-1]] if starts else None

    def forget_before(self, isin: str, timeframe: str, cutoff: datetime) -> int:
        """Drop the series' candles that start before `cutoff`; returns how many. The warm-up uses it after a split:
        what it did not read again is on the old price scale (review 9.23)."""
        key = (isin, timeframe)
        starts = self._starts.get(key)
        if not starts:
            return 0
        n = bisect_left(starts, cutoff)
        series = self._series[key]
        for start in starts[:n]:
            del series[start]
        del starts[:n]
        return n

    def forget(self, isins: Iterable[str]) -> None:
        """Drop stocks that left the universe."""
        gone = set(isins)
        for key in [k for k in self._series if k[0] in gone]:
            del self._series[key]
            self._starts.pop(key, None)
