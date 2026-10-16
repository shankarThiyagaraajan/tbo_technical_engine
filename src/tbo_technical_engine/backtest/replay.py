"""Replay a stock's candles through the LIVE strategy code (`StrategyEvaluator`, `IndicatorEngine`, `CandleBuffer`).

Nothing is copied: the candidates this produces for a recorded session are the ones the worker emitted or would have
emitted (the plan's central rule: one recorded session gives the same candidates live, in replay and in the backtester).
The order of events is the worker's: a session's 5-minute candle is added to the buffer, then evaluated; daily candles
join the buffer when their day has ended, because the live warm-up never holds today's.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta

from tbo_platform_kit.calendar import IST

from tbo_technical_engine.schemas.candle import Candle
from tbo_technical_engine.schemas.signal import SignalCandidate
from tbo_technical_engine.schemas.strategy import StrategyFile
from tbo_technical_engine.services.candle_buffer import CandleBuffer
from tbo_technical_engine.services.evaluator import StrategyEvaluator
from tbo_technical_engine.services.indicator_engine import IndicatorEngine
from tbo_technical_engine.utils.timeframes import length


def day_of(candle: Candle) -> date:
    """The IST trading day a candle belongs to."""
    return candle.start_at.astimezone(IST).date()


def replay_stock(
    config: StrategyFile,
    five: Sequence[Candle],
    fifteen: Sequence[Candle],
    daily: Sequence[Candle],
    start: date,
    end: date,
) -> list[SignalCandidate]:
    """Candidates for sessions in [start, end]. Candles before `start` only warm the indicators up."""
    buffer = CandleBuffer()
    buffer.add_many(c for c in five if day_of(c) < start)
    buffer.add_many(c for c in fifteen if day_of(c) < start)
    buffer.add_many(c for c in daily if day_of(c) < start)
    evaluator = StrategyEvaluator(IndicatorEngine(buffer), buffer, config)

    by_day: dict[date, list[Candle]] = {}
    for c in [*five, *fifteen]:
        d = day_of(c)
        if start <= d <= end:
            by_day.setdefault(d, []).append(c)
    pending_daily = sorted((c for c in daily if day_of(c) >= start), key=lambda c: c.start_at)
    next_daily = 0
    found: list[SignalCandidate] = []
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
            now = candle.start_at + timedelta(minutes=5, seconds=1)
            for candidate in evaluator.evaluate(candle, now):
                evaluator.accept(candidate)
                found.append(candidate)
    return found
