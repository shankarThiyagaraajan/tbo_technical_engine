"""The evaluator of `CandleIngest` for the strategies: evaluate, store, announce.

The candidates of one candle, their rows and their `signal.candidate` events are written in one transaction (the events
through the outbox); after the commit the outbox is flushed at once, and the 30-second sweep covers a failure there.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tbo_platform_kit.events import EventPublisher
from tbo_platform_kit.logging import get_logger
from tbo_signals import Signals

from tbo_technical_engine import outbox
from tbo_technical_engine.api.shapes import candidate_dict
from tbo_technical_engine.schemas.candle import Candle
from tbo_technical_engine.services.cooldowns import CooldownStore
from tbo_technical_engine.services.evaluator import StrategyEvaluator
from tbo_technical_engine.store import signals as store

log = get_logger(__name__)
SIGNAL_CANDIDATE = "tbo.technical.signal.candidate.v1"
SIGNAL_SCHEMA = "https://tbo.internal/schemas/technical/signal_candidate.v1.json"


class SignalRunner:
    """Evaluates a candle, stores the candidates and queues their events in one transaction, then starts the cooldowns.
    The cooldown moves only after the write, so a failed write can be retried by redelivery."""

    def __init__(
        self,
        evaluator: StrategyEvaluator,
        sessions: async_sessionmaker[AsyncSession],
        publisher: EventPublisher,
        cooldowns: CooldownStore,
        clock: Callable[[], datetime],
    ) -> None:
        self.evaluator = evaluator
        self.sessions = sessions
        self.publisher = publisher
        self.cooldowns = cooldowns
        self.clock = clock
        self.signals: Signals | None = None
        """Counts stored candidates and published events (`technical.signal_candidates`, `technical.signal_events`)."""

    async def __call__(self, candle: Candle) -> None:
        """Evaluate `candle`; no-op when no strategy fired."""
        await self.cooldowns.load(candle.isin, self.evaluator.fired)
        now = self.clock()
        candidates = self.evaluator.evaluate(candle, now)
        if not candidates:
            return
        stored = 0
        async with self.sessions() as session, session.begin():
            for c in candidates:
                if await store.save(session, c):
                    event = self.publisher.build(
                        SIGNAL_CANDIDATE, subject=c.isin, data=candidate_dict(c), dataschema=SIGNAL_SCHEMA, time=now
                    )
                    await outbox.add(session, event)
                    stored += 1
        for c in candidates:
            self.evaluator.accept(c)
            await self.cooldowns.mark(c)
            log.info("signal_candidate", strategy=c.strategy, side=c.side, isin=c.isin, strength=c.strength)
        if stored:
            if self.signals is not None:
                self.signals.emit("technical.signal_candidates", stored)
            await outbox.flush_after_commit(self.sessions, self.publisher, self.signals)
