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
from tbo_technical_engine.services.candle_buffer import CandleBuffer

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
        state = self._sync((isin, timeframe, ""), timeframe, self.buffer.series(isin, timeframe))
        return None if state is None else (state.last, state.values)

    def recent(self, isin: str, timeframe: str) -> list[tuple[Candle, dict[str, float]]]:
        """The last `TAIL` candles with the values at each of them, oldest first (empty without candles)."""
        state = self._sync((isin, timeframe, ""), timeframe, self.buffer.series(isin, timeframe))
        return [] if state is None else list(state.tail)

    def values_asof(self, isin: str, timeframe: str, cutoff: datetime) -> tuple[Candle, dict[str, float]] | None:
        """The values at the newest `timeframe` candle that had closed by `cutoff`: a slower timeframe as the entry
        candle saw it, whether or not a later candle has arrived since (live, replay and backtest then agree)."""
        series = self.buffer.before(isin, timeframe, cutoff)
        state = self._sync((isin, timeframe, "asof"), timeframe, series)
        return None if state is None else (state.last, state.values)

    def _sync(self, key: tuple[str, str, str], timeframe: str, series: list[Candle]) -> _State | None:
        suite = self.suites.get(timeframe)
        if suite is None or not series:
            return None
        state = self._state.get(key)
        newest = series[-1]
        if state is not None and state.last == newest and len(series) == state.count and series[0] == state.first:
            return state
        if (
            state is not None
            and len(series) >= 2
            and series[-2] == state.last
            and newest.start_at > state.last.start_at
            and len(series) in (state.count, state.count + 1)
        ):
            state.values = state.stream.update(bar_of(newest))
            state.tail.append((newest, dict(state.values)))
            state.last = newest
            state.count = len(series)
            state.first = series[0]
            return state
        stream = suite.stream()
        tail: deque[tuple[Candle, dict[str, float]]] = deque(maxlen=TAIL)
        values: dict[str, float] = {}
        for candle in series:
            values = stream.update(bar_of(candle))
            tail.append((candle, dict(values)))
        state = _State(stream, newest, len(series), series[0], values, tail)
        self._state[key] = state
        return state

    def forget(self, isins: set[str] | frozenset[str]) -> None:
        """Drop the state of stocks that left the universe."""
        for key in [k for k in self._state if k[0] in isins]:
            del self._state[key]
