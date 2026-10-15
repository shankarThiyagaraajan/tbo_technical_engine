"""The `market_context` table."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from tbo_technical_engine.schemas.context import MarketContext
from tbo_technical_engine.tables import market_context as t


async def save(session: AsyncSession, at: datetime, context: MarketContext, created_at: datetime) -> bool:
    """Insert; False when this 5-minute context already exists (a second process, a restart in the same minute)."""
    stmt = (
        insert(t)
        .values(
            context_at=at,
            trade_date=date.fromisoformat(context.trade_date),
            data=context.model_dump(mode="json"),
            data_quality=context.data_quality,
            created_at=created_at,
        )
        .on_conflict_do_nothing(index_elements=["context_at"])
        .returning(t.c.context_at)
    )
    return (await session.execute(stmt)).first() is not None


def _to_context(row: Any) -> dict[str, Any]:
    data: dict[str, Any] = row.data
    return data


async def latest(session: AsyncSession) -> dict[str, Any] | None:
    row = (await session.execute(select(t).order_by(t.c.context_at.desc()).limit(1))).first()
    return None if row is None else _to_context(row)


async def history(
    session: AsyncSession, *, start: datetime, end: datetime, after: datetime | None, limit: int
) -> list[tuple[datetime, dict[str, Any]]]:
    """Contexts in [start, end), oldest first; one extra row tells the caller there is a next page."""
    query = select(t).where(t.c.context_at >= start, t.c.context_at < end)
    if after is not None:
        query = query.where(t.c.context_at > after)
    rows = (await session.execute(query.order_by(t.c.context_at).limit(limit + 1))).all()
    return [(r.context_at, _to_context(r)) for r in rows]
