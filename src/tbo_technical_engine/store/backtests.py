"""The `backtest`, `backtest_trade` and `strategy_evidence` tables."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from tbo_technical_engine.schemas.backtest import Trade
from tbo_technical_engine.store.tables import backtest as t
from tbo_technical_engine.store.tables import backtest_trade as tt
from tbo_technical_engine.store.tables import strategy_evidence as ev

ACTIVE = ("queued", "running")


async def create(
    session: AsyncSession,
    *,
    backtest_id: str,
    strategy: str,
    params_version: str,
    params_overrides: list[dict[str, str]],
    from_date: date,
    to_date: date,
    isins: list[str],
    code_version: str,
    backend: str,
    requested_by: str,
    now: datetime,
) -> None:
    """Insert a waiting backtest row."""
    await session.execute(
        insert(t).values(
            backtest_id=backtest_id,
            status="queued",
            strategy=strategy,
            params_version=params_version,
            params_overrides=params_overrides,
            from_date=from_date,
            to_date=to_date,
            isins=isins,
            code_version=code_version,
            backend=backend,
            requested_by=requested_by,
            created_at=now,
        )
    )


async def active_count(session: AsyncSession) -> int:
    """Queued and running jobs: what the concurrency cap counts."""
    return int((await session.execute(select(func.count()).select_from(t).where(t.c.status.in_(ACTIVE)))).scalar_one())


async def waiting(session: AsyncSession, queued_before: datetime) -> list[str]:
    """Backtests still `queued` that were created before `queued_before` (their job was probably never sent)."""
    stmt = (
        select(t.c.backtest_id).where(t.c.status == "queued", t.c.created_at < queued_before).order_by(t.c.created_at)
    )
    return [r.backtest_id for r in (await session.execute(stmt)).all()]


async def get(session: AsyncSession, backtest_id: str) -> Any | None:
    """One backtest row, or None."""
    return (await session.execute(select(t).where(t.c.backtest_id == backtest_id))).first()


async def claim(session: AsyncSession, backtest_id: str, now: datetime, stale_after: timedelta) -> bool:
    """Move queued -> running. A `running` job is claimable again only when it is older than `stale_after` (its worker
    died); that is what makes the task safe when the broker delivers it twice."""
    result = await session.execute(
        update(t)
        .where(
            t.c.backtest_id == backtest_id,
            (t.c.status == "queued") | ((t.c.status == "running") & (t.c.started_at < now - stale_after)),
        )
        .values(status="running", started_at=now, error=None)
        .returning(t.c.backtest_id)
    )
    return result.first() is not None


async def finish(
    session: AsyncSession,
    backtest_id: str,
    *,
    metrics: dict[str, Any],
    result: dict[str, Any],
    trades: Sequence[Trade],
    now: datetime,
) -> None:
    """Replace the run's trades and mark it succeeded, in one transaction (a re-run leaves no mix of two runs)."""
    await session.execute(delete(tt).where(tt.c.backtest_id == backtest_id))
    rows = [
        {
            "backtest_id": backtest_id,
            "trade_no": n,
            "isin": x.isin,
            "side": x.side,
            "entry_at": x.entry_at,
            "exit_at": x.exit_at,
            "entry_price": x.entry_price,
            "exit_price": x.exit_price,
            "exit_reason": x.exit_reason,
            "r_multiple": x.r,
            "gross_r": x.gross_r,
            "quantity": x.quantity,
            "costs": x.costs,
        }
        for n, x in enumerate(sorted(trades, key=lambda x: (x.entry_at, x.isin, x.signal_id)), start=1)
    ]
    for i in range(0, len(rows), 2000):
        await session.execute(insert(tt).values(rows[i : i + 2000]))
    await session.execute(
        update(t)
        .where(t.c.backtest_id == backtest_id)
        .values(status="succeeded", metrics=metrics, result=result, error=None, finished_at=now)
    )


async def fail(session: AsyncSession, backtest_id: str, error: str, now: datetime) -> None:
    """Mark a backtest failed with a short error text."""
    await session.execute(
        update(t).where(t.c.backtest_id == backtest_id).values(status="failed", error=error[:500], finished_at=now)
    )


async def trades_page(session: AsyncSession, backtest_id: str, after_no: int, limit: int) -> list[Any]:
    """A page of a backtest's trades in order, after the given trade number."""
    stmt = (
        select(tt)
        .where(tt.c.backtest_id == backtest_id, tt.c.trade_no > after_no)
        .order_by(tt.c.trade_no)
        .limit(limit + 1)
    )
    return list((await session.execute(stmt)).all())


async def save_evidence(
    session: AsyncSession, strategy: str, params_version: str, backtest_id: str, data: dict[str, Any], now: datetime
) -> None:
    """Store the evidence summary of a strategy and parameter version from a finished backtest."""
    stmt = insert(ev).values(
        strategy=strategy, params_version=params_version, backtest_id=backtest_id, data=data, computed_at=now
    )
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=["strategy", "params_version"],
            set_={"backtest_id": backtest_id, "data": data, "computed_at": now},
        )
    )


async def evidence(session: AsyncSession, strategy: str, params_version: str | None = None) -> dict[str, Any] | None:
    """The stored evidence for a strategy; without a version, the newest one."""
    stmt = select(ev).where(ev.c.strategy == strategy)
    if params_version is not None:
        stmt = stmt.where(ev.c.params_version == params_version)
    row = (await session.execute(stmt.order_by(ev.c.computed_at.desc()).limit(1))).first()
    return None if row is None else dict(row.data)
