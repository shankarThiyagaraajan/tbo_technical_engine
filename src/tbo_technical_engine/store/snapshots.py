"""The `technical_snapshot` table: one row per (session, stock)."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from tbo_technical_engine.schemas.swing import Snapshot
from tbo_technical_engine.tables import technical_snapshot as t


def content_hash(snapshot: Snapshot) -> str:
    """Hash of the record without `computed_at`: the same numbers computed twice are the same snapshot."""
    body = snapshot.model_dump(mode="json", exclude={"computed_at"})
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


async def upsert(session: AsyncSession, snapshot: Snapshot, now: datetime) -> bool:
    """Write the snapshot. True when it is new or its numbers changed; False when the stored one is identical (a
    retried job, an 08:00 refresh of a stock whose data did not change), so the caller announces nothing."""
    digest = content_hash(snapshot)
    insert_stmt = insert(t).values(
        as_of_date=date.fromisoformat(snapshot.as_of_date),
        isin=snapshot.isin,
        data=snapshot.model_dump(mode="json"),
        content_hash=digest,
        created_at=now,
        updated_at=now,
    )
    stmt = insert_stmt.on_conflict_do_update(
        index_elements=["as_of_date", "isin"],
        set_={"data": insert_stmt.excluded.data, "content_hash": digest, "updated_at": now},
        where=t.c.content_hash != digest,
    ).returning(t.c.isin)
    return (await session.execute(stmt)).first() is not None


async def serving(session: AsyncSession, trade_date: date | None) -> date | None:
    """The session whose snapshot serves `trade_date`: the latest built strictly before it ("the snapshot built after
    the previous session's close"), or the latest of all when no date is given. None when there is none."""
    query = select(func.max(t.c.as_of_date))
    if trade_date is not None:
        query = query.where(t.c.as_of_date < trade_date)
    found: date | None = (await session.execute(query)).scalar_one()
    return found


async def get(session: AsyncSession, as_of: date, isin: str) -> dict[str, Any] | None:
    row = (await session.execute(select(t.c.data).where(t.c.as_of_date == as_of, t.c.isin == isin))).first()
    return None if row is None else dict(row.data)


async def page(session: AsyncSession, as_of: date, *, after: str | None, limit: int) -> list[dict[str, Any]]:
    """Snapshots of one session ordered by ISIN; one extra row tells the caller there is a next page."""
    query = select(t.c.data).where(t.c.as_of_date == as_of)
    if after is not None:
        query = query.where(t.c.isin > after)
    return [dict(r.data) for r in (await session.execute(query.order_by(t.c.isin).limit(limit + 1))).all()]
