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

from dataclasses import dataclass, field

from tbo_technical_engine import indicators
from tbo_technical_engine.indicators.bars import Bars, bar_of
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


@dataclass
class _State:
    stream: SuiteStream
    last: Candle
    count: int
    first: Candle
    values: dict[str, float] = field(default_factory=dict)


class IndicatorEngine:
    def __init__(self, buffer: CandleBuffer, suites: dict[str, Suite] | None = None) -> None:
        self.buffer = buffer
        self.suites = suites or default_suites()
        self._state: dict[tuple[str, str], _State] = {}

    def values(self, isin: str, timeframe: str) -> tuple[Candle, dict[str, float]] | None:
        """The values at the buffer's newest candle of `timeframe`, or None when there are no candles / no suite."""
        suite = self.suites.get(timeframe)
        series = self.buffer.series(isin, timeframe)
        if suite is None or not series:
            return None
        key = (isin, timeframe)
        state = self._state.get(key)
        newest = series[-1]
        if state is not None and state.last == newest and len(series) == state.count and series[0] == state.first:
            return state.last, state.values
        if (
            state is not None
            and len(series) >= 2
            and series[-2] == state.last
            and newest.start_at > state.last.start_at
            and len(series) in (state.count, state.count + 1)
        ):
            state.values = state.stream.update(bar_of(newest))
            state.last = newest
            state.count = len(series)
            state.first = series[0]
            return newest, state.values
        stream, values = suite.replay(Bars.from_candles(series))
        self._state[key] = _State(stream, newest, len(series), series[0], values)
        return newest, values

    def forget(self, isins: set[str] | frozenset[str]) -> None:
        for key in [k for k in self._state if k[0] in isins]:
            del self._state[key]
