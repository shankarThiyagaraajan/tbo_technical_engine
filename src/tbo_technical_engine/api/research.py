"""GET /v1/strategies (TE3) and the backtest operations (TE5)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from tbo_platform_kit.http_server import requires
from tbo_platform_kit.tokens import Claims

from tbo_technical_engine.api.stub import not_implemented
from tbo_technical_engine.utils.timefmt import z

router = APIRouter()


@router.get("/v1/strategies")
async def list_strategies(request: Request, caller: Claims = requires("technical:read")) -> dict[str, Any]:
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
async def create_backtest(caller: Claims = requires("technical:research")) -> None:
    raise not_implemented("TE5")


@router.get("/v1/backtests/{backtest_id}")
async def get_backtest(backtest_id: str, caller: Claims = requires("technical:research")) -> None:
    raise not_implemented("TE5")


@router.get("/v1/backtests/{backtest_id}/trades")
async def list_backtest_trades(backtest_id: str, caller: Claims = requires("technical:research")) -> None:
    raise not_implemented("TE5")
