"""The evaluator of `CandleIngest` for the strategies: evaluate, store, announce.

The candidates of one candle, their rows and their `signal.candidate` events are written in one transaction (the events
through the outbox); after the commit the outbox is flushed at once, and the 30-second sweep covers a failure there.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tbo_platform_kit.events import EventPublisher
from tbo_platform_kit.logging import get_logger
from tbo_signals import Signals

from tbo_technical_engine import outbox
from tbo_technical_engine.api.shapes import candidate_dict
from tbo_technical_engine.schemas.candle import Candle
from tbo_technical_engine.services.cooldowns import CooldownStore
from tbo_technical_engine.schemas.signal import SignalCandidate
from tbo_technical_engine.services.evaluator import StrategyEvaluator
from tbo_technical_engine.store import signals as store

log = get_logger(__name__)
SIGNAL_CANDIDATE = "tbo.technical.signal.candidate.v1"
SIGNAL_SCHEMA = "https://tbo.internal/schemas/technical/signal_candidate.v1.json"
WRITE_RETRY_DELAYS = (0.25, 1.0, 2.0)
"""Seconds to wait before each retry of the candidate write (review 9.3). A crossover is defined on one candle, so a
write that is lost is never evaluated again: the candidates are kept in memory and the transaction is retried. A
PostgreSQL blip of a few seconds is covered; a longer outage still loses that boundary, and the `evaluator_failed` log
and the FAILED count in `evaluation_boundary` say so."""


class SignalRunner:
    """Evaluates a candle, stores the candidates and queues their events in one transaction, then starts the cooldowns.
    The cooldown moves only after the write. A failed write is retried here (WRITE_RETRY_DELAYS), not by redelivery:
    the candle is already in the buffer, so a redelivered event would be dropped as a duplicate."""

    def __init__(
        self,
        evaluator: StrategyEvaluator,
        sessions: async_sessionmaker[AsyncSession],
        publisher: EventPublisher,
        cooldowns: CooldownStore,
        clock: Callable[[], datetime],
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.evaluator = evaluator
        self.sessions = sessions
        self.publisher = publisher
        self.cooldowns = cooldowns
        self.clock = clock
        self.sleep = sleep
        self.signals: Signals | None = None
        """Counts stored candidates and published events (`technical.signal_candidates`, `technical.signal_events`)."""

    async def __call__(self, candle: Candle) -> None:
        """Evaluate `candle`; no-op when no strategy fired."""
        await self.cooldowns.load(candle.isin, self.evaluator.fired)
        now = self.clock()
        candidates = self.evaluator.evaluate(candle, now)
        if not candidates:
            return
        stored = await self._write_with_retry(candidates, now)
        for c in candidates:
            self.evaluator.accept(c)
            await self.cooldowns.mark(c)
            log.info("signal_candidate", strategy=c.strategy, side=c.side, isin=c.isin, strength=c.strength)
        if stored:
            if self.signals is not None:
                self.signals.emit("technical.signal_candidates", stored)
            await outbox.flush_after_commit(self.sessions, self.publisher, self.signals)

    async def _write_with_retry(self, candidates: list[SignalCandidate], now: datetime) -> int:
        """Store `candidates` and queue their events in one transaction; retry it after each WRITE_RETRY_DELAYS wait.
        Returns how many were new. Raises the last error when every attempt failed."""
        for delay in (*WRITE_RETRY_DELAYS, None):
            try:
                return await self._write(candidates, now)
            except Exception as exc:
                if delay is None:
                    raise
                log.warning("candidate_write_retry", error=type(exc).__name__, wait_s=delay)
                await self.sleep(delay)
        raise AssertionError("unreachable")  # pragma: no cover

    async def _write(self, candidates: list[SignalCandidate], now: datetime) -> int:
        stored = 0
        async with self.sessions() as session, session.begin():
            for c in candidates:
                if await store.save(session, c):
                    event = self.publisher.build(
                        SIGNAL_CANDIDATE, subject=c.isin, data=candidate_dict(c), dataschema=SIGNAL_SCHEMA, time=now
                    )
                    await outbox.add(session, event)
                    stored += 1
        return stored
