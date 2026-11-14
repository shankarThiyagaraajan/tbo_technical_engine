"""Kit 2.x start check (remove-lab-mode row D2-5k2, plan P24): `create_app` refuses to start when a route outside the
kit's public paths has no `requires()` in its dependencies. This app builds one way only (`verifier_from_settings`,
service tokens; it never calls `trusted_issuers_from_settings`), so there is no realm variant to check.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from starlette.types import Message, Scope
from tbo_platform_kit.http_server import RouteUnguarded, unguarded_routes


async def _start_and_stop(app: FastAPI) -> list[str]:
    """Drive the ASGI lifespan the way a server does (the kit's middleware runs its route check on startup) and return
    the message types the app sent.
    """
    incoming: list[Message] = [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}]
    sent: list[str] = []

    async def receive() -> Message:
        return incoming.pop(0)

    async def send(message: Message) -> None:
        sent.append(str(message["type"]))

    scope: Scope = {"type": "lifespan", "asgi": {"version": "3.0", "spec_version": "2.0"}, "state": {}}
    await app(scope, receive, send)
    return sent


def test_every_route_of_the_app_is_guarded(app: FastAPI) -> None:
    """Every route but the kit's public ones (/health/live, /health/ready, /metrics, /openapi.json) takes requires()."""
    assert unguarded_routes(app) == []


async def test_the_app_starts_and_stops(app: FastAPI) -> None:
    """The kit's start check passes and the app's own lifespan (gauge refresher, calendar) runs and stops."""
    assert await _start_and_stop(app) == ["lifespan.startup.complete", "lifespan.shutdown.complete"]


async def test_an_unguarded_route_fails_the_start(app: FastAPI) -> None:
    """The check is live on this app: a route added without requires() refuses the start."""

    @app.get("/v1/unguarded")
    async def unguarded() -> dict[str, str]:
        return {}

    assert unguarded_routes(app) == ["GET /v1/unguarded"]
    with pytest.raises(RouteUnguarded):
        await _start_and_stop(app)
