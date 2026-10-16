from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from tbo_contracts import validate
from tbo_platform_kit.testing import TestKeys

from tbo_technical_engine.config.settings import REPOSITORY
from tbo_technical_engine.schemas.indicators import IndicatorSnapshot
from tbo_technical_engine.store import indicators as store
from tests.support import ISIN_A

START = datetime(2026, 10, 15, 4, 25, tzinfo=UTC)


@pytest.fixture
def read(keys: TestKeys) -> dict[str, str]:
    return keys.bearer("tbo_platform", REPOSITORY, ["technical:read"])


@pytest.fixture
async def loaded(sessions: Any) -> None:
    async with sessions() as session, session.begin():
        await store.save(
            session,
            IndicatorSnapshot(
                isin=ISIN_A,
                timeframe="5m",
                candle_start_at=START,
                values={"ema_9": "1247.2", "ema_21": "1243.85", "adx_14": None},
                computed_at=START,
            ),
        )


async def test_latest_values_match_the_contract(client: httpx.AsyncClient, read: dict[str, str], loaded: None) -> None:
    answer = await client.get(f"/v1/indicators/{ISIN_A}", headers=read)
    assert answer.status_code == 200
    body = answer.json()
    validate("technical/indicator_set.v1.json", body)
    assert body["values"]["ema_9"] == "1247.2" and body["values"]["adx_14"] is None
    assert body["candle_start_at"] == "2026-10-15T04:25:00Z"


async def test_names_limit_the_values(client: httpx.AsyncClient, read: dict[str, str], loaded: None) -> None:
    body = (await client.get(f"/v1/indicators/{ISIN_A}", params={"names": "ema_9,nope"}, headers=read)).json()
    assert body["values"] == {"ema_9": "1247.2"}


async def test_unknown_stock_or_timeframe_is_404(client: httpx.AsyncClient, read: dict[str, str], loaded: None) -> None:
    assert (await client.get("/v1/indicators/INE000000000", headers=read)).status_code == 404
    other = await client.get(f"/v1/indicators/{ISIN_A}", params={"timeframe": "15m"}, headers=read)
    assert other.status_code == 404 and other.json()["code"] == "not_found"


async def test_a_bad_isin_is_a_validation_problem(client: httpx.AsyncClient, read: dict[str, str]) -> None:
    answer = await client.get("/v1/indicators/not-an-isin", headers=read)
    assert answer.status_code == 422


async def test_needs_the_read_scope(client: httpx.AsyncClient, keys: TestKeys, loaded: None) -> None:
    assert (await client.get(f"/v1/indicators/{ISIN_A}")).status_code == 401
    research = keys.bearer("tbo_platform", REPOSITORY, ["technical:research"])
    assert (await client.get(f"/v1/indicators/{ISIN_A}", headers=research)).status_code == 403
