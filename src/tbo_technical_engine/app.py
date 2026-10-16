"""The HTTP app: the kit's platform interface (health, metrics, errors, tokens, the served contract) plus this
component's routes. Everything is built here, so tests can pass their own settings and token verifier.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import redis.asyncio as aioredis
from fastapi import FastAPI
from tbo_platform_kit.calendar import TradingCalendar, now_utc
from tbo_platform_kit.db import create_engine, session_factory
from tbo_platform_kit.events import EventPublisher
from tbo_platform_kit.http_server import Verifier, create_app, database_check, redis_check, verifier_from_settings
from tbo_platform_kit.idempotency import IdempotencyStore
from tbo_platform_kit.metrics import Metrics

from tbo_technical_engine.api import context, research, signals, snapshots
from tbo_technical_engine.config.settings import REPOSITORY, Settings
from tbo_technical_engine.services.backtests import enqueue_with_celery, load_settings
from tbo_technical_engine.services.latency_gauges import LatencyGauges, refresh_forever
from tbo_technical_engine.services.strategy_loader import load_strategies
from tbo_technical_engine.store.tables import metadata


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

    gauges = LatencyGauges(metrics)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        stop = asyncio.Event()
        refresher = asyncio.create_task(
            refresh_forever(gauges, session_factory(engine), now_utc, TradingCalendar.load(), stop)
        )
        yield
        stop.set()
        await refresher
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
    state.settings = settings
    state.strategies = load_strategies(settings.strategies_file)
    state.backtest_settings = load_settings(settings.backtest_file)
    state.idempotency = IdempotencyStore(metadata, state.sessions)
    state.enqueue_backtest = enqueue_with_celery  # tests replace it with a recorder
    state.publisher = EventPublisher(redis, profile, source=REPOSITORY, metrics=metrics)
    return app
