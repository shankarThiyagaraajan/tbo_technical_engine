"""The HTTP app: the kit's platform interface (health, metrics, errors, tokens, the served contract) plus this
component's routes. Everything is built here, so tests can pass their own settings and token verifier.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import redis.asyncio as aioredis
from fastapi import FastAPI
from tbo_platform_kit.db import create_engine, session_factory
from tbo_platform_kit.events import EventPublisher
from tbo_platform_kit.http_server import Verifier, create_app, database_check, redis_check, verifier_from_settings
from tbo_platform_kit.metrics import Metrics

from tbo_technical_engine.api import context, research, signals, snapshots
from tbo_technical_engine.config.settings import REPOSITORY, Settings
from tbo_technical_engine.services.strategy_loader import load_strategies


def build_app(settings: Settings | None = None, *, verifier: Verifier | None = None) -> FastAPI:
    """The API process: routers, a database and Redis behind the readiness check, and the shared objects the
    routes read from `app.state`. Settings are required up front so a mis-set environment stops the start,
    not a request."""
    settings = settings or Settings()
    settings.require("database_url", "redis_url")
    assert settings.redis_url is not None
    profile = settings.profile
    engine = create_engine(settings)
    redis = aioredis.Redis.from_url(settings.redis_url.get_secret_value())
    metrics = Metrics(settings.short_name, settings.version, profile.mode.value)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        await redis.aclose()
        await engine.dispose()

    app = create_app(
        settings=settings,
        verifier=verifier or verifier_from_settings(settings),
        contract=settings.contract_file,
        ready_checks=[database_check(engine), redis_check(redis)],
        metrics=metrics,
        lifespan=lifespan,
    )
    for router in (signals.router, context.router, snapshots.router, research.router):
        app.include_router(router)
    state: Any = app.state
    state.engine = engine
    state.sessions = session_factory(engine)
    state.redis = redis
    state.strategies = load_strategies(settings.strategies_file)
    state.publisher = EventPublisher(redis, profile, source=REPOSITORY, metrics=metrics)
    return app
