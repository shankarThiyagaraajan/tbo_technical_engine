"""The `indicator_snapshot` table: written by the signal worker, read by the API, pruned by a nightly job."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from tbo_technical_engine.schemas.indicators import IndicatorSnapshot
from tbo_technical_engine.tables import indicator_snapshot


async def save(session: AsyncSession, snapshot: IndicatorSnapshot) -> None:
    """Insert, or replace the row of the same candle (a corrected candle is evaluated again)."""
    row = {
        "isin": snapshot.isin,
        "timeframe": snapshot.timeframe,
        "candle_start_at": snapshot.candle_start_at,
        "values": snapshot.values,
        "backend": snapshot.backend,
        "computed_at": snapshot.computed_at,
    }
    stmt = insert(indicator_snapshot).values(row)
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=["isin", "timeframe", "candle_start_at"],
            set_={k: stmt.excluded[k] for k in ("values", "backend", "computed_at")},
        )
    )


async def latest(session: AsyncSession, isin: str, timeframe: str) -> IndicatorSnapshot | None:
    row = (
        await session.execute(
            select(indicator_snapshot)
            .where(indicator_snapshot.c.isin == isin, indicator_snapshot.c.timeframe == timeframe)
            .order_by(indicator_snapshot.c.candle_start_at.desc())
            .limit(1)
        )
    ).first()
    if row is None:
        return None
    return IndicatorSnapshot(
        isin=row.isin,
        timeframe=row.timeframe,
        candle_start_at=row.candle_start_at,
        values=row.values,
        backend=row.backend,
        computed_at=row.computed_at,
    )


async def prune(session: AsyncSession, older_than: datetime) -> int:
    """Delete snapshots computed before `older_than`; returns how many."""
    result = await session.execute(delete(indicator_snapshot).where(indicator_snapshot.c.computed_at < older_than))
    return int(result.rowcount or 0)  # type: ignore[attr-defined]
