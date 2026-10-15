"""GET /v1/snapshots, /v1/snapshots/{isin} and /v1/swing-setups. Built in TE6."""

from __future__ import annotations

from fastapi import APIRouter
from tbo_platform_kit.http_server import requires
from tbo_platform_kit.tokens import Claims

from tbo_technical_engine.api.stub import not_implemented

router = APIRouter()


@router.get("/v1/snapshots")
async def list_snapshots(caller: Claims = requires("technical:read")) -> None:
    raise not_implemented("TE6")


@router.get("/v1/snapshots/{isin}")
async def get_snapshot(isin: str, caller: Claims = requires("technical:read")) -> None:
    raise not_implemented("TE6")


@router.get("/v1/swing-setups")
async def list_swing_setups(caller: Claims = requires("technical:read")) -> None:
    raise not_implemented("TE6")
