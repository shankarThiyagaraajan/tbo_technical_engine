"""The `swing_setup` table."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from tbo_technical_engine.schemas.swing import Setup
from tbo_technical_engine.store.tables import swing_setup as t
from tbo_technical_engine.utils.timefmt import z


def price(value: Decimal) -> str:
    """A price as the contract's decimal string."""
    return format(value, "f")


def to_dict(setup: Setup) -> dict[str, Any]:
    """The contract's swing_setup record (also the event's data)."""
    return {
        "setup_id": setup.setup_id,
        "isin": setup.isin,
        "setup_date": setup.setup_date.isoformat(),
        "setup_type": setup.setup_type,
        "side": setup.side,
        "entry": price(setup.entry),
        "stop": price(setup.stop),
        "target_1": price(setup.target_1),
        "target_2": None if setup.target_2 is None else price(setup.target_2),
        "valid_until_date": setup.valid_until_date.isoformat(),
        "params_version": setup.params_version,
        "created_at": z(setup.created_at),
    }


def _row_dict(row: Any) -> dict[str, Any]:
    return {
        "setup_id": row.setup_id,
        "isin": row.isin,
        "setup_date": row.setup_date.isoformat(),
        "setup_type": row.setup_type,
        "side": row.side,
        "entry": price(row.entry),
        "stop": price(row.stop),
        "target_1": price(row.target_1),
        "target_2": None if row.target_2 is None else price(row.target_2),
        "valid_until_date": row.valid_until_date.isoformat(),
        "params_version": row.params_version,
        "created_at": z(row.created_at),
    }


async def save(session: AsyncSession, setup: Setup) -> bool:
    """Insert; False when this stock already has a setup for that session (a retried job)."""
    stmt = (
        insert(t)
        .values(**setup.model_dump(mode="python"))
        .on_conflict_do_nothing(constraint="uq_swing_setup_isin_date")
        .returning(t.c.setup_id)
    )
    return (await session.execute(stmt)).first() is not None


async def latest_date(session: AsyncSession) -> date | None:
    """The most recent setup date, or None."""
    found: date | None = (await session.execute(select(func.max(t.c.setup_date)))).scalar_one()
    return found


async def page(session: AsyncSession, setup_date: date, *, after: str | None, limit: int) -> list[dict[str, Any]]:
    """Setups found after one session, ordered by ISIN; one extra row tells the caller there is a next page."""
    query = select(t).where(t.c.setup_date == setup_date)
    if after is not None:
        query = query.where(t.c.isin > after)
    return [_row_dict(r) for r in (await session.execute(query.order_by(t.c.isin).limit(limit + 1))).all()]
