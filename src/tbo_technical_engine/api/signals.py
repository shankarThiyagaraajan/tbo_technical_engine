"""GET /v1/indicators/{isin}, /v1/signals and /v1/signals/{signal_id}. Built in TE2 (indicators) and TE3 (signals)."""

from __future__ import annotations

import re
from datetime import date as date_type
from datetime import datetime, time, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from tbo_contracts.models.common import Timeframe
from tbo_platform_kit.calendar import IST
from tbo_platform_kit.http_server import requires
from tbo_platform_kit.problems import Problem
from tbo_platform_kit.tokens import Claims

from tbo_technical_engine.api.shapes import candidate_dict
from tbo_technical_engine.store import indicators as indicators_store
from tbo_technical_engine.store import signals as signals_store
from tbo_technical_engine.utils import paging
from tbo_technical_engine.utils.timefmt import parse_z, z

ISIN = re.compile(r"^IN[A-Z0-9]{9}[0-9]$")
ULID = re.compile(r"^[0-7][0-9A-HJKMNP-TV-Z]{25}$")

router = APIRouter()


@router.get("/v1/indicators/{isin}")
async def get_indicators(
    request: Request,
    isin: str,
    timeframe: Annotated[Timeframe, Query()] = "5m",  # type: ignore[assignment]
    names: Annotated[str | None, Query(max_length=1200)] = None,
    caller: Claims = requires("technical:read"),
) -> dict[str, Any]:
    """The values at the last evaluated closed candle. 404 for a stock that has none (not in the universe, or not
    evaluated yet today)."""
    if not ISIN.fullmatch(isin):
        raise Problem("validation_failed", "Not an ISIN.", errors=[{"field": "isin", "message": "not an ISIN"}])
    async with request.app.state.sessions() as session:
        snapshot = await indicators_store.latest(session, isin, str(timeframe))
    if snapshot is None:
        raise Problem("not_found", f"No indicator values for {isin} on {timeframe}.")
    values = snapshot.values
    if names:
        wanted = {n.strip() for n in names.split(",") if n.strip()}
        values = {k: v for k, v in values.items() if k in wanted}
    return {
        "isin": snapshot.isin,
        "timeframe": snapshot.timeframe,
        "candle_start_at": z(snapshot.candle_start_at),
        "values": values,
        "backend": snapshot.backend,
        "computed_at": z(snapshot.computed_at),
    }


@router.get("/v1/signals")
async def list_signals(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    cursor: Annotated[str | None, Query(min_length=1, max_length=512)] = None,
    date: Annotated[date_type | None, Query()] = None,
    isin: Annotated[str | None, Query()] = None,
    strategy: Annotated[str | None, Query(pattern=r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$", max_length=60)] = None,
    caller: Claims = requires("technical:read"),
) -> dict[str, Any]:
    """Candidates of one trade day (default today, IST), newest first."""
    if isin is not None and not ISIN.fullmatch(isin):
        raise Problem("validation_failed", "Not an ISIN.", errors=[{"field": "isin", "message": "not an ISIN"}])
    day = date or datetime.now(IST).date()
    start = datetime.combine(day, time(0, 0), tzinfo=IST)
    after = paging.decode(cursor)
    marker: tuple[datetime, str] | None = None
    if after is not None:
        try:
            marker = (parse_z(str(after[0])), str(after[1]))
        except (IndexError, ValueError) as exc:
            raise Problem(
                "validation_failed",
                "The cursor is not valid.",
                errors=[{"field": "cursor", "message": "not a cursor from this API"}],
            ) from exc
    async with request.app.state.sessions() as session:
        rows = await signals_store.page(
            session, start=start, end=start + timedelta(days=1), isin=isin, strategy=strategy, after=marker, limit=limit
        )
    page, more = rows[:limit], len(rows) > limit
    return {
        "items": [candidate_dict(c) for c in page],
        "next_cursor": paging.encode([z(page[-1].created_at), page[-1].signal_id]) if more else None,
    }


@router.get("/v1/signals/{signal_id}")
async def get_signal(request: Request, signal_id: str, caller: Claims = requires("technical:read")) -> dict[str, Any]:
    """One stored candidate by its ULID; 404 `not_found` when there is none."""
    if not ULID.fullmatch(signal_id):
        raise Problem("validation_failed", "Not a signal ID.", errors=[{"field": "signal_id", "message": "not a ULID"}])
    async with request.app.state.sessions() as session:
        found = await signals_store.get(session, signal_id)
    if found is None:
        raise Problem("not_found", f"No signal {signal_id}.")
    return candidate_dict(found)
