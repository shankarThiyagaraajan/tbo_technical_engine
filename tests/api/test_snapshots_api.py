"""GET /v1/snapshots, /v1/snapshots/{isin} and /v1/swing-setups."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest
from tbo_contracts import validate
from tbo_platform_kit.testing import TestKeys

from tbo_technical_engine.config.settings import REPOSITORY
from tbo_technical_engine.schemas.swing import Setup
from tbo_technical_engine.store import snapshots as snapshot_store
from tbo_technical_engine.store import swing_setups as setup_store
from tbo_technical_engine.swing import analysis
from tbo_technical_engine.utils.ids import setup_id
from tests.swing.data import PARAMS, bars_from, mean_reverting

NOW = datetime(2026, 10, 15, 10, 30, tzinfo=UTC)
D1, D2 = date(2026, 10, 14), date(2026, 10, 15)
ISINS = ["INE000000021", "INE000000031", "INE000000041", "INE000000051"]


@pytest.fixture
def read(keys: TestKeys) -> dict[str, str]:
    return keys.bearer("tbo_platform", REPOSITORY, ["technical:read"])


def snap(isin: str, as_of: date, seed: int) -> Any:
    bars = bars_from(mean_reverting(300, seed=seed), end=as_of)
    measured = analysis.measure(isin, bars, PARAMS)
    assert measured is not None
    return analysis.snapshot(measured, 50, 1e9, "2026-10-15T10:30:00Z")


def setup(isin: str, day: date, kind: str = "base_breakout") -> Setup:
    return Setup(
        setup_id=setup_id(isin, kind, day, NOW),
        isin=isin,
        setup_date=day,
        setup_type=kind,
        side="buy",
        entry=Decimal("105.00"),
        stop=Decimal("97.90"),
        target_1=Decimal("115.60"),
        target_2=Decimal("126.25"),
        valid_until_date=date(2026, 10, 21),
        params_version="2026.10.1",
        created_at=NOW,
    )


@pytest.fixture
async def stored(sessions: Any) -> None:
    async with sessions() as session, session.begin():
        for seed, name in enumerate(ISINS, start=1):
            await snapshot_store.upsert(session, snap(name, D1, seed), NOW)
            await snapshot_store.upsert(session, snap(name, D2, seed + 10), NOW)
        await setup_store.save(session, setup(ISINS[1], D1))
        await setup_store.save(session, setup(ISINS[0], D2, "ema_pullback"))
        await setup_store.save(session, setup(ISINS[2], D2))


async def test_without_a_date_the_latest_session_serves(
    client: httpx.AsyncClient, read: dict[str, str], stored: None
) -> None:
    answer = await client.get("/v1/snapshots", headers=read)
    assert answer.status_code == 200
    items = answer.json()["items"]
    assert [i["isin"] for i in items] == ISINS and {i["as_of_date"] for i in items} == {"2026-10-15"}
    for item in items:
        validate("technical/technical_snapshot.v1.json", item)


async def test_the_date_is_the_trade_date_served_so_it_gets_the_previous_sessions_snapshot(
    client: httpx.AsyncClient, read: dict[str, str], stored: None
) -> None:
    # Trade date 15 Oct is served by the snapshot built after the 14th; the 16th by the one built after the 15th.
    served = {
        day: {
            i["as_of_date"]
            for i in (await client.get("/v1/snapshots", params={"date": day}, headers=read)).json()["items"]
        }
        for day in ("2026-10-15", "2026-10-16", "2026-10-20")
    }
    assert served == {"2026-10-15": {"2026-10-14"}, "2026-10-16": {"2026-10-15"}, "2026-10-20": {"2026-10-15"}}


async def test_no_snapshot_before_the_date_is_an_empty_page(
    client: httpx.AsyncClient, read: dict[str, str], stored: None
) -> None:
    answer = await client.get("/v1/snapshots", params={"date": "2026-10-14"}, headers=read)
    assert answer.status_code == 200 and answer.json() == {"items": [], "next_cursor": None}


async def test_nothing_stored_is_an_empty_page(client: httpx.AsyncClient, read: dict[str, str], sessions: Any) -> None:
    assert (await client.get("/v1/snapshots", headers=read)).json() == {"items": [], "next_cursor": None}
    assert (await client.get("/v1/swing-setups", headers=read)).json() == {"items": [], "next_cursor": None}


async def test_pages_follow_the_isin_order_without_gaps(
    client: httpx.AsyncClient, read: dict[str, str], stored: None
) -> None:
    seen: list[str] = []
    cursor = None
    for _ in range(5):
        params: dict[str, Any] = {"limit": 3, **({"cursor": cursor} if cursor else {})}
        body = (await client.get("/v1/snapshots", params=params, headers=read)).json()
        seen += [i["isin"] for i in body["items"]]
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert seen == ISINS


async def test_a_bad_cursor_is_422(client: httpx.AsyncClient, read: dict[str, str], stored: None) -> None:
    assert (await client.get("/v1/snapshots", params={"cursor": "not-a-cursor"}, headers=read)).status_code == 422


async def test_one_snapshot_by_isin(client: httpx.AsyncClient, read: dict[str, str], stored: None) -> None:
    answer = await client.get(f"/v1/snapshots/{ISINS[2]}", headers=read)
    assert (
        answer.status_code == 200 and answer.json()["isin"] == ISINS[2] and answer.json()["as_of_date"] == "2026-10-15"
    )
    validate("technical/technical_snapshot.v1.json", answer.json())
    older = await client.get(f"/v1/snapshots/{ISINS[2]}", params={"date": "2026-10-15"}, headers=read)
    assert older.json()["as_of_date"] == "2026-10-14"


async def test_an_unknown_stock_is_404_and_a_bad_isin_422(
    client: httpx.AsyncClient, read: dict[str, str], stored: None
) -> None:
    assert (await client.get("/v1/snapshots/INE999999999", headers=read)).status_code == 404
    assert (await client.get("/v1/snapshots/nonsense", headers=read)).status_code == 422


async def test_setups_default_to_the_latest_session_and_filter_by_date(
    client: httpx.AsyncClient, read: dict[str, str], stored: None
) -> None:
    latest = (await client.get("/v1/swing-setups", headers=read)).json()["items"]
    assert [(s["isin"], s["setup_type"]) for s in latest] == [(ISINS[0], "ema_pullback"), (ISINS[2], "base_breakout")]
    for item in latest:
        validate("technical/swing_setup.v1.json", item)
    older = (await client.get("/v1/swing-setups", params={"date": "2026-10-14"}, headers=read)).json()["items"]
    assert [s["isin"] for s in older] == [ISINS[1]]
    assert (await client.get("/v1/swing-setups", params={"date": "2026-10-13"}, headers=read)).json()["items"] == []


async def test_a_second_setup_for_the_same_stock_and_session_is_refused_by_the_database(sessions: Any) -> None:
    async with sessions() as session, session.begin():
        assert await setup_store.save(session, setup(ISINS[0], D2))
        assert not await setup_store.save(session, setup(ISINS[0], D2, "week_52_high_breakout"))


async def test_the_scope_is_enforced(client: httpx.AsyncClient, keys: TestKeys) -> None:
    for path in ("/v1/snapshots", f"/v1/snapshots/{ISINS[0]}", "/v1/swing-setups"):
        assert (await client.get(path)).status_code == 401
        wrong = keys.bearer("tbo_platform", REPOSITORY, ["technical:research"])
        assert (await client.get(path, headers=wrong)).status_code == 403


async def test_a_partial_latest_session_does_not_hide_the_last_complete_one(
    client: httpx.AsyncClient, read: dict[str, str], stored: None, sessions: Any
) -> None:
    """9.4: a run with a late feed stored 1 of 4 snapshots for the 16th; the 15th keeps serving."""
    d3 = date(2026, 10, 16)
    async with sessions() as session, session.begin():
        await snapshot_store.upsert(session, snap(ISINS[0], d3, 9), NOW)
    items = (await client.get("/v1/snapshots", headers=read)).json()["items"]
    assert len(items) == 4 and {i["as_of_date"] for i in items} == {"2026-10-15"}
