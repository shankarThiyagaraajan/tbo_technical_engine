"""The strategies wired to storage, the outbox and the Redis cooldown checkpoint (real PostgreSQL and Redis)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, text

from tbo_technical_engine.services.cooldowns import CooldownStore
from tbo_technical_engine.services.evaluator import StrategyEvaluator
from tbo_technical_engine.services.indicator_engine import IndicatorEngine
from tbo_technical_engine.services.signal_runner import SIGNAL_CANDIDATE, SignalRunner
from tbo_technical_engine.store.tables import outbox, signal_candidate
from tests.strategies.session import CONFIG, History
from tests.support import ISIN_A

HISTORY = History(days=20, seed=12)
DAY = 15
NOW = datetime(2026, 10, 15, 6, 0, tzinfo=UTC)


def build(app: Any, settings: Any, redis: Any, preload: bool = True) -> tuple[Any, SignalRunner, StrategyEvaluator]:
    from tbo_technical_engine.services.candle_buffer import CandleBuffer

    buffer = CandleBuffer()
    if preload:
        buffer.add_many(HISTORY.before(DAY))
    evaluator = StrategyEvaluator(IndicatorEngine(buffer), buffer, CONFIG)
    runner = SignalRunner(
        evaluator, app.state.sessions, app.state.publisher, CooldownStore(redis, settings.profile), lambda: NOW
    )
    return buffer, runner, evaluator


async def play(buffer: Any, runner: SignalRunner) -> None:
    for candle in HISTORY.events(DAY):
        buffer.add(candle)
        if candle.timeframe == "5m":
            await runner(candle)


async def count(sessions: Any, table: Any) -> int:
    async with sessions() as session:
        return int((await session.execute(select(func.count()).select_from(table))).scalar_one())


async def test_candidates_are_stored_announced_and_checkpointed(
    app: Any, settings: Any, redis: Any, sessions: Any
) -> None:
    buffer, runner, evaluator = build(app, settings, redis)
    await play(buffer, runner)

    stored = await count(sessions, signal_candidate)
    assert stored > 0
    async with sessions() as session:
        events = [
            json.loads(r.payload)
            for r in (await session.execute(select(outbox.c.payload, outbox.c.published_at))).all()
        ]
        unpublished = (await session.execute(select(func.count()).where(outbox.c.published_at.is_(None)))).scalar_one()
    assert len(events) == stored
    assert {e["type"] for e in events} == {SIGNAL_CANDIDATE}
    assert all(e["subject"] == ISIN_A and e["data"]["isin"] == ISIN_A for e in events)
    assert unpublished == 0  # flushed right after each commit, not left to the sweep
    stream = await redis.xlen(f"{settings.profile.stream_prefix}tbo.technical.signal")
    assert stream == stored

    # the cooldowns are in Redis: a new process loads them
    store = CooldownStore(redis, settings.profile)
    loaded: dict[Any, Any] = {}
    await store.load(ISIN_A, loaded)
    assert loaded == evaluator.fired and loaded


async def test_the_same_candles_again_add_nothing(app: Any, settings: Any, redis: Any, sessions: Any) -> None:
    """A redelivered session after a crash: the IDs repeat, the copies are refused, no event is queued twice."""
    buffer, runner, _ = build(app, settings, redis)
    await play(buffer, runner)
    stored, queued = await count(sessions, signal_candidate), await count(sessions, outbox)
    await redis.flushdb()  # the checkpoint is gone too: this process has no memory of firing
    buffer2, runner2, _ = build(app, settings, redis)
    await play(buffer2, runner2)
    assert await count(sessions, signal_candidate) == stored
    assert await count(sessions, outbox) == queued


async def test_a_failed_write_keeps_the_candle_retryable(app: Any, settings: Any, redis: Any, sessions: Any) -> None:
    """If the transaction fails, nothing is stored and no cooldown starts, so the next evaluation can still emit it."""
    buffer, runner, evaluator = build(app, settings, redis)
    async with sessions() as session, session.begin():
        await session.execute(text("ALTER TABLE technical.signal_candidate RENAME TO signal_candidate_x"))
    try:
        for candle in HISTORY.events(DAY):
            buffer.add(candle)
            if candle.timeframe == "5m":
                try:
                    await runner(candle)
                except Exception:
                    break
        assert evaluator.fired == {}
    finally:
        async with sessions() as session, session.begin():
            await session.execute(text("ALTER TABLE technical.signal_candidate_x RENAME TO signal_candidate"))
