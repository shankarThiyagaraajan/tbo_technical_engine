from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from tbo_contracts import validate
from tbo_platform_kit.testing import TestKeys

from tbo_technical_engine.config.settings import REPOSITORY
from tbo_technical_engine.schemas.context import Breadth, MarketContext
from tbo_technical_engine.store import context as store
from tbo_technical_engine.utils.timefmt import z

BASE = datetime(2026, 10, 15, 3, 45, tzinfo=UTC)  # 09:15 IST


@pytest.fixture
def read(keys: TestKeys) -> dict[str, str]:
    return keys.bearer("tbo_platform", REPOSITORY, ["technical:read"])


def context(at: datetime, advancing: int = 10) -> MarketContext:
    return MarketContext(
        context_at=z(at),
        trade_date="2026-10-15",
        index_states=[],
        vix=None,
        breadth=Breadth(advancing=advancing, declining=3, unchanged=1),
        cpr_class=None,
        data_quality="ok",
    )


@pytest.fixture
async def stored(sessions: Any) -> list[datetime]:
    times = [BASE + timedelta(minutes=5 * i) for i in range(5)]
    async with sessions() as session, session.begin():
        for i, at in enumerate(times):
            await store.save(session, at, context(at, advancing=i), at)
    return times


async def test_latest_is_the_newest(client: httpx.AsyncClient, read: dict[str, str], stored: list[datetime]) -> None:
    answer = await client.get("/v1/context", headers=read)
    assert answer.status_code == 200
    validate("technical/market_context.v1.json", answer.json())
    assert answer.json()["context_at"] == z(stored[-1]) and answer.json()["breadth"]["advancing"] == 4


async def test_latest_before_any_context_is_404(client: httpx.AsyncClient, read: dict[str, str], sessions: Any) -> None:
    assert (await client.get("/v1/context", headers=read)).status_code == 404


async def test_history_is_oldest_first_and_pages(
    client: httpx.AsyncClient, read: dict[str, str], stored: list[datetime]
) -> None:
    params: dict[str, str | int] = {"from": z(BASE), "to": z(BASE + timedelta(hours=1)), "limit": 2}
    first = (await client.get("/v1/context/history", params=params, headers=read)).json()
    assert [i["context_at"] for i in first["items"]] == [z(stored[0]), z(stored[1])] and first["next_cursor"]
    second = (
        await client.get("/v1/context/history", params={**params, "cursor": first["next_cursor"]}, headers=read)
    ).json()
    assert [i["context_at"] for i in second["items"]] == [z(stored[2]), z(stored[3])]
    third = (
        await client.get("/v1/context/history", params={**params, "cursor": second["next_cursor"]}, headers=read)
    ).json()
    assert [i["context_at"] for i in third["items"]] == [z(stored[4])] and third["next_cursor"] is None


async def test_history_range_is_half_open(
    client: httpx.AsyncClient, read: dict[str, str], stored: list[datetime]
) -> None:
    got = await client.get("/v1/context/history", params={"from": z(stored[1]), "to": z(stored[3])}, headers=read)
    assert [i["context_at"] for i in got.json()["items"]] == [z(stored[1]), z(stored[2])]


async def test_history_default_is_todays_session(
    client: httpx.AsyncClient, read: dict[str, str], sessions: Any
) -> None:
    now = datetime.now(UTC)
    async with sessions() as session, session.begin():
        await store.save(session, now, context(now), now)
        await store.save(session, now - timedelta(days=3), context(now - timedelta(days=3)), now)
    got = await client.get("/v1/context/history", headers=read)
    assert [i["context_at"] for i in got.json()["items"]] == [z(now)]


async def test_history_rejects_bad_ranges(client: httpx.AsyncClient, read: dict[str, str]) -> None:
    only_from = await client.get("/v1/context/history", params={"from": z(BASE)}, headers=read)
    assert only_from.status_code == 422
    backwards = await client.get("/v1/context/history", params={"from": z(BASE), "to": z(BASE)}, headers=read)
    assert backwards.status_code == 422
    bad = await client.get("/v1/context/history", params={"cursor": "%%%"}, headers=read)
    assert bad.status_code == 422
