"""GET /v1/indicators/{isin}, /v1/signals and /v1/signals/{signal_id}. Built in TE2 (indicators) and TE3 (signals)."""

from __future__ import annotations

from fastapi import APIRouter
from tbo_platform_kit.http_server import requires
from tbo_platform_kit.tokens import Claims

from tbo_technical_engine.api.stub import not_implemented

router = APIRouter()


@router.get("/v1/indicators/{isin}")
async def get_indicators(isin: str, caller: Claims = requires("technical:read")) -> None:
    raise not_implemented("TE2")


@router.get("/v1/signals")
async def list_signals(caller: Claims = requires("technical:read")) -> None:
    raise not_implemented("TE3")


@router.get("/v1/signals/{signal_id}")
async def get_signal(signal_id: str, caller: Claims = requires("technical:read")) -> None:
    raise not_implemented("TE3")
