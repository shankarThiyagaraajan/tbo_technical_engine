from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from tbo_contracts import validate
from tbo_platform_kit.testing import TestKeys

from tbo_technical_engine.config.settings import REPOSITORY
from tbo_technical_engine.store import signals as store
from tests.store.test_signal_store import ID, START, candidate
from tests.support import ISIN_A


@pytest.fixture
def read(keys: TestKeys) -> dict[str, str]:
    return keys.bearer("tbo_platform", REPOSITORY, ["technical:read"])


@pytest.fixture
async def stored(sessions: Any) -> list[str]:
    ids = [f"01JA8ZQ3W7K4N2C9V5T6B1M30{c}" for c in "ABC"]
    async with sessions() as session, session.begin():
        for i, signal_id in enumerate(ids):
            await store.save(session, candidate(signal_id, minutes=5 * i))
    return ids


async def test_get_one(client: httpx.AsyncClient, read: dict[str, str], stored: list[str]) -> None:
    answer = await client.get(f"/v1/signals/{stored[0]}", headers=read)
    assert answer.status_code == 200
    body = answer.json()
    validate("technical/signal_candidate.v1.json", body)
    assert body["strategy"] == "orb_15m" and body["candle_start_at"] == "2026-10-15T04:25:00Z"


async def test_get_unknown_and_malformed(client: httpx.AsyncClient, read: dict[str, str], stored: list[str]) -> None:
    assert (await client.get(f"/v1/signals/{ID[:-1]}Z", headers=read)).status_code == 404
    assert (await client.get("/v1/signals/not-an-id", headers=read)).status_code == 422


async def test_list_a_day_newest_first_and_page_through_it(
    client: httpx.AsyncClient, read: dict[str, str], stored: list[str]
) -> None:
    day = START.astimezone(UTC).date().isoformat()
    first = await client.get("/v1/signals", params={"date": day, "limit": 2}, headers=read)
    assert first.status_code == 200
    body = first.json()
    assert [c["signal_id"] for c in body["items"]] == [stored[2], stored[1]]
    assert body["next_cursor"]
    for item in body["items"]:
        validate("technical/signal_candidate.v1.json", item)
    second = await client.get(
        "/v1/signals", params={"date": day, "limit": 2, "cursor": body["next_cursor"]}, headers=read
    )
    assert [c["signal_id"] for c in second.json()["items"]] == [stored[0]]
    assert second.json()["next_cursor"] is None


async def test_list_filters_and_default_day(client: httpx.AsyncClient, read: dict[str, str], stored: list[str]) -> None:
    day = START.date().isoformat()
    assert (await client.get("/v1/signals", params={"date": day, "strategy": "vwap_pivot"}, headers=read)).json()[
        "items"
    ] == []
    got = await client.get("/v1/signals", params={"date": day, "isin": ISIN_A}, headers=read)
    assert len(got.json()["items"]) == 3


async def test_list_without_a_date_is_today_in_ist(
    client: httpx.AsyncClient, read: dict[str, str], sessions: Any
) -> None:
    now = datetime.now(UTC)
    made = candidate("01JA8ZQ3W7K4N2C9V5T6B1M30Z", strategy="vwap_pivot").model_copy(update={"candle_start_at": now})
    old = candidate("01JA8ZQ3W7K4N2C9V5T6B1M30Y", strategy="vwap_pivot").model_copy(
        update={"candle_start_at": now - timedelta(days=3)}
    )
    async with sessions() as session, session.begin():
        await store.save(session, made)
        await store.save(session, old)
    got = await client.get("/v1/signals", params={"strategy": "vwap_pivot"}, headers=read)
    assert [c["signal_id"] for c in got.json()["items"]] == [made.signal_id]


async def test_list_rejects_bad_input(client: httpx.AsyncClient, read: dict[str, str]) -> None:
    assert (await client.get("/v1/signals", params={"isin": "nope"}, headers=read)).status_code == 422
    assert (await client.get("/v1/signals", params={"cursor": "%%%"}, headers=read)).status_code == 422
    assert (await client.get("/v1/signals", params={"limit": 0}, headers=read)).status_code == 422


async def test_strategies(client: httpx.AsyncClient, read: dict[str, str]) -> None:
    answer = await client.get("/v1/strategies", headers=read)
    assert answer.status_code == 200
    items = answer.json()["strategies"]
    assert [s["strategy"] for s in items] == sorted(s["strategy"] for s in items)
    assert len(items) == 5
    for item in items:
        validate("technical/strategy.v1.json", item)
    assert {s["params_version"] for s in items} == {"2026.10.1"}
