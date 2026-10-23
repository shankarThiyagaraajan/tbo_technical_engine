"""GET /v1/strategies (TE3), and the backtest operations (TE5): create, read, and list the trades."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession
from tbo_platform_kit.http_server import requires
from tbo_platform_kit.idempotency import StoredResponse
from tbo_platform_kit.problems import Problem
from tbo_platform_kit.tokens import Claims

from tbo_technical_engine.api.shapes import backtest_dict, backtest_trade_dict
from tbo_technical_engine.schemas.backtest import BacktestCreate
from tbo_technical_engine.services import backtests
from tbo_technical_engine.store import backtests as store
from tbo_technical_engine.utils import paging
from tbo_technical_engine.utils.timefmt import z

ULID = re.compile(r"^[0-7][0-9A-HJKMNP-TV-Z]{25}$")
READ = "technical:read"
RESEARCH = "technical:research"  # backtests: start, read, cancel

router = APIRouter()


@router.get("/v1/strategies")
async def list_strategies(request: Request, caller: Claims = requires(READ)) -> dict[str, Any]:
    """Every strategy in the parameter file, active ones first, with the version its candidates carry."""
    file = request.app.state.strategies
    items = [
        {
            "strategy": name,
            "title": spec.title,
            "kind": "intraday",
            "timeframe": spec.timeframe,
            "is_active": spec.is_active,
            "params_version": file.params_version,
            "params": spec.params,
            "active_from_at": z(file.active_from_at),
        }
        for name, spec in file.strategies.items()
    ]
    items.sort(key=lambda s: (not s["is_active"], s["strategy"]))
    return {"strategies": items}


@router.post("/v1/backtests", status_code=202)
async def create_backtest(
    request: Request,
    body: BacktestCreate,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    caller: Claims = requires(RESEARCH),
) -> Response:
    """Queue a backtest on the research queue. The caps (stocks, days, concurrent jobs) are in `config/backtest.yaml`;
    a request over them is a 422 or a 429, never queued. The row is written inside the idempotency transaction, the
    job is sent after the commit, and a Beat sweep re-sends a job whose send was lost."""
    app = request.app
    created: list[str] = []

    async def work(session: AsyncSession) -> StoredResponse:
        backtest_id = await backtests.submit(
            session,
            body,
            app.state.strategies,
            app.state.backtest_settings,
            requested_by=caller.sub,
            code_version=app.state.settings.version,
            now=datetime.now(UTC),
        )
        created.append(backtest_id)
        row = await store.get(session, backtest_id)
        return StoredResponse(202, backtest_dict(row), {"Location": f"/v1/backtests/{backtest_id}"})

    response: Response = await app.state.idempotency.run(request, caller.sub, idempotency_key, work)
    if created:  # not a replay: this request made the row
        app.state.enqueue_backtest(created[0])
    return response


@router.get("/v1/backtests/{backtest_id}")
async def get_backtest(request: Request, backtest_id: str, caller: Claims = requires(RESEARCH)) -> dict[str, Any]:
    """One backtest with its status, coverage and the contract's six metrics."""
    async with request.app.state.sessions() as session:
        row = await _row(session, backtest_id)
    return backtest_dict(row)


@router.get("/v1/backtests/{backtest_id}/trades")
async def list_backtest_trades(
    request: Request,
    backtest_id: str,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    cursor: Annotated[str | None, Query(min_length=1, max_length=512)] = None,
    caller: Claims = requires(RESEARCH),
) -> dict[str, Any]:
    """Trades in entry order; empty until the backtest has succeeded."""
    after = paging.decode(cursor)
    after_no = 0
    if after is not None:
        if len(after) != 1 or not isinstance(after[0], int) or after[0] < 0:
            raise Problem(
                "validation_failed",
                "The cursor is not valid.",
                errors=[{"field": "cursor", "message": "not a cursor from this API"}],
            )
        after_no = after[0]
    async with request.app.state.sessions() as session:
        await _row(session, backtest_id)
        rows = await store.trades_page(session, backtest_id, after_no, limit)
    more = len(rows) > limit
    rows = rows[:limit]
    return {
        "items": [backtest_trade_dict(r) for r in rows],
        "next_cursor": paging.encode([rows[-1].trade_no]) if more else None,
    }


async def _row(session: AsyncSession, backtest_id: str) -> Any:
    if not ULID.fullmatch(backtest_id):
        raise Problem("not_found", f"No backtest {backtest_id}.")
    row = await store.get(session, backtest_id)
    if row is None:
        raise Problem("not_found", f"No backtest {backtest_id}.")
    return row
