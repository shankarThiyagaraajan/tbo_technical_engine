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

from tbo_technical_engine.config import latency as outcomes
from tbo_technical_engine.schemas.candle import Candle
from tbo_technical_engine.services.candle_buffer import CandleBuffer
from tbo_technical_engine.services.latency import LatencyTracker
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
    """Takes each `candle.closed` event into the buffer and, when the engine is allowed to act on it, hands it to the
    evaluators."""

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
        self.tracker: LatencyTracker | None = None
        """Told what became of each 5-minute candle, for the latency record (TE8)."""

    def why_not(self, candle: Candle) -> str | None:
        """None when the candle may be evaluated; otherwise the reason it may not: `inactive` (no warm-up since the
        last outage, or the feed is down), `cold` (this stock's history is not warm) or `stale` (it closed more than
        `max_age` ago: a backlog after a restart is not a signal)."""
        if candle.timeframe != ENTRY_TIMEFRAME or not self.readiness.active:
            return outcomes.INACTIVE
        if candle.isin not in self.universe or not is_warm(self.buffer, candle.isin, self.spec):
            return outcomes.COLD
        if self.clock() - (candle.start_at + length(candle.timeframe)) > self.max_age:
            return outcomes.STALE
        return None

    def may_evaluate(self, candle: Candle) -> bool:
        """True only for a 5-minute candle of a universe stock, while the feed is healthy and warm-up is done, and the
        candle closed less than `max_age` ago (a backlog after a restart is not a signal)."""
        return candle.timeframe == ENTRY_TIMEFRAME and self.why_not(candle) is None

    async def handle(self, event: dict[str, Any], session: AsyncSession | None) -> None:
        """The EventConsumer handler for `tbo.market_feed.candle.closed.v1`. Idempotent: a redelivery is not stored
        twice, so it is not evaluated twice either."""
        candle = Candle.model_validate(event["data"])
        if candle.timeframe not in STORED_TIMEFRAMES or candle.isin not in self.universe:
            return
        if not self.buffer.add(candle):
            return
        if candle.timeframe != ENTRY_TIMEFRAME:
            return
        reason = self.why_not(candle)
        if reason is not None:
            self._observe(candle, reason)
            return
        failed = False
        for evaluate in self.evaluators:
            try:
                await evaluate(candle)
            except Exception as exc:
                # One failing evaluator (an audit write, a strategy bug) must not stop the others, and must not
                # fail the event: it would be redelivered, found already stored, and skipped for everyone.
                failed = True
                log.error(
                    "evaluator_failed", evaluator=getattr(evaluate, "__qualname__", "?"), error=type(exc).__name__
                )
        self._observe(candle, outcomes.FAILED if failed else outcomes.EVALUATED)

    def _observe(self, candle: Candle, outcome: str) -> None:
        """Tell the latency record; for an evaluated candle, how long after its close the evaluators finished."""
        if self.tracker is None:
            return
        seconds = (self.clock() - (candle.start_at + length(candle.timeframe))).total_seconds()
        self.tracker.observe(candle.start_at, outcome, seconds if outcome == outcomes.EVALUATED else None)
