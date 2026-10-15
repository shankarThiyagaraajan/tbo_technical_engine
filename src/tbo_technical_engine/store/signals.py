"""The `signal_candidate` table."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from tbo_technical_engine.schemas.signal import SignalCandidate
from tbo_technical_engine.tables import signal_candidate as t


def price(value: Decimal) -> str:
    return format(value, "f")


def _to_candidate(row: Any) -> SignalCandidate:
    return SignalCandidate(
        signal_id=row.signal_id,
        isin=row.isin,
        strategy=row.strategy,
        side=row.side,
        timeframe=row.timeframe,
        candle_start_at=row.candle_start_at,
        entry_low=price(row.entry_low),
        entry_high=price(row.entry_high),
        stop=price(row.stop),
        target_1=price(row.target_1),
        target_2=price(row.target_2),
        strength=format(row.strength, "f"),
        components=row.components,
        indicator_values=row.indicator_values,
        params_version=row.params_version,
        created_at=row.created_at,
    )


async def save(session: AsyncSession, c: SignalCandidate) -> bool:
    """Insert; False when this candidate (same ID) is already stored."""
    values = c.model_dump(mode="python")
    values["components"] = [x.model_dump() for x in c.components]
    stmt = insert(t).values(values).on_conflict_do_nothing(index_elements=["signal_id"]).returning(t.c.signal_id)
    return (await session.execute(stmt)).first() is not None


async def get(session: AsyncSession, signal_id: str) -> SignalCandidate | None:
    row = (await session.execute(select(t).where(t.c.signal_id == signal_id))).first()
    return None if row is None else _to_candidate(row)


async def page(
    session: AsyncSession,
    *,
    start: datetime,
    end: datetime,
    isin: str | None,
    strategy: str | None,
    after: tuple[datetime, str] | None,
    limit: int,
) -> list[SignalCandidate]:
    """Candidates whose candle started in [start, end), newest first by created_at then signal_id; one extra row
    tells the caller there is a next page."""
    query = select(t).where(t.c.candle_start_at >= start, t.c.candle_start_at < end)
    if isin:
        query = query.where(t.c.isin == isin)
    if strategy:
        query = query.where(t.c.strategy == strategy)
    if after:
        query = query.where(or_(t.c.created_at < after[0], and_(t.c.created_at == after[0], t.c.signal_id < after[1])))
    rows = (await session.execute(query.order_by(t.c.created_at.desc(), t.c.signal_id.desc()).limit(limit + 1))).all()
    return [_to_candidate(r) for r in rows]
