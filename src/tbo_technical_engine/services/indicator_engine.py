"""The live indicator state: one streaming suite per stock and timeframe, always equal to a replay of the buffer.

The suites hold O(1) state and advance one candle at a time. That is only right while the candles arrive in order and
none is missing or corrected, so before every read the engine compares the buffer with what it has consumed:

- the buffer ends where the engine ended: nothing to do;
- exactly one new candle follows the last consumed one: advance by it;
- anything else (a gap filled by the warm-up, a corrected candle, a restart): rebuild by replaying the buffer.

So the values are a function of the buffer, whichever way it got there, and a restarted engine and one that never
stopped agree.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

from tbo_technical_engine import indicators
from tbo_technical_engine.indicators.bars import bar_of
from tbo_technical_engine.indicators.suite import Suite, SuiteStream
from tbo_technical_engine.schemas.candle import Candle
from tbo_technical_engine.services.candle_buffer import CandleBuffer, SeriesView

BACKEND = "numpy"


def default_suites() -> dict[str, Suite]:
    """What is computed per timeframe (starting values; the strategies of TE3 decide what they read)."""
    return {
        "5m": Suite(
            [
                indicators.get("ema", period=9),
                indicators.get("ema", period=21),
                indicators.get("atr", period=14),
                indicators.get("adx", period=14),
                indicators.get("rsi", period=14),
                indicators.get("supertrend", period=10, multiplier=3.0),
                indicators.get("vwap"),
                indicators.get("pivots"),
                indicators.get("camarilla"),
                indicators.get("cpr"),
                indicators.get("opening_range", minutes=15, timeframe_minutes=5),
                indicators.get("volume_average", period=20),
            ]
        ),
        "15m": Suite(
            [
                indicators.get("ema", period=9),
                indicators.get("ema", period=21),
                indicators.get("atr", period=14),
                indicators.get("supertrend", period=10, multiplier=3.0),
            ]
        ),
        "1d": Suite([indicators.get("atr", period=14), indicators.get("ema", period=20)]),
    }


TAIL = 8
"""Rows kept per series for `recent`: the strategies look back at most this many bars (a crossover, a few bars below
VWAP, a five-bar swing low)."""


def _same(a: Candle, b: Candle) -> bool:
    """Equal candles, the common case (the very object the buffer holds) answered without comparing every field."""
    return a is b or a == b


@dataclass
class _State:
    stream: SuiteStream
    last: Candle
    count: int
    first: Candle
    values: dict[str, float] = field(default_factory=dict)
    tail: deque[tuple[Candle, dict[str, float]]] = field(default_factory=lambda: deque(maxlen=TAIL))


class IndicatorEngine:
    """Indicator values for the candles in the buffer. Each (stock, timeframe) keeps a streaming suite and advances it
    by one bar when exactly one candle was appended; any other change (a gap, a correction, an older candle) rebuilds it
    from the buffer, so a result never depends on how the state was reached."""

    def __init__(self, buffer: CandleBuffer, suites: dict[str, Suite] | None = None) -> None:
        self.buffer = buffer
        self.suites = suites or default_suites()
        self._state: dict[tuple[str, str, str], _State] = {}

    def values(self, isin: str, timeframe: str) -> tuple[Candle, dict[str, float]] | None:
        """The values at the buffer's newest candle of `timeframe`, or None when there are no candles / no suite."""
        state = self._sync((isin, timeframe, ""), timeframe, self.buffer.view(isin, timeframe))
        return None if state is None else (state.last, state.values)

    def recent(self, isin: str, timeframe: str) -> list[tuple[Candle, dict[str, float]]]:
        """The last `TAIL` candles with the values at each of them, oldest first (empty without candles)."""
        state = self._sync((isin, timeframe, ""), timeframe, self.buffer.view(isin, timeframe))
        return [] if state is None else list(state.tail)

    def values_asof(self, isin: str, timeframe: str, cutoff: datetime) -> tuple[Candle, dict[str, float]] | None:
        """The values at the newest `timeframe` candle that had closed by `cutoff`: a slower timeframe as the entry
        candle saw it, whether or not a later candle has arrived since (live, replay and backtest then agree)."""
        series = self.buffer.view_before(isin, timeframe, cutoff)
        state = self._sync((isin, timeframe, "asof"), timeframe, series)
        return None if state is None else (state.last, state.values)

    def _sync(self, key: tuple[str, str, str], timeframe: str, series: SeriesView) -> _State | None:
        suite = self.suites.get(timeframe)
        if suite is None or not series:
            return None
        state = self._state.get(key)
        newest = series[-1]
        if (
            state is not None
            and _same(state.last, newest)
            and len(series) == state.count
            and _same(series[0], state.first)
        ):
            return state
        if state is not None:
            caught_up = self._advance(state, series)
            if caught_up is not None:
                return caught_up
        stream = suite.stream()
        tail: deque[tuple[Candle, dict[str, float]]] = deque(maxlen=TAIL)
        values: dict[str, float] = {}
        for candle in series:
            values = stream.update(bar_of(candle))
            tail.append((candle, dict(values)))
        state = _State(stream, newest, len(series), series[0], values, tail)
        self._state[key] = state
        return state

    def prime(self, isin: str) -> None:
        """Build the state of every timeframe for a stock now, so the first candle after a start or a warm-up does not
        pay for it. Rebuilding from the buffer costs about 30 microseconds a candle and a stock holds 1,500 or so:
        for a 500-stock universe that is most of half a minute, which must not fall on the first candle's latency."""
        for timeframe in self.suites:
            self.values(isin, timeframe)
        newest = self.buffer.last(isin, "5m")
        if newest is not None:
            for timeframe in self.suites:
                if timeframe != "5m":
                    self.values_asof(isin, timeframe, newest.start_at)

    @staticmethod
    def _advance(state: _State, series: SeriesView) -> _State | None:
        """Feed the candles that follow the last consumed one, or None when the buffer is not "the state plus new
        candles at its end" (a correction, an older candle filled in, a different series).

        The evaluator reads indicators only inside a strategy's entry window, so several candles usually arrive between
        two reads (every evening and morning): catching up costs those candles, a rebuild costs the whole buffer.
        """
        n = len(series)
        through = series.count_through(state.last.start_at)  # candles up to and including the last consumed one
        # Nothing new, or the series holds more old candles than the state consumed (an older candle was filled in).
        if through >= n or through > state.count or through == 0 or not _same(series[through - 1], state.last):
            return None
        # `through < state.count` means the oldest candles were trimmed at capacity; otherwise none were, and the
        # first candle must still be the one the state started from.
        if through == state.count and not _same(series[0], state.first):
            return None
        for i in range(through, n):
            candle = series[i]
            state.values = state.stream.update(bar_of(candle))
            state.tail.append((candle, dict(state.values)))
            state.last = candle
        state.count = n
        state.first = series[0]
        return state

    def forget(self, isins: set[str] | frozenset[str]) -> None:
        """Drop the state of stocks that left the universe."""
        for key in [k for k in self._state if k[0] in isins]:
            del self._state[key]
