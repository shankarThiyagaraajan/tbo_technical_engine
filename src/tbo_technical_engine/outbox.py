"""The transactional outbox: an event is written in the same transaction as the change it announces, then
published to Redis after the commit. A crash between the two leaves the row unpublished, and the sweep
(tasks.flush_outbox, every 30 s) publishes it later, so no event is lost. Delivery is at least once; consumers
are idempotent by event id.

One row that Redis keeps refusing (a payload too large for the stream, a stream the component's ACL user may not
write after a rename) must not stop every later event: each row is published on its own, a failure is recorded on
the row (`attempts`, `last_error`) and the others go on; after MAX_ATTEMPTS the row is left behind (`dead_at`) and
logged at ERROR, for a person to look at (it stays in the table; nothing is deleted).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tbo_contracts import validate_event
from tbo_platform_kit.events import EventPublisher, stream_of
from tbo_platform_kit.logging import get_logger
from tbo_signals import Signals

from tbo_technical_engine.store.tables import outbox

log = get_logger(__name__)
BATCH = 100
MAX_ATTEMPTS = 10
"""Publish attempts before a row is left behind: ten sweeps, five minutes, is long enough for a Redis restart and
short enough that a poison row is noticed the same morning."""


async def add(session: AsyncSession, event: dict[str, Any]) -> None:
    """Queue `event` inside the caller's transaction. It is validated now, so a bad event fails the change."""
    validate_event(event)
    await session.execute(
        outbox.insert().values(
            event_id=event["id"],
            stream=stream_of(event["type"]),
            payload=json.dumps(event, separators=(",", ":"), ensure_ascii=False),
            created_at=datetime.now(UTC),
        )
    )


async def flush(
    sessions: async_sessionmaker[AsyncSession], publisher: EventPublisher, signals: Signals | None = None
) -> int:
    """Publish unpublished events, oldest first; returns how many. Safe to run from several processes.

    A row whose publish fails is marked (attempts, last_error) and skipped; the batch goes on. Rows at MAX_ATTEMPTS
    are left behind with `dead_at` set and are not retried by this sweep (clear `dead_at` and `attempts` to retry).
    Each published row counts as `technical.signal_events` when `signals` is given.
    """
    published = 0
    while True:
        async with sessions() as session, session.begin():
            rows = (
                await session.execute(
                    select(outbox.c.event_id, outbox.c.stream, outbox.c.payload, outbox.c.attempts)
                    .where(outbox.c.published_at.is_(None), outbox.c.dead_at.is_(None))
                    .order_by(outbox.c.created_at)
                    .limit(BATCH)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            for row in rows:
                try:
                    await publisher.publish(row.stream, json.loads(row.payload))
                except Exception as exc:  # whatever Redis or the payload did, the next row still goes
                    attempts = (row.attempts or 0) + 1
                    dead = datetime.now(UTC) if attempts >= MAX_ATTEMPTS else None
                    await session.execute(
                        update(outbox)
                        .where(outbox.c.event_id == row.event_id)
                        .values(attempts=attempts, last_error=f"{type(exc).__name__}: {exc}"[:500], dead_at=dead)
                    )
                    error = type(exc).__name__
                    if dead:
                        log.error("outbox_row_left_behind", event_id=row.event_id, attempts=attempts, error=error)
                    else:
                        log.warning("outbox_publish_failed", event_id=row.event_id, attempt=attempts, error=error)
                    continue
                await session.execute(
                    update(outbox).where(outbox.c.event_id == row.event_id).values(published_at=datetime.now(UTC))
                )
                published += 1
                if signals is not None:
                    signals.emit("technical.signal_events", 1)
        if len(rows) < BATCH:
            if published:
                log.info("outbox_flushed", published=published)
            return published


async def waiting(sessions: async_sessionmaker[AsyncSession]) -> int:
    """Rows still to publish (not counting the ones left behind): the board's "outbox waiting"."""
    async with sessions() as session:
        count = await session.scalar(
            select(func.count()).select_from(outbox).where(outbox.c.published_at.is_(None), outbox.c.dead_at.is_(None))
        )
    return int(count or 0)


async def flush_after_commit(
    sessions: async_sessionmaker[AsyncSession], publisher: EventPublisher, signals: Signals | None = None
) -> None:
    """The request-path flush, right after a change is committed: best effort. The change is already durable and
    the sweep publishes the row within 30 s whatever happens here, so a Redis blip must not turn a successful
    request into a 500."""
    try:
        await flush(sessions, publisher, signals)
    except Exception as exc:  # the sweep is the guarantee; this is only the fast path
        log.warning("outbox_flush_deferred", error=type(exc).__name__)
