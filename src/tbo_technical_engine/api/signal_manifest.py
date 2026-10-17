"""GET /v1/signal-manifest: the signals this service declares, for the platform registry (tbo_signals)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from tbo_platform_kit.http_server import requires
from tbo_platform_kit.tokens import Claims
from tbo_signals import signals_scope

from tbo_technical_engine.services.platform_signals import MANIFEST_PATH

router = APIRouter()
SCOPE = signals_scope("technical")


@router.get(MANIFEST_PATH)
async def get_signal_manifest(request: Request, caller: Claims = requires(SCOPE)) -> dict[str, Any]:
    """The manifest and the level it runs at. Served at every level, so the registry sees a switched-off service too."""
    payload: dict[str, Any] = request.app.state.signals.manifest_payload()
    return payload
