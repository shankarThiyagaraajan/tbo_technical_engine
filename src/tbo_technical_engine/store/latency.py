"""The `evaluation_boundary` table: one row per 5-minute boundary, written by the signal worker, read by the API
(gauges) and by `scripts/session_report.py`."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from tbo_technical_engine.schemas.latency import BoundaryLatency
from tbo_technical_engine.store.tables import evaluation_boundary


async def save_many(session: AsyncSession, rows: Iterable[BoundaryLatency]) -> int:
    """Write rows; a boundary already recorded is kept as it is (the process that wrote it saw more of it than a worker
    restarted halfway through the grace period)."""
    values = [r.model_dump() for r in rows]
    if not values:
        return 0
    stmt = insert(evaluation_boundary).values(values).on_conflict_do_nothing(index_elements=["boundary_at"])
    await session.execute(stmt)
    return len(values)


async def for_day(session: AsyncSession, day: date) -> list[BoundaryLatency]:
    """The rows of one trading day, oldest first."""
    found = await session.execute(
        select(evaluation_boundary)
        .where(evaluation_boundary.c.trade_date == day)
        .order_by(evaluation_boundary.c.boundary_at)
    )
    return [BoundaryLatency.model_validate(dict(r._mapping)) for r in found]


async def since(session: AsyncSession, start: datetime) -> list[BoundaryLatency]:
    """Rows whose boundary closed at or after `start`, oldest first."""
    found = await session.execute(
        select(evaluation_boundary)
        .where(evaluation_boundary.c.boundary_at >= start)
        .order_by(evaluation_boundary.c.boundary_at)
    )
    return [BoundaryLatency.model_validate(dict(r._mapping)) for r in found]
