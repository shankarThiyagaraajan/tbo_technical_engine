from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import httpx

from tbo_technical_engine.clients import market_feed
from tbo_technical_engine.schemas.candle import FeedStatus, UniverseSnapshot
from tbo_technical_engine.services.candle_buffer import CandleBuffer
from tbo_technical_engine.services.readiness import Readiness
from tbo_technical_engine.services.supervisor import WarmupSupervisor
from tbo_technical_engine.services.universe import UniverseHolder
from tbo_technical_engine.services.warmup import WarmupSpec, is_warm
from tests.support import CALENDAR, ISIN_A, ISIN_B, FakeFeed, history, make_client

TODAY = date(2026, 10, 15)


class Rig:
    def __init__(self, now: datetime, *, isins: tuple[str, ...] = (ISIN_A, ISIN_B)) -> None:
        self.now = now
        self.feed = FakeFeed([c for i in (ISIN_A, ISIN_B) for tf in history(i, TODAY).values() for c in tf])
        self.buffer = CandleBuffer()
        self.readiness = Readiness()
        self.universe = UniverseHolder()
        if isins:
            self.universe.offer(UniverseSnapshot(trade_date="2026-10-15", universe_version=1, isins=isins))
        self.supervisor = WarmupSupervisor(
            self.feed.client(), self.buffer, self.readiness, self.universe, WarmupSpec(), CALENDAR, lambda: self.now
        )

    def candle_calls(self) -> int:
        return sum(1 for r in self.feed.calls if r.url.path == "/v1/candles")


IST_1000 = datetime(2026, 10, 15, 4, 30, tzinfo=UTC)


async def test_start_up_reads_the_feed_status_then_warms_everyone() -> None:
    rig = Rig(IST_1000)
    await rig.supervisor.tick()
    assert rig.readiness.active
    assert all(is_warm(rig.buffer, i, WarmupSpec()) for i in (ISIN_A, ISIN_B))


async def test_nothing_is_read_when_the_feed_is_not_healthy() -> None:
    rig = Rig(IST_1000)
    rig.feed.state = "down"
    await rig.supervisor.tick()
    assert rig.candle_calls() == 0 and not rig.readiness.active


async def test_nothing_happens_without_a_universe() -> None:
    rig = Rig(IST_1000, isins=())
    await rig.supervisor.tick()
    assert rig.feed.calls == []


async def test_nothing_happens_on_a_holiday_or_weekend() -> None:
    rig = Rig(datetime(2026, 10, 17, 4, 30, tzinfo=UTC))  # Saturday
    await rig.supervisor.tick()
    assert rig.feed.calls == []


async def test_a_quiet_tick_reads_nothing_more() -> None:
    rig = Rig(IST_1000)
    await rig.supervisor.tick()
    calls = len(rig.feed.calls)
    await rig.supervisor.tick()
    assert len(rig.feed.calls) == calls


async def test_a_stock_that_failed_is_retried_alone() -> None:
    rig = Rig(IST_1000)
    rig.feed.fail_isins = {ISIN_B}
    await rig.supervisor.tick()
    assert rig.readiness.active  # the engine runs; B stays silent
    assert not is_warm(rig.buffer, ISIN_B, WarmupSpec())
    rig.feed.fail_isins = set()
    rig.feed.calls.clear()
    await rig.supervisor.tick()
    assert is_warm(rig.buffer, ISIN_B, WarmupSpec())
    assert {r.url.params["isin"] for r in rig.feed.calls if r.url.path == "/v1/candles"} == {ISIN_B}


async def test_recovery_from_an_outage_warms_everyone_again() -> None:
    rig = Rig(IST_1000)
    await rig.supervisor.tick()
    rig.readiness.on_feed_status(FeedStatus(state="down", changed_at=IST_1000))
    rig.readiness.on_feed_status(FeedStatus(state="healthy", changed_at=IST_1000))
    assert not rig.readiness.active
    rig.feed.calls.clear()
    await rig.supervisor.tick()
    assert rig.readiness.active
    assert {r.url.params["isin"] for r in rig.feed.calls if r.url.path == "/v1/candles"} == {ISIN_A, ISIN_B}


async def test_an_outage_during_the_warm_up_leaves_the_engine_inactive() -> None:
    rig = Rig(IST_1000)
    original = rig.feed.handle

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/candles":
            rig.readiness.on_feed_status(FeedStatus(state="down", changed_at=IST_1000))
        return original(request)

    transport = httpx.MockTransport(handle)
    rig.supervisor.client = market_feed.MarketFeedClient(
        make_client(market_feed.AUDIENCE, market_feed.SCOPES, transport)
    )
    rig.readiness.on_feed_status(FeedStatus(state="healthy", changed_at=IST_1000))
    await rig.supervisor.tick()
    assert not rig.readiness.active


async def test_each_session_starts_with_a_fresh_warm_up_at_nine() -> None:
    rig = Rig(datetime(2026, 10, 15, 3, 29, tzinfo=UTC))  # 08:59 IST
    rig.readiness.on_feed_status(FeedStatus(state="healthy", changed_at=rig.now))
    await rig.supervisor.tick()
    assert rig.readiness.active  # the start-up warm-up
    rig.feed.calls.clear()
    await rig.supervisor.tick()
    assert rig.feed.calls == []  # 08:59: still the same warm-up
    rig.now += timedelta(minutes=2)  # 09:01
    await rig.supervisor.tick()
    assert rig.candle_calls() > 0 and rig.readiness.active
    rig.feed.calls.clear()
    await rig.supervisor.tick()
    assert rig.feed.calls == []  # only once a day
