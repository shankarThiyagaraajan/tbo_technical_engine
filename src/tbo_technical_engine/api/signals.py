"""GET /v1/indicators/{isin}, /v1/signals and /v1/signals/{signal_id}. Built in TE2 (indicators) and TE3 (signals)."""

from __future__ import annotations

import re
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from tbo_contracts.models.common import Timeframe
from tbo_platform_kit.http_server import requires
from tbo_platform_kit.problems import Problem
from tbo_platform_kit.tokens import Claims

from tbo_technical_engine.api.stub import not_implemented
from tbo_technical_engine.store import indicators as indicators_store
from tbo_technical_engine.utils.timefmt import z

ISIN = re.compile(r"^IN[A-Z0-9]{9}[0-9]$")

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
async def list_signals(caller: Claims = requires("technical:read")) -> None:
    raise not_implemented("TE3")


@router.get("/v1/signals/{signal_id}")
async def get_signal(signal_id: str, caller: Claims = requires("technical:read")) -> None:
    raise not_implemented("TE3")
