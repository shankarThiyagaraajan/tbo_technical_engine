"""GET /v1/context and /v1/context/history. Built in TE4."""

from __future__ import annotations

from fastapi import APIRouter
from tbo_platform_kit.http_server import requires
from tbo_platform_kit.tokens import Claims

from tbo_technical_engine.api.stub import not_implemented

router = APIRouter()


@router.get("/v1/context")
async def get_context(caller: Claims = requires("technical:read")) -> None:
    raise not_implemented("TE4")


@router.get("/v1/context/history")
async def list_context_history(caller: Claims = requires("technical:read")) -> None:
    raise not_implemented("TE4")
