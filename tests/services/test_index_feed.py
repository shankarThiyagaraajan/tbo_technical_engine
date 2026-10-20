"""The market feed as the index source (ADR-019): events first, REST as the fallback, a signal for a missing index."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx

from tbo_technical_engine.clients import market_feed
from tbo_technical_engine.context import compute
from tbo_technical_engine.schemas.candle import IndexCandle
from tbo_technical_engine.services.index_feed import FeedIndexSource
from tbo_technical_engine.utils.timefmt import z
from tests.support import make_client, sample, session_start, spy_signals

DAY = date(2026, 10, 15)
YESTERDAY = date(2026, 10, 14)
OPEN = session_start(DAY)
MISSING = "tbo_technical_index_missing_indices"
READS = "tbo_technical_index_reads_requests_total"
LENGTH = {"5m": timedelta(minutes=5), "15m": timedelta(minutes=15), "1d": timedelta(days=1)}


def bar(
    code: str, timeframe: str, start: datetime, close: str, *, vwap: str | None = None, revision: int = 1
) -> IndexCandle:
    price = Decimal(close)
    return IndexCandle(
        index_code=code,
        timeframe=timeframe,
        start_at=start,
        open=price,
        high=price + 2,
        low=price - 2,
        close=price,
        session_vwap=Decimal(vwap) if vwap else None,
        revision=revision,
    )


def daily(code: str, day: date, close: str) -> IndexCandle:
    return bar(code, "1d", datetime(day.year, day.month, day.day, tzinfo=UTC) - timedelta(hours=5, minutes=30), close)


def json_of(c: IndexCandle) -> dict[str, Any]:
    return {**c.model_dump(mode="json"), "start_at": z(c.start_at)}


class IndexFeed:
    """GET /v1/indices and /v1/index-candles over a list of index candles; `down` answers 503 to everything."""

    def __init__(
        self, data: list[IndexCandle], tracked: tuple[str, ...] = ("nifty_50", "nifty_bank", "india_vix")
    ) -> None:
        self.data = data
        self.tracked = tracked
        self.down = False
        self.calls: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.down:
            return httpx.Response(503)
        if request.url.path == "/v1/indices":
            rows = [
                {"index_code": c, "name": c.upper(), "kind": "broad", "kite_instrument_id": 1, "is_tracked": True}
                for c in self.tracked
            ] + [{"index_code": "nifty_500", "name": "NIFTY 500", "kind": "broad", "is_tracked": False}]
            return httpx.Response(200, json={"indices": rows})
        q = request.url.params
        rows_ = sorted(
            (
                c
                for c in self.data
                if c.index_code == q["index_code"]
                and c.timeframe == q["timeframe"]
                and q["from"] <= z(c.start_at)
                and z(c.start_at + LENGTH[c.timeframe]) <= q["to"]
            ),
            key=lambda c: c.start_at,
        )
        return httpx.Response(200, json={"items": [json_of(c) for c in rows_], "next_cursor": None})

    def candle_calls(self) -> list[httpx.Request]:
        return [r for r in self.calls if r.url.path == "/v1/index-candles"]

    def client(self) -> market_feed.MarketFeedClient:
        return market_feed.MarketFeedClient(
            make_client(market_feed.AUDIENCE, market_feed.SCOPES, httpx.MockTransport(self.handle))
        )


def session(code: str, closes: list[str], vwaps: list[str]) -> list[IndexCandle]:
    return [
        bar(code, "5m", OPEN + timedelta(minutes=5 * i), c, vwap=v)
        for i, (c, v) in enumerate(zip(closes, vwaps, strict=True))
    ]


def market() -> list[IndexCandle]:
    data = session("nifty_50", ["24000", "24010", "24030"], ["24000.00", "24004.00", "24012.00"])
    data += session("nifty_bank", ["52000", "51990", "51980"], ["52000.00", "51996.00", "51990.00"])
    data += session("india_vix", ["13.10", "13.00", "12.85"], ["13.10", "13.05", "12.98"])
    data += [bar("nifty_50", "15m", session_start(YESTERDAY) + timedelta(minutes=15 * i), "23900") for i in range(25)]
    data += [
        daily("nifty_50", YESTERDAY, "23950"),
        daily("nifty_bank", YESTERDAY, "52100"),
        daily("india_vix", YESTERDAY, "13.12"),
    ]
    return data


async def test_the_first_context_of_the_day_reads_history_over_rest() -> None:
    feed = IndexFeed(market())
    source = FeedIndexSource(feed.client())
    snapshot = await source.fetch(OPEN + timedelta(minutes=15))
    codes = [s.code for s in snapshot.indices]
    assert codes == ["nifty_50", "nifty_bank"]  # tracked only, and India VIX is the VIX, not an index state
    nifty = snapshot.indices[0]
    assert [c.close for c in nifty.candles_5m] == [Decimal("24000"), Decimal("24010"), Decimal("24030")]
    assert len(nifty.candles_15m) == 25
    assert nifty.previous_day is not None and nifty.previous_day.close == Decimal("23950")
    assert nifty.vwap == Decimal("24012.00")
    assert snapshot.vix is not None and (snapshot.vix.level, snapshot.vix.previous_close) == (12.85, 13.12)
    assert {r.url.params["timeframe"] for r in feed.candle_calls()} == {"5m", "15m", "1d"}


async def test_the_feeds_vwap_makes_the_index_state_even_without_volume() -> None:
    source = FeedIndexSource(IndexFeed(market()).client())
    snapshot = await source.fetch(OPEN + timedelta(minutes=15))
    state = compute.index_state(snapshot.indices[0], DAY)
    assert state is not None
    assert (state.last_price, state.vwap, state.is_above_vwap) == ("24030.00", "24012.00", True)
    bank = compute.index_state(snapshot.indices[1], DAY)
    assert bank is not None and bank.is_above_vwap is False


async def test_events_keep_it_current_without_more_rest_reads() -> None:
    feed = IndexFeed(market())
    source = FeedIndexSource(feed.client())
    await source.fetch(OPEN + timedelta(minutes=15))
    before = len(feed.candle_calls())
    for code, close in (("nifty_50", "24050"), ("nifty_bank", "51970"), ("india_vix", "12.80")):
        assert source.offer(bar(code, "5m", OPEN + timedelta(minutes=15), close, vwap="1.00"))
    snapshot = await source.fetch(OPEN + timedelta(minutes=20))
    assert len(feed.candle_calls()) == before
    assert snapshot.indices[0].candles_5m[-1].close == Decimal("24050")
    assert snapshot.vix is not None and snapshot.vix.level == 12.80


def test_a_lower_or_equal_revision_is_ignored() -> None:
    source = FeedIndexSource(IndexFeed([]).client())
    assert source.offer(bar("nifty_50", "5m", OPEN, "100", revision=2))
    assert not source.offer(bar("nifty_50", "5m", OPEN, "99", revision=2))
    assert not source.offer(bar("nifty_50", "5m", OPEN, "98", revision=1))
    assert source.offer(bar("nifty_50", "5m", OPEN, "97", revision=3))


async def test_an_index_whose_events_stopped_is_read_again_at_most_every_five_minutes() -> None:
    signals, _ = spy_signals()
    feed = IndexFeed(market())
    source = FeedIndexSource(feed.client(), signals)
    await source.fetch(OPEN + timedelta(minutes=15))
    assert sample(signals, MISSING) == 0
    warm = len(feed.candle_calls())
    late = OPEN + timedelta(minutes=15)  # the newest 5m candle ended 09:30: at 09:45 it is two candles late
    await source.fetch(late + timedelta(minutes=15))
    refills = feed.candle_calls()[warm:]
    assert {r.url.params["timeframe"] for r in refills} == {"5m", "15m"}  # today only, not the history again
    assert len(refills) == 6  # three indices, still behind after the read
    assert sample(signals, MISSING) == 3
    await source.fetch(late + timedelta(minutes=17))
    assert len(feed.candle_calls()) == warm + 6  # not again within 5 minutes
    feed.data += [
        bar(c, "5m", OPEN + timedelta(minutes=5 * i), "1", vwap="1")
        for c in ("nifty_50", "nifty_bank", "india_vix")
        for i in range(3, 6)
    ]
    await source.fetch(late + timedelta(minutes=20))
    assert sample(signals, MISSING) == 0
    assert sample(signals, READS, result="ok") == 3 + 3 + 3  # warm, the 09:45 refill, the 09:50 refill


async def test_with_the_feed_down_the_context_has_no_index_data_and_the_signal_counts_it() -> None:
    signals, _ = spy_signals()
    feed = IndexFeed(market())
    feed.down = True
    source = FeedIndexSource(feed.client(), signals)
    early = await source.fetch(OPEN)
    assert early.indices == [] and early.vix is None
    assert sample(signals, MISSING) == 0  # nothing is due at 09:15
    later = await source.fetch(OPEN + timedelta(minutes=30))
    assert later.indices == [] and later.vix is None
    assert sample(signals, MISSING) == 2  # the list is unknown, so nifty_50 and India VIX are expected
    assert sample(signals, READS, result="failed") == 4


async def test_events_alone_serve_when_rest_is_down() -> None:
    feed = IndexFeed([])
    feed.down = True
    source = FeedIndexSource(feed.client())
    for c in session("nifty_50", ["24000", "24010"], ["24000.00", "24004.00"]):
        source.offer(c)
    snapshot = await source.fetch(OPEN + timedelta(minutes=10))
    assert [s.code for s in snapshot.indices] == ["nifty_50"]
    assert snapshot.indices[0].vwap == Decimal("24004.00")


async def test_the_tracked_list_is_read_hourly_and_kept_when_the_feed_fails() -> None:
    feed = IndexFeed(market())
    source = FeedIndexSource(feed.client())
    await source.fetch(OPEN)
    assert source.tracked == ("india_vix", "nifty_50", "nifty_bank")
    feed.down = True
    await source.fetch(OPEN + timedelta(minutes=70))
    assert source.tracked == ("india_vix", "nifty_50", "nifty_bank")
    assert len([r for r in feed.calls if r.url.path == "/v1/indices"]) == 2


async def test_reset_reads_the_history_again() -> None:
    feed = IndexFeed(market())
    source = FeedIndexSource(feed.client())
    await source.fetch(OPEN + timedelta(minutes=15))
    warm = len(feed.candle_calls())
    source.reset()
    await source.fetch(OPEN + timedelta(minutes=16))
    assert len(feed.candle_calls()) == 2 * warm


async def test_a_new_day_drops_yesterdays_five_minute_candles_and_reads_again() -> None:
    feed = IndexFeed(market())
    source = FeedIndexSource(feed.client())
    await source.fetch(OPEN + timedelta(minutes=15))
    tomorrow = OPEN + timedelta(days=1)
    snapshot = await source.fetch(tomorrow)
    assert snapshot.indices == []  # nothing of the new day yet; yesterday's 5m candles are not today's
    assert all(c.start_at >= tomorrow for c in source._sorted("nifty_50", "5m"))
    assert len(feed.candle_calls()) == 2 * 9
