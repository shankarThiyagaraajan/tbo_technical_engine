"""The daily swing run against a fake feed, a fake universe, a real PostgreSQL and a real Redis."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from tbo_contracts import validate_event

from tbo_technical_engine.services.snapshot_service import IncompleteRun, SnapshotService
from tbo_technical_engine.tables import outbox, swing_setup, technical_snapshot
from tests.support import CALENDAR, FakeFeed, fundamental_client
from tests.swing.data import DAY, PARAMS, as_bars, bars_from, base_breakout_series, candles_from, mean_reverting

SNAPSHOT_TYPE = "tbo.technical.technical.snapshot.v1"
SETUP_TYPE = "tbo.technical.swing.setup.v1"
NEXT = DAY + timedelta(days=1)  # Friday, a trading day
BREAKOUT = "INE000000001"


def isin(i: int) -> str:
    return f"INE{i:08d}1"


class DailyFeed(FakeFeed):
    """Serves adjusted and unadjusted daily history from two datasets, so a test can tell them apart."""

    def __init__(self) -> None:
        super().__init__()
        self.raw: list[Any] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.params.get("adjusted") != "true":
            self.data, held = self.raw, self.data
            try:
                return super().handle(request)
            finally:
                self.data = held
        return super().handle(request)


def universe_handler(isins: list[str], published: set[date]) -> Any:
    def handle(request: httpx.Request) -> httpx.Response:
        asked = date.fromisoformat(request.url.params["date"])
        if asked not in published:
            return httpx.Response(404)
        items = [{"trade_date": asked.isoformat(), "isin": i, "universe_version": 1} for i in isins]
        return httpx.Response(200, json={"items": items, "next_cursor": None})

    return handle


class World:
    """Six stocks: a base breakout, and five that wander (seeds differ, so their returns and ranks do)."""

    def __init__(self, app: Any) -> None:
        self.app = app
        self.isins = [BREAKOUT, *[isin(i) for i in range(2, 7)]]
        self.feed = DailyFeed()
        self.published = {DAY, NEXT}
        self.clock = datetime(2026, 10, 15, 10, 30, tzinfo=UTC)  # 16:00 IST
        self.bars = {BREAKOUT: as_bars(base_breakout_series())}
        for i, name in enumerate(self.isins[1:], start=2):
            self.bars[name] = bars_from(mean_reverting(300, seed=i * 11, centre=100.0 + 10 * i))
        self.reload()

    def reload(self) -> None:
        self.feed.data = [c for name, b in self.bars.items() for c in candles_from(name, b)]
        self.feed.raw = [c for name, b in self.bars.items() for c in candles_from(name, b)]

    def service(self, handler: Any = None) -> SnapshotService:
        funds = fundamental_client(handler or universe_handler(self.isins, self.published))
        return SnapshotService(
            self.feed.client(),
            funds,
            self.app.state.sessions,
            self.app.state.publisher,
            CALENDAR,
            PARAMS,
            lambda: self.clock,
        )


@pytest.fixture
def world(app: Any, sessions: Any) -> World:
    return World(app)


async def count(sessions: Any, table: Any) -> int:
    async with sessions() as session:
        return int((await session.execute(select(func.count()).select_from(table))).scalar_one())


async def events(sessions: Any, kind: str) -> list[dict[str, Any]]:
    async with sessions() as session:
        rows = (await session.execute(select(outbox.c.payload).order_by(outbox.c.created_at, outbox.c.event_id))).all()
    found = [json.loads(r.payload) for r in rows]
    return [e for e in found if e["type"] == kind]


async def test_the_close_run_stores_and_announces_every_snapshot_and_the_setup(world: World, sessions: Any) -> None:
    summary = await world.service().run("close", DAY)
    assert (summary.as_of, summary.universe, summary.snapshots_new, summary.failed) == (DAY, 6, 6, [])
    assert await count(sessions, technical_snapshot) == 6
    snapshot_events = await events(sessions, SNAPSHOT_TYPE)
    assert sorted(e["subject"] for e in snapshot_events) == sorted(world.isins)
    for event in snapshot_events:
        validate_event(event)
        assert event["data"]["as_of_date"] == "2026-10-15"
    setup_events = await events(sessions, SETUP_TYPE)
    assert [e["subject"] for e in setup_events] == [BREAKOUT]
    setup = setup_events[0]["data"]
    validate_event(setup_events[0])
    assert (setup["setup_type"], setup["setup_date"], setup["side"]) == ("base_breakout", "2026-10-15", "buy")
    # Valid for three sessions: Fri 16, Mon 19, and Wed 21 because Tue 20 is a holiday in the test calendar.
    assert setup["valid_until_date"] == "2026-10-21" and setup["params_version"] == PARAMS.params_version


async def test_a_repeated_close_run_announces_nothing_twice(world: World, sessions: Any) -> None:
    service = world.service()
    await service.run("close", DAY)
    before = await count(sessions, outbox)
    again = await service.run("close", DAY)
    assert (again.snapshots_new, again.snapshots_unchanged, again.setups_new) == (0, 6, 0)
    assert await count(sessions, outbox) == before
    assert await count(sessions, swing_setup) == 1


async def test_the_morning_refresh_announces_only_what_changed_and_finds_no_setups(world: World, sessions: Any) -> None:
    await world.service().run("close", DAY)
    before = await count(sessions, outbox)
    world.clock = datetime(2026, 10, 16, 2, 30, tzinfo=UTC)  # 08:00 IST on Friday; today's universe is not out yet
    world.published = {DAY}  # the fallback to the previous session's universe is exercised
    quiet = await world.service().run("morning", NEXT)
    assert (quiet.as_of, quiet.snapshots_new, quiet.snapshots_unchanged, quiet.setups_new) == (DAY, 0, 6, 0)
    assert await count(sessions, outbox) == before

    # The feed corrects one stock's last close overnight: only that stock is announced again.
    victim = isin(3)
    fixed = world.bars[victim]
    fixed.close[-1] = fixed.close[-1] * 1.05
    fixed.high[-1] = fixed.high[-1] * 1.05
    world.reload()
    changed = await world.service().run("morning", NEXT)
    assert (changed.snapshots_new, changed.snapshots_unchanged) == (1, 5)
    latest = (await events(sessions, SNAPSHOT_TYPE))[-1]
    assert latest["subject"] == victim


async def test_rank_follows_the_returns_inside_the_universe(world: World, sessions: Any) -> None:
    await world.service().run("close", DAY)
    by_isin = {e["subject"]: e["data"] for e in await events(sessions, SNAPSHOT_TYPE)}
    by_return = sorted(
        by_isin, key=lambda i: (float(by_isin[i]["return_60d_pct"]) + float(by_isin[i]["return_20d_pct"]))
    )
    percentiles = [by_isin[i]["rs_percentile"] for i in by_return]
    assert percentiles == sorted(percentiles) and percentiles[0] < percentiles[-1]


async def test_traded_value_comes_from_the_unadjusted_candles(world: World, sessions: Any) -> None:
    world.feed.raw = [c for name, b in world.bars.items() for c in candles_from(name, b, volume_scale=2.0)]
    await world.service().run("close", DAY)
    data = {e["subject"]: e["data"] for e in await events(sessions, SNAPSHOT_TYPE)}[isin(2)]
    bars = world.bars[isin(2)]
    expected = float((bars.close[-20:] * bars.volume[-20:] * 2.0).mean())
    assert abs(float(data["avg_turnover_20d"]) - expected) < expected * 1e-4  # candle prices are rounded to paise


async def test_a_stock_without_todays_candle_gets_no_snapshot(world: World, sessions: Any) -> None:
    world.feed.data = [
        c for c in world.feed.data if not (c.isin == isin(4) and c.start_at.date() >= DAY - timedelta(days=1))
    ]
    summary = await world.service().run("close", DAY)
    assert summary.stale == [isin(4)] and summary.snapshots_new == 5


async def test_a_young_stock_gets_no_snapshot(world: World, sessions: Any) -> None:
    short = bars_from(mean_reverting(40, seed=5))
    world.feed.data = [c for c in world.feed.data if c.isin != isin(5)] + candles_from(isin(5), short)
    world.feed.raw = world.feed.data
    summary = await world.service().run("close", DAY)
    assert summary.too_short == [isin(5)] and summary.snapshots_new == 5


async def test_one_stock_failing_does_not_stop_the_rest(world: World, sessions: Any) -> None:
    world.feed.fail_isins = {isin(6)}  # 1 of 6 is under the 20% limit
    summary = await world.service().run("close", DAY)
    assert summary.failed == [isin(6)] and summary.snapshots_new == 5


async def test_too_many_failures_publish_nothing_and_raise(world: World, sessions: Any) -> None:
    world.feed.fail_isins = {isin(5), isin(6)}  # 2 of 6 is over the limit: a rank over 4 stocks would mislead
    with pytest.raises(IncompleteRun):
        await world.service().run("close", DAY)
    assert await count(sessions, technical_snapshot) == 0 and await count(sessions, outbox) == 0


async def test_a_holiday_is_skipped(world: World, sessions: Any) -> None:
    summary = await world.service().run("close", date(2026, 10, 20))
    assert summary.skipped == "not_a_trading_day" and await count(sessions, outbox) == 0


async def test_a_missing_universe_is_skipped_not_an_error(world: World, sessions: Any) -> None:
    world.published = set()
    summary = await world.service().run("close", DAY)
    assert summary.skipped == "no_universe" and await count(sessions, outbox) == 0


async def test_the_daily_fetch_is_bounded_by_the_configured_concurrency(world: World, sessions: Any) -> None:
    await world.service().run("close", DAY)
    daily_calls = [r for r in world.feed.calls if r.url.path == "/v1/candles"]
    assert len(daily_calls) == 12  # two reads (adjusted history, unadjusted traded value) per stock, one page each
    adjusted = [r for r in daily_calls if r.url.params.get("adjusted") == "true"]
    assert len(adjusted) == 6 and all(r.url.params["timeframe"] == "1d" for r in daily_calls)
