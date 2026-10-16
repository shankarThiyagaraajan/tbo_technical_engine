"""POST /v1/backtests and its results: caps, idempotency, scopes, and a real run over a filled research cache."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from tbo_platform_kit.ids import new_ulid
from tbo_platform_kit.testing import TestKeys

from tbo_technical_engine.config.settings import REPOSITORY
from tests.backtest.data import FIRST, LAST, filled_cache, runner
from tests.support import ISIN_A

BODY: dict[str, Any] = {
    "strategy": "vwap_pivot",
    "from_date": FIRST.isoformat(),
    "to_date": LAST.isoformat(),
    "isins": [ISIN_A],
}


@pytest.fixture
def research(keys: TestKeys) -> dict[str, str]:
    return keys.bearer("tbo_platform", REPOSITORY, ["technical:research"])


@pytest.fixture
def sent(app: Any, sessions: Any) -> list[str]:
    """The jobs the API sent to the queue (a recorder instead of Celery)."""
    jobs: list[str] = []
    app.state.enqueue_backtest = jobs.append
    return jobs


def post(
    client: httpx.AsyncClient, headers: dict[str, str], body: dict[str, Any] = BODY, key: str | None = None
) -> Any:
    return client.post("/v1/backtests", json=body, headers={**headers, "Idempotency-Key": key or new_ulid()})


async def test_a_request_is_queued_and_sent_once(
    client: httpx.AsyncClient, research: dict[str, str], sessions: Any, sent: list[str]
) -> None:
    key = new_ulid()
    first = await post(client, research, key=key)
    assert first.status_code == 202
    body = first.json()
    assert body["status"] == "queued" and body["metrics"] is None and body["strategy"] == "vwap_pivot"
    assert body["isins"] == [ISIN_A] and body["params_version"] == "2026.10.1" and body["error"] is None
    assert first.headers["Location"] == f"/v1/backtests/{body['backtest_id']}"
    assert sent == [body["backtest_id"]]

    again = await post(client, research, key=key)  # the same request: the same answer, not a second job
    assert again.json() == body and again.headers["Idempotent-Replayed"] == "true" and sent == [body["backtest_id"]]
    assert (await post(client, research, {**BODY, "strategy": "orb_15m"}, key=key)).status_code == 409


async def test_a_finished_run_is_served_with_metrics_and_paged_trades(
    client: httpx.AsyncClient, research: dict[str, str], sessions: Any, sent: list[str], app: Any, tmp_path: Path
) -> None:
    created = (await post(client, research)).json()
    backtest_id = created["backtest_id"]
    assert await runner(sessions, filled_cache(tmp_path)).execute(backtest_id) == "succeeded"

    done = (await client.get(f"/v1/backtests/{backtest_id}", headers=research)).json()
    assert done["status"] == "succeeded" and done["started_at"] and done["finished_at"] and done["error"] is None
    metrics = done["metrics"]
    assert set(metrics) == {"trade_count", "win_rate_pct", "avg_r", "expectancy_r", "max_drawdown_r", "profit_factor"}
    assert metrics["trade_count"] > 3

    first = (await client.get(f"/v1/backtests/{backtest_id}/trades?limit=2", headers=research)).json()
    assert [t["trade_no"] for t in first["items"]] == [1, 2] and first["next_cursor"]
    rest = (
        await client.get(f"/v1/backtests/{backtest_id}/trades?cursor={first['next_cursor']}", headers=research)
    ).json()
    assert rest["items"][0]["trade_no"] == 3 and rest["next_cursor"] is None
    assert len(first["items"]) + len(rest["items"]) == metrics["trade_count"]
    trade = first["items"][0]
    assert set(trade) == {
        "backtest_id", "trade_no", "isin", "side", "entry_at", "exit_at",
        "entry_price", "exit_price", "exit_reason", "r_multiple",
    }  # fmt: skip
    assert trade["entry_at"].endswith("Z") and trade["isin"] == ISIN_A


async def test_trades_are_empty_until_the_backtest_has_run(
    client: httpx.AsyncClient, research: dict[str, str], sent: list[str]
) -> None:
    backtest_id = (await post(client, research)).json()["backtest_id"]
    page = (await client.get(f"/v1/backtests/{backtest_id}/trades", headers=research)).json()
    assert page == {"items": [], "next_cursor": None}


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"to_date": "2027-12-31"}, "to_date"),  # longer than max_range_days
        ({"from_date": "2026-10-10", "to_date": "2026-10-01"}, "to_date"),
        ({"strategy": "no_such"}, "strategy"),
        ({"params_overrides": [{"name": "typo", "value": "1"}]}, "params_overrides.typo"),
        ({"params_version": "1999.01.1"}, "params_version"),
        ({"isins": ["INE002A01018"] * 2}, "isins"),
        ({"isins": ["not-an-isin"]}, "isins"),
    ],
)
async def test_a_request_over_the_caps_or_wrong_is_refused_with_its_field(
    client: httpx.AsyncClient, research: dict[str, str], sent: list[str], change: dict[str, Any], field: str
) -> None:
    answer = await post(client, research, {**BODY, **change})
    assert answer.status_code == 422 and answer.headers["content-type"].startswith("application/problem+json")
    assert any(e["field"].endswith(field) for e in answer.json()["errors"]), answer.json()
    assert sent == []


async def test_too_many_stocks_for_the_range_is_refused(
    client: httpx.AsyncClient, research: dict[str, str], sent: list[str]
) -> None:
    # 400 days x 101 ISINs = 40,400 > max_stock_days
    isins = [f"INE{i:08d}{i % 10}" for i in range(101)]
    body = {**BODY, "from_date": "2025-10-01", "to_date": "2026-11-04", "isins": isins}
    answer = await post(client, research, body)
    assert answer.status_code == 422 and "ISINs x days" in str(answer.json()["errors"])


async def test_more_than_the_concurrent_cap_is_a_429_with_retry_after(
    client: httpx.AsyncClient, research: dict[str, str], sent: list[str]
) -> None:
    assert (await post(client, research)).status_code == 202
    assert (await post(client, research)).status_code == 202
    third = await post(client, research)
    assert third.status_code == 429 and third.headers["Retry-After"] == "300"
    assert len(sent) == 2


async def test_scope_idempotency_key_and_unknown_ids(
    client: httpx.AsyncClient, research: dict[str, str], keys: TestKeys, sent: list[str]
) -> None:
    read = keys.bearer("tbo_platform", REPOSITORY, ["technical:read"])
    assert (await post(client, read)).status_code == 403
    assert (await client.post("/v1/backtests", json=BODY, headers=research)).status_code == 428
    unknown = "01J00000000000000000000000"
    assert (await client.get(f"/v1/backtests/{unknown}", headers=research)).status_code == 404
    assert (await client.get("/v1/backtests/not-an-id", headers=research)).status_code == 404
    assert (await client.get(f"/v1/backtests/{unknown}/trades", headers=research)).status_code == 404
    assert (await client.get(f"/v1/backtests/{unknown}", headers=read)).status_code == 403
    bad_cursor = await client.get(f"/v1/backtests/{unknown}/trades?cursor=zzz", headers=research)
    assert bad_cursor.status_code == 422
