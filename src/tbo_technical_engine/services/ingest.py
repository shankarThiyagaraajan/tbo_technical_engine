"""From a `candle.closed` event to the strategies: store the candle, then evaluate it if the engine may.

The rules of the plan are all here and nowhere else: strategies see a candle only when it is a completed 5-minute
candle, the feed is healthy, the warm-up is fresh, the stock is in the universe and warm, and the candle is recent
(a backlog read after a restart is stored but never evaluated: its entries would be minutes or hours late).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from tbo_platform_kit.logging import get_logger

from tbo_technical_engine.schemas.candle import Candle
from tbo_technical_engine.services.candle_buffer import CandleBuffer
from tbo_technical_engine.services.readiness import Readiness
from tbo_technical_engine.services.universe import UniverseHolder
from tbo_technical_engine.services.warmup import WarmupSpec, is_warm
from tbo_technical_engine.utils.timeframes import length

log = get_logger(__name__)

Evaluator = Callable[[Candle], Awaitable[None]]
ENTRY_TIMEFRAME = "5m"
STORED_TIMEFRAMES = frozenset({"5m", "15m"})
"""Timeframes the engine keeps from the stream; 1m and 60m candles are published for other consumers."""


class CandleIngest:
    def __init__(
        self,
        buffer: CandleBuffer,
        readiness: Readiness,
        universe: UniverseHolder,
        spec: WarmupSpec,
        clock: Callable[[], datetime],
        *,
        max_age: timedelta = timedelta(seconds=120),
    ) -> None:
        self.buffer = buffer
        self.readiness = readiness
        self.universe = universe
        self.spec = spec
        self.clock = clock
        self.max_age = max_age
        self.evaluators: list[Evaluator] = []
        """Called, in order, with each candle that may be evaluated (the strategies, from TE3)."""

    def may_evaluate(self, candle: Candle) -> bool:
        if candle.timeframe != ENTRY_TIMEFRAME or not self.readiness.active:
            return False
        if candle.isin not in self.universe or not is_warm(self.buffer, candle.isin, self.spec):
            return False
        return self.clock() - (candle.start_at + length(candle.timeframe)) <= self.max_age

    async def handle(self, event: dict[str, Any], session: AsyncSession | None) -> None:
        """The EventConsumer handler for `tbo.market_feed.candle.closed.v1`. Idempotent: a redelivery is not stored
        twice, so it is not evaluated twice either."""
        candle = Candle.model_validate(event["data"])
        if candle.timeframe not in STORED_TIMEFRAMES or candle.isin not in self.universe:
            return
        if not self.buffer.add(candle):
            return
        if self.may_evaluate(candle):
            for evaluate in self.evaluators:
                try:
                    await evaluate(candle)
                except Exception as exc:
                    # One failing evaluator (an audit write, a strategy bug) must not stop the others, and must not
                    # fail the event: it would be redelivered, found already stored, and skipped for everyone.
                    log.error(
                        "evaluator_failed", evaluator=getattr(evaluate, "__qualname__", "?"), error=type(exc).__name__
                    )
