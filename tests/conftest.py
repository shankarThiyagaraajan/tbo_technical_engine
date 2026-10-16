"""Fixtures: a real PostgreSQL and Redis, the schema migrated with Alembic, the app with test token keys.

Set TBO_TEST_DATABASE_URL (postgresql+asyncpg://...) and TBO_TEST_REDIS_URL; `compose.dev.yml` starts both, and
CI provides them as service containers. Without them the tests are skipped locally and fail in CI.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import redis.asyncio as aioredis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from tbo_platform_kit.testing import TestKeys

from tbo_technical_engine.app import build_app
from tbo_technical_engine.config.settings import REPOSITORY, SHORT_NAME, Settings

ROOT = Path(__file__).resolve().parents[1]
PREFIX = f"TBO_{SHORT_NAME.upper()}_"


def _need(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        message = f"{name} is not set (docker compose -f compose.dev.yml up -d --wait; see compose.dev.yml)"
        if os.environ.get("CI"):
            pytest.fail(message)
        pytest.skip(message)
    return value


@pytest.fixture(scope="session")
def database_url() -> str:
    """The test database with schema `technical` dropped, recreated and migrated to head."""
    url = _need("TBO_TEST_DATABASE_URL")

    async def reset() -> None:
        engine = create_async_engine(url)
        async with engine.begin() as conn:
            await conn.execute(text(f"DROP SCHEMA IF EXISTS {SHORT_NAME} CASCADE"))
            await conn.execute(text(f"CREATE SCHEMA {SHORT_NAME}"))
            await conn.execute(text(f"SET search_path TO {SHORT_NAME}, public"))
        await engine.dispose()

    import asyncio

    asyncio.run(reset())
    env = {**os.environ, "TBO_MODE": "lab", f"{PREFIX}DATABASE_URL": url}
    migrated = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=env, capture_output=True, text=True
    )
    assert migrated.returncode == 0, migrated.stderr
    return url


@pytest.fixture(scope="session")
def redis_url() -> str:
    return _need("TBO_TEST_REDIS_URL")


@pytest.fixture(scope="session")
def keys() -> TestKeys:
    return TestKeys()


@pytest.fixture
def settings(database_url: str, redis_url: str, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("TBO_MODE", "lab")
    return Settings(database_url=database_url, redis_url=redis_url, contract_file=ROOT / "contract" / "openapi.json")


@pytest.fixture
async def redis(redis_url: str) -> AsyncIterator[Any]:
    client = aioredis.Redis.from_url(redis_url)
    await client.flushdb()
    yield client
    await client.aclose()


@pytest.fixture
async def app(settings: Settings, keys: TestKeys, redis: Any) -> AsyncIterator[Any]:
    application = build_app(settings, verifier=keys.verifier(REPOSITORY))
    yield application
    await application.state.redis.aclose()
    await application.state.engine.dispose()


@pytest.fixture
async def client(app: Any) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
async def sessions(app: Any) -> Any:
    """The app's session factory, with this component's tables emptied."""
    factory = app.state.sessions
    async with factory() as session, session.begin():
        tables = ("indicator_snapshot", "signal_candidate", "market_context", "outbox")
        await session.execute(text("TRUNCATE " + ", ".join(f"{SHORT_NAME}.{t}" for t in tables)))
    return factory
