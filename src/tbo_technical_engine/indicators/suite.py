"""A set of indicators evaluated together on one stock and timeframe.

`Suite.batch(bars)` is for research; `Suite.stream()` is the live form: one `update(bar)` per closed candle returns
every value. `Suite.replay(bars)` feeds a buffer through the streaming form, which is how the live engine rebuilds its
state after a restart (so a restarted engine and one that never stopped hold identical state).
"""

from __future__ import annotations

from collections.abc import Sequence

from tbo_technical_engine.indicators.bars import F64, Bar, Bars
from tbo_technical_engine.indicators.base import Indicator, Stream


class Suite:
    """Indicators run together on one stock and timeframe; two indicators may not share an output name."""

    def __init__(self, indicators: Sequence[Indicator]) -> None:
        names = [o for ind in indicators for o in ind.outputs]
        if len(names) != len(set(names)):
            raise ValueError("two indicators produce the same output name")
        self.indicators = tuple(indicators)

    @property
    def outputs(self) -> tuple[str, ...]:
        return tuple(o for ind in self.indicators for o in ind.outputs)

    def batch(self, bars: Bars) -> dict[str, F64]:
        out: dict[str, F64] = {}
        for ind in self.indicators:
            out.update(ind.batch(bars))
        return out

    def stream(self) -> SuiteStream:
        return SuiteStream([ind.stream() for ind in self.indicators])

    def replay(self, bars: Bars) -> tuple[SuiteStream, dict[str, float]]:
        """A stream that has consumed `bars`, and the values of the last bar."""
        stream = self.stream()
        last: dict[str, float] = {}
        for i in range(len(bars)):
            last = stream.update(bars.row(i))
        return stream, last


class SuiteStream:
    """Live form of a Suite: one `update` feeds every member and merges their outputs."""

    def __init__(self, streams: list[Stream]) -> None:
        self.streams = streams

    def update(self, bar: Bar) -> dict[str, float]:
        out: dict[str, float] = {}
        for s in self.streams:
            out.update(s.update(bar))
        return out
