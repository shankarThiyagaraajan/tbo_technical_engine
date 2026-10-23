"""The transactional outbox: an event is written in the same transaction as the change it announces, then
published to Redis after the commit. A crash between the two leaves the row unpublished, and the sweep
(tasks.flush_outbox, every 30 s) publishes it later, so no event is lost. Delivery is at least once; consumers
are idempotent by event id.

One row that Redis keeps refusing (a payload too large for the stream, a stream the component's ACL user may not
write after a rename) must not stop every later event: each row is published on its own, a failure is recorded on
the row (`attempts`, `last_error`) and the others go on; after MAX_ATTEMPTS the row is left behind (`dead_at`) and
logged at ERROR, for a person to look at (it stays in the table; nothing is deleted). Redis being down is not a
row's fault: it stops the sweep and counts nothing.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from redis.exceptions import ResponseError
from sqlalchemy import Row, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tbo_contracts import validate_event
from tbo_platform_kit.events import stream_of
from tbo_platform_kit.logging import get_logger
from tbo_signals import Signals

from tbo_technical_engine.store.tables import outbox

log = get_logger(__name__)
BATCH = 100
MAX_ATTEMPTS = 10
"""Publish attempts before a row is left behind: ten sweeps, five minutes, is long enough for a Redis restart and
short enough that a poison row is noticed the same morning."""


class Publisher(Protocol):
    """What the outbox needs from the kit's `EventPublisher`: one `publish`. A Protocol so a test can pass a fake."""

    async def publish(self, stream: str, event: dict[str, Any]) -> str:
        """Publish `event` to `stream`; returns the stream entry id."""
        ...


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
    sessions: async_sessionmaker[AsyncSession],
    publisher: Publisher,
    signals: Signals | None = None,
    *,
    count_attempts: bool = True,
) -> int:
    """Publish unpublished events, oldest first; returns how many. Safe to run from several processes.

    Two kinds of failure are told apart (review finding 9.1, the platform template's 2.2). A *row* fault
    (`_is_row_fault`: a payload that is not JSON, a stream Redis refuses with a ResponseError such as NOPERM) is
    recorded on the row and the sweep goes on; the row gets one attempt per sweep, and a row failed in this sweep is not
    selected again, so a full batch cannot burn ten attempts in a millisecond. Any other failure (Redis down, a
    timeout) is the *infrastructure*, not the row's fault: the sweep stops at once and no attempt is counted, so a Redis
    restart can never make rows dead. `count_attempts=False` (the request path) records the error but never adds an
    attempt: only the 30 s sweep counts. Rows at MAX_ATTEMPTS are left behind with `dead_at` set (clear `dead_at` and
    `attempts` to retry). Each published row counts as `technical.signal_events` when `signals` is given.
    """
    published = 0
    failed: set[str] = set()
    while True:
        async with sessions() as session, session.begin():
            rows = await _batch(session, failed)
            for row in rows:
                outcome = await _publish_one(session, publisher, row, count_attempts=count_attempts)
                if outcome == "deferred":
                    return published  # Redis is the problem, not the rows: try again next sweep, count nothing
                if outcome == "row_fault":
                    failed.add(row.event_id)
                    continue
                published += 1
                if signals is not None:
                    signals.emit("technical.signal_events", 1)
        if len(rows) < BATCH:
            if published:
                log.info("outbox_flushed", published=published)
            return published


async def _batch(session: AsyncSession, failed: set[str]) -> Sequence[Row[Any]]:
    """The next oldest unpublished rows, locked; rows that failed in this sweep are left out."""
    return (
        await session.execute(
            select(outbox.c.event_id, outbox.c.stream, outbox.c.payload, outbox.c.attempts)
            .where(outbox.c.published_at.is_(None), outbox.c.dead_at.is_(None), outbox.c.event_id.not_in(failed))
            .order_by(outbox.c.created_at)
            .limit(BATCH)
            .with_for_update(skip_locked=True)
        )
    ).all()


async def _publish_one(
    session: AsyncSession, publisher: Publisher, row: Row[Any], *, count_attempts: bool
) -> Literal["published", "row_fault", "deferred"]:
    """Publish one row and mark it; a row fault is recorded on the row, any other failure defers the whole sweep."""
    try:
        await publisher.publish(row.stream, json.loads(row.payload))
    except Exception as exc:  # whatever Redis or the payload did, it is sorted below
        if not _is_row_fault(exc):
            log.warning("outbox_publish_deferred", error=type(exc).__name__)
            return "deferred"
        await _record_row_fault(session, row, exc, count_attempts=count_attempts)
        return "row_fault"
    await session.execute(
        update(outbox).where(outbox.c.event_id == row.event_id).values(published_at=datetime.now(UTC))
    )
    return "published"


async def _record_row_fault(session: AsyncSession, row: Row[Any], exc: Exception, *, count_attempts: bool) -> None:
    """Keep the error on the row, count the attempt (sweeps only), and leave it behind at MAX_ATTEMPTS."""
    error = type(exc).__name__
    attempts = (row.attempts or 0) + (1 if count_attempts else 0)
    dead = datetime.now(UTC) if attempts >= MAX_ATTEMPTS else None
    await session.execute(
        update(outbox)
        .where(outbox.c.event_id == row.event_id)
        .values(attempts=attempts, last_error=f"{error}: {exc}"[:500], dead_at=dead)
    )
    if dead:
        log.error("outbox_row_left_behind", event_id=row.event_id, attempts=attempts, error=error)
    else:
        log.warning("outbox_publish_failed", event_id=row.event_id, attempt=attempts, error=error)


def _is_row_fault(exc: Exception) -> bool:
    """True when the row itself is the problem: retrying the same payload will fail the same way."""
    return isinstance(exc, ValueError | TypeError | ResponseError)


async def waiting(sessions: async_sessionmaker[AsyncSession]) -> int:
    """Rows still to publish (not counting the ones left behind): the board's "outbox waiting"."""
    async with sessions() as session:
        count = await session.scalar(
            select(func.count()).select_from(outbox).where(outbox.c.published_at.is_(None), outbox.c.dead_at.is_(None))
        )
    return int(count or 0)


async def flush_after_commit(
    sessions: async_sessionmaker[AsyncSession], publisher: Publisher, signals: Signals | None = None
) -> None:
    """The request-path flush, right after a change is committed: best effort. The change is already durable and
    the sweep publishes the row within 30 s whatever happens here, so a Redis blip must not turn a successful
    request into a 500."""
    try:
        await flush(sessions, publisher, signals, count_attempts=False)
    except Exception as exc:  # the sweep is the guarantee; this is only the fast path
        log.warning("outbox_flush_deferred", error=type(exc).__name__)
