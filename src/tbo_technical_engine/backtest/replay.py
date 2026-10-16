"""Replay a stock's candles through the LIVE strategy code (`StrategyEvaluator`, `IndicatorEngine`, `CandleBuffer`).

Nothing is copied: the candidates this produces for a recorded session are the ones the worker emitted or would have
emitted (the plan's central rule: one recorded session gives the same candidates live, in replay and in the backtester).
The order of events is the worker's: a session's 5-minute candle is added to the buffer, then evaluated; daily candles
join the buffer when their day has ended, because the live warm-up never holds today's.

A replay has two halves. `build_tape` runs the candles through the buffer and the indicator engine and keeps, for every
candle a strategy could look at, the `Context` it would be given. `replay_tape` runs the strategies over that tape.
Parameters (windows, cooldowns, thresholds) change only the second half, so a parameter sweep builds the tape of a
stock once and replays it for every parameter set; `replay_stock` is both halves in one call.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from tbo_platform_kit.calendar import IST

from tbo_technical_engine.schemas.candle import Candle
from tbo_technical_engine.schemas.signal import SignalCandidate
from tbo_technical_engine.schemas.strategy import Params, StrategyFile
from tbo_technical_engine.services.candle_buffer import CandleBuffer
from tbo_technical_engine.services.evaluator import StrategyEvaluator
from tbo_technical_engine.services.indicator_engine import IndicatorEngine
from tbo_technical_engine.strategies.base import Context
from tbo_technical_engine.utils.timeframes import length


def day_of(candle: Candle) -> date:
    """The IST trading day a candle belongs to."""
    return candle.start_at.astimezone(IST).date()


@dataclass(frozen=True)
class Tape:
    """What every parameter set needs from one stock's replay: the 5-minute candles to evaluate, in the worker's order,
    and the context of each one a strategy could look at."""

    steps: list[tuple[Candle, datetime]]
    """(candle, the time the worker would have evaluated it), for every 5-minute candle in the range."""
    contexts: dict[datetime, Context | None]
    """By candle start. Only candles inside some strategy's entry window have one: the others are never evaluated."""


def entry_windows(config: StrategyFile) -> list[tuple[time, time]]:
    """The entry windows of every strategy in the file, active or not: a tape built from them serves any strategy and
    any parameter set that stays inside them (a window is a parameter, so a sweep that moves one builds a new tape)."""
    windows = []
    for spec in config.strategies.values():
        p = Params(spec.params)
        windows.append((p.clock("entry_from"), p.clock("entry_until")))
    return windows


def build_tape(
    config: StrategyFile,
    five: Sequence[Candle],
    fifteen: Sequence[Candle],
    daily: Sequence[Candle],
    start: date,
    end: date,
    windows: Sequence[tuple[time, time]] | None = None,
) -> Tape:
    """The tape for sessions in [start, end]. Candles before `start` only warm the indicators up.

    `windows` are the entry windows to build contexts for (default: those of `config`); a sweep passes the union over
    its parameter sets."""
    buffer = CandleBuffer()
    buffer.add_many(c for c in five if day_of(c) < start)
    buffer.add_many(c for c in fifteen if day_of(c) < start)
    buffer.add_many(c for c in daily if day_of(c) < start)
    evaluator = StrategyEvaluator(IndicatorEngine(buffer), buffer, config)
    windows = entry_windows(config) if windows is None else windows

    by_day: dict[date, list[Candle]] = {}
    for c in [*five, *fifteen]:
        d = day_of(c)
        if start <= d <= end:
            by_day.setdefault(d, []).append(c)
    pending_daily = sorted((c for c in daily if day_of(c) >= start), key=lambda c: c.start_at)
    next_daily = 0
    steps: list[tuple[Candle, datetime]] = []
    contexts: dict[datetime, Context | None] = {}
    for d in sorted(by_day):
        while next_daily < len(pending_daily) and day_of(pending_daily[next_daily]) < d:
            buffer.add(pending_daily[next_daily])
            next_daily += 1
        # Closing order; a 5-minute candle before a 15-minute one that closes at the same instant (as the feed sends).
        events = sorted(by_day[d], key=lambda c: (c.start_at + length(c.timeframe), c.timeframe != "5m"))
        for candle in events:
            buffer.add(candle)
            if candle.timeframe != "5m":
                continue
            steps.append((candle, candle.start_at + timedelta(minutes=5, seconds=1)))
            closes = (candle.start_at + length("5m")).astimezone(IST).time()
            if any(opens <= closes <= until for opens, until in windows):
                contexts[candle.start_at] = evaluator.context(candle)
    return Tape(steps, contexts)


def replay_tape(config: StrategyFile, tape: Tape) -> list[SignalCandidate]:
    """Candidates for one parameter set over a tape, in the order the worker would have emitted them."""
    empty = CandleBuffer()
    evaluator = StrategyEvaluator(IndicatorEngine(empty), empty, config, contexts=tape.contexts)
    found: list[SignalCandidate] = []
    for candle, now in tape.steps:
        for candidate in evaluator.evaluate(candle, now):
            evaluator.accept(candidate)
            found.append(candidate)
    return found


def replay_stock(
    config: StrategyFile,
    five: Sequence[Candle],
    fifteen: Sequence[Candle],
    daily: Sequence[Candle],
    start: date,
    end: date,
) -> list[SignalCandidate]:
    """Candidates for sessions in [start, end]. Candles before `start` only warm the indicators up."""
    return replay_tape(config, build_tape(config, five, fifteen, daily, start, end))
