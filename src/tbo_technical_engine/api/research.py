"""GET /v1/strategies (TE3) and the backtest operations (TE5)."""

from __future__ import annotations

from fastapi import APIRouter
from tbo_platform_kit.http_server import requires
from tbo_platform_kit.tokens import Claims

from tbo_technical_engine.api.stub import not_implemented

router = APIRouter()


@router.get("/v1/strategies")
async def list_strategies(caller: Claims = requires("technical:read")) -> None:
    raise not_implemented("TE3")


@router.post("/v1/backtests", status_code=202)
async def create_backtest(caller: Claims = requires("technical:research")) -> None:
    raise not_implemented("TE5")


@router.get("/v1/backtests/{backtest_id}")
async def get_backtest(backtest_id: str, caller: Claims = requires("technical:research")) -> None:
    raise not_implemented("TE5")


@router.get("/v1/backtests/{backtest_id}/trades")
async def list_backtest_trades(backtest_id: str, caller: Claims = requires("technical:research")) -> None:
    raise not_implemented("TE5")
