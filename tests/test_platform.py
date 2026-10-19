"""The platform interface: health, metrics, the served contract, and the routes matching the contract."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import httpx
from tbo_platform_kit.contract import route_mismatches, served_differences
from tbo_platform_kit.events import EventPublisher

ROOT = Path(__file__).resolve().parents[1]


async def test_health(client: httpx.AsyncClient) -> None:
    assert (await client.get("/health/live")).json() == {"status": "ok"}
    ready = await client.get("/health/ready")
    assert ready.status_code == 200, ready.text


async def test_metrics(client: httpx.AsyncClient) -> None:
    await client.get("/health/live")
    body = (await client.get("/metrics")).text
    assert "tbo_technical_" in body


async def test_serves_the_vendored_contract_byte_for_byte(client: httpx.AsyncClient) -> None:
    served = await client.get("/openapi.json")
    assert served.content == (ROOT / "contract" / "openapi.json").read_bytes()


async def test_served_openapi_is_the_released_contract(client: httpx.AsyncClient) -> None:
    """What the running app serves equals the contract in the released tbo-contracts package (CT5)."""
    served = (await client.get("/openapi.json")).json()
    assert served_differences(served, "tbo_technical_engine") == []


def test_routes_are_exactly_the_contract(app: Any) -> None:
    doc = json.loads((ROOT / "contract" / "openapi.json").read_text())
    assert route_mismatches(app, doc) == []


async def test_unknown_route_is_a_problem(client: httpx.AsyncClient) -> None:
    r = await client.get("/v1/nothing-here")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["code"] == "not_found"


async def test_one_row_redis_refuses_does_not_block_the_outbox(app: Any, redis: Any) -> None:
    """A poison row (here: a payload Redis refuses) is marked and skipped; the rows behind it are published, and
    after MAX_ATTEMPTS it is left behind with dead_at set and never retried by the sweep."""
    from redis.exceptions import ResponseError
    from sqlalchemy import select

    from tbo_technical_engine import outbox
    from tbo_technical_engine.store.tables import outbox as outbox_table

    publisher = app.state.publisher
    good = publisher.build(
        "tbo.template.widget.created.v1",
        subject="INE002A01018",
        data={"widget_id": "01JA8ZQ3W7K4N2C9V5T6B1M0XE", "isin": "INE002A01018", "created_at": "2026-10-15T03:00:00Z"},
        dataschema="template/widget_created.v1.json",
    )
    poison = {**good, "id": "01JA8ZQ3W7K4N2C9V5T6B1M0XF"}
    async with app.state.sessions() as session, session.begin():
        await outbox.add(session, poison)
        await outbox.add(session, good)

    class RefusingPublisher:
        async def publish(self, stream: str, event: dict[str, Any]) -> str:
            if event["id"] == poison["id"]:
                raise ResponseError("NOPERM this user has no permissions")  # what redis-py raises for an ACL refusal
            return str(await publisher.publish(stream, event))

    assert await outbox.flush(app.state.sessions, cast(EventPublisher, RefusingPublisher())) == 1
    async with app.state.sessions() as session:
        rows = {r.event_id: r for r in (await session.execute(select(outbox_table))).all()}
    assert rows[good["id"]].published_at is not None
    assert rows[poison["id"]].published_at is None and rows[poison["id"]].attempts == 1
    assert rows[poison["id"]].last_error.startswith("ResponseError")
    for _ in range(outbox.MAX_ATTEMPTS - 1):
        await outbox.flush(app.state.sessions, cast(EventPublisher, RefusingPublisher()))
    async with app.state.sessions() as session:
        left = (await session.execute(select(outbox_table).where(outbox_table.c.event_id == poison["id"]))).one()
    assert left.attempts == outbox.MAX_ATTEMPTS and left.dead_at is not None
    await outbox.flush(app.state.sessions, cast(EventPublisher, RefusingPublisher()))  # left behind: not touched again
    async with app.state.sessions() as session:
        again = (await session.execute(select(outbox_table).where(outbox_table.c.event_id == poison["id"]))).one()
    assert again.attempts == outbox.MAX_ATTEMPTS
    # The request path never fails on this: the sweep is the guarantee.
    await outbox.flush_after_commit(app.state.sessions, cast(EventPublisher, RefusingPublisher()))
