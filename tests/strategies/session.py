"""A synthetic multi-session history as candles, and a driver that feeds it to an evaluator the way the worker does."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from tbo_technical_engine.indicators.bars import Bars
from tbo_technical_engine.schemas.candle import Candle
from tbo_technical_engine.schemas.signal import SignalCandidate
from tbo_technical_engine.schemas.strategy import StrategyFile
from tbo_technical_engine.services.candle_buffer import CandleBuffer
from tbo_technical_engine.services.evaluator import StrategyEvaluator
from tbo_technical_engine.services.indicator_engine import IndicatorEngine
from tbo_technical_engine.services.strategy_loader import load_strategies
from tests.indicators.data import BARS_PER_SESSION, random_walk
from tests.support import ISIN_A

CONFIG = load_strategies(Path(__file__).resolve().parents[2] / "config" / "strategies.yaml")


def _candle(isin: str, tf: str, ts: int, o: float, h: float, low: float, c: float, v: float) -> Candle:
    q = Decimal("0.01")
    return Candle(
        isin=isin,
        timeframe=tf,
        start_at=datetime.fromtimestamp(ts, UTC),
        open=Decimal(str(o)).quantize(q),
        high=Decimal(str(h)).quantize(q),
        low=Decimal(str(low)).quantize(q),
        close=Decimal(str(c)).quantize(q),
        volume=int(v),
    )


def five_minute(bars: Bars, isin: str = ISIN_A) -> list[Candle]:
    return [
        _candle(isin, "5m", int(bars.ts[i]), bars.open[i], bars.high[i], bars.low[i], bars.close[i], bars.volume[i])
        for i in range(len(bars))
    ]


def aggregate(candles: Sequence[Candle], tf: str, size: int) -> list[Candle]:
    """`size` consecutive candles of one session into one."""
    out = []
    for i in range(0, len(candles) - size + 1, size):
        group = candles[i : i + size]
        out.append(
            Candle(
                isin=group[0].isin,
                timeframe=tf,
                start_at=group[0].start_at,
                open=group[0].open,
                high=max(c.high for c in group),
                low=min(c.low for c in group),
                close=group[-1].close,
                volume=sum(c.volume for c in group),
            )
        )
    return out


class History:
    def __init__(self, days: int, seed: int = 11, isin: str = ISIN_A) -> None:
        bars = random_walk(days=days, seed=seed)
        self.five = five_minute(bars, isin)
        per = BARS_PER_SESSION
        sessions = [self.five[i : i + per] for i in range(0, len(self.five), per)]
        self.sessions = sessions
        self.fifteen = [c for s in sessions for c in aggregate(s, "15m", 3)]
        self.daily = [aggregate(s, "1d", per)[0] for s in sessions]

    def before(self, day_index: int) -> list[Candle]:
        """Everything that happened before session `day_index`: 15m and daily candles and 5m of earlier sessions."""
        per15 = BARS_PER_SESSION // 3
        return (
            [c for s in self.sessions[:day_index] for c in s]
            + self.fifteen[: day_index * per15]
            + self.daily[:day_index]
        )

    def events(self, day_index: int) -> Iterable[Candle]:
        """The candles of one session in the order the feed closes them: a 15-minute candle after the 5-minute one that
        closes at the same instant."""
        per15 = BARS_PER_SESSION // 3
        fifteen = self.fifteen[day_index * per15 : (day_index + 1) * per15]
        closing = {c.start_at + timedelta(minutes=15): c for c in fifteen}
        for c5 in self.sessions[day_index]:
            yield c5
            done = closing.get(c5.start_at + timedelta(minutes=5))
            if done is not None:
                yield done


def fresh(history: Sequence[Candle], config: StrategyFile = CONFIG) -> tuple[CandleBuffer, StrategyEvaluator]:
    buffer = CandleBuffer()
    buffer.add_many(history)
    return buffer, StrategyEvaluator(IndicatorEngine(buffer), buffer, config)


def drive(buffer: CandleBuffer, evaluator: StrategyEvaluator, events: Iterable[Candle]) -> list[SignalCandidate]:
    """What the worker does per candle: store it, evaluate a 5-minute one (as if on time), keep what is emitted."""
    emitted: list[SignalCandidate] = []
    for candle in events:
        buffer.add(candle)
        if candle.timeframe != "5m":
            continue
        now = candle.start_at + timedelta(minutes=5, seconds=1)
        for c in evaluator.evaluate(candle, now):
            evaluator.accept(c)
            emitted.append(c)
    return emitted
