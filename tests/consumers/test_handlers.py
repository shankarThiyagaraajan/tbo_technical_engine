"""The consumers over a real Redis stream and the kit's publisher, so the wire format is the contract's."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from tbo_contracts import validate_event
from tbo_platform_kit.events import EventPublisher
from tbo_platform_kit.mode import Mode, profile_for

from tbo_technical_engine.consumers.handlers import (
    CANDLE_STREAM,
    FEED_STREAM,
    INDEX_STREAM,
    UNIVERSE_STREAM,
    candle_consumer,
    feed_consumer,
    index_consumer,
    universe_consumer,
)
from tbo_technical_engine.schemas.candle import FeedStatus, UniverseSnapshot
from tbo_technical_engine.services.candle_buffer import CandleBuffer
from tbo_technical_engine.services.index_feed import FeedIndexSource
from tbo_technical_engine.services.ingest import CandleIngest
from tbo_technical_engine.services.readiness import Readiness
from tbo_technical_engine.services.universe import UniverseHolder
from tbo_technical_engine.services.warmup import WarmupSpec
from tests.support import ISIN_A, ISIN_B, FakeFeed, candle, candle_json, history, sample, spy_signals

PROFILE = profile_for(Mode.LAB)
START = datetime(2026, 10, 15, 4, 25, tzinfo=UTC)


def publisher(redis: Any, source: str) -> EventPublisher:
    return EventPublisher(redis, PROFILE, source=source)


async def send_candle(redis: Any, c: Any) -> None:
    pub = publisher(redis, "tbo_market_feed_service")
    event = pub.build(
        "tbo.market_feed.candle.closed.v1", subject=c.isin, data=candle_json(c), dataschema="market_feed/candle.v1.json"
    )
    await pub.publish(CANDLE_STREAM, event)


async def send_status(redis: Any, state: str) -> None:
    pub = publisher(redis, "tbo_market_feed_service")
    data = {
        "state": state,
        "subscribed_count": 100,
        "last_tick_at": None,
        "reason": None if state == "healthy" else "test",
        "changed_at": "2026-10-15T04:00:00Z",
    }
    event = pub.build(
        "tbo.market_feed.feed.status.v1", subject="market", data=data, dataschema="market_feed/feed_status.v1.json"
    )
    await pub.publish(FEED_STREAM, event)


async def send_universe(redis: Any, version: int, *isins: str) -> None:
    pub = publisher(redis, "tbo_fundamental_engine")
    data = {
        "trade_date": "2026-10-15",
        "universe_version": version,
        "isins": list(isins),
        "published_at": "2026-10-15T02:50:00Z",
    }
    event = pub.build(
        "tbo.fundamental.universe.published.v1",
        subject="market",
        data=data,
        dataschema="fundamental/universe_published.v1.json",
    )
    await pub.publish(UNIVERSE_STREAM, event)


@pytest.fixture
def parts() -> dict[str, Any]:
    buffer, readiness, universe = CandleBuffer(), Readiness(), UniverseHolder()
    clock = datetime(2026, 10, 15, 4, 30, tzinfo=UTC)
    ingest = CandleIngest(buffer, readiness, universe, WarmupSpec(), lambda: clock)
    seen: list[Any] = []

    async def evaluate(c: Any) -> None:
        seen.append(c)

    ingest.evaluators.append(evaluate)
    return {"buffer": buffer, "readiness": readiness, "universe": universe, "ingest": ingest, "seen": seen}


async def test_universe_event_sets_the_universe_and_a_newer_one_drops_stocks(redis: Any, parts: dict[str, Any]) -> None:
    consumer = universe_consumer(redis, PROFILE, parts["universe"], parts["buffer"], "w1")
    await send_universe(redis, 1, ISIN_A, ISIN_B)
    parts["buffer"].add(candle(ISIN_B, "5m", START))
    await consumer.run_once(block_ms=10)
    assert parts["universe"].isins == {ISIN_A, ISIN_B}
    await send_universe(redis, 2, ISIN_A)
    await consumer.run_once(block_ms=10)
    assert parts["universe"].isins == {ISIN_A} and parts["buffer"].count(ISIN_B, "5m") == 0


async def test_stocks_that_drop_out_of_the_universe_are_reported_for_the_rest_of_their_state(
    redis: Any, parts: dict[str, Any]
) -> None:
    """9.13: indicator streams and cooldowns of a removed stock are dropped too, through the callback."""
    dropped: list[frozenset[str]] = []
    consumer = universe_consumer(redis, PROFILE, parts["universe"], parts["buffer"], "w1", on_dropped=dropped.append)
    await send_universe(redis, 1, ISIN_A, ISIN_B)
    await consumer.run_once(block_ms=10)
    await send_universe(redis, 2, ISIN_A)
    await consumer.run_once(block_ms=10)
    assert dropped == [frozenset({ISIN_B})]


async def test_feed_status_events_drive_readiness(redis: Any, parts: dict[str, Any]) -> None:
    consumer = feed_consumer(redis, PROFILE, parts["readiness"], "w1")
    await send_status(redis, "healthy")
    await consumer.run_once(block_ms=10)
    assert parts["readiness"].feed_healthy
    await send_status(redis, "down")
    await consumer.run_once(block_ms=10)
    assert not parts["readiness"].feed_healthy and parts["readiness"].needs_warmup


async def test_a_closed_candle_event_reaches_the_evaluators(redis: Any, parts: dict[str, Any]) -> None:
    parts["universe"].offer(UniverseSnapshot(trade_date="2026-10-15", universe_version=1, isins=(ISIN_A,)))
    parts["buffer"].add_many(c for tf in history(ISIN_A, date(2026, 10, 15)).values() for c in tf)
    parts["readiness"].on_feed_status(FeedStatus(state="healthy", changed_at=START))
    parts["readiness"].mark_warm(parts["readiness"].generation)
    consumer = candle_consumer(redis, PROFILE, parts["ingest"], "w1")
    await consumer.ensure_group()  # the group exists before the event, as it does in production
    await send_candle(redis, candle(ISIN_A, "5m", START))
    await send_candle(redis, candle(ISIN_A, "5m", START))  # the same candle twice on the stream
    outcomes = await consumer.run_once(block_ms=10)
    assert [o.result for o in outcomes] == ["processed", "processed"]
    assert len(parts["seen"]) == 1  # stored once, so evaluated once


async def test_a_new_candle_group_does_not_replay_old_stream_entries(redis: Any, parts: dict[str, Any]) -> None:
    await send_candle(redis, candle(ISIN_A, "5m", START))  # published before this worker ever started
    consumer = candle_consumer(redis, PROFILE, parts["ingest"], "w1")
    assert await consumer.run_once(block_ms=10) == []


async def send_index(redis: Any, close: str, revision: int = 1) -> None:
    pub = publisher(redis, "tbo_market_feed_service")
    data = {
        "index_code": "nifty_50",
        "timeframe": "5m",
        "start_at": "2026-10-15T04:25:00Z",
        "open": "24000.00",
        "high": "24020.00",
        "low": "23990.00",
        "close": close,
        "session_vwap": "24005.10",
        "revision": revision,
        "source": "historical",
    }
    event = pub.build(
        "tbo.market_feed.index_candle.closed.v1",
        subject="market",
        data=data,
        dataschema="market_feed/index_candle.v1.json",
    )
    validate_event(event)  # the test's event is the contract's, so the consumer reads what the feed sends
    await pub.publish(INDEX_STREAM, event)


async def test_index_candle_events_reach_the_index_source(redis: Any) -> None:
    signals, _ = spy_signals()
    source = FeedIndexSource(FakeFeed().client())
    consumer = index_consumer(redis, PROFILE, source, "w1", signals=signals)
    await consumer.ensure_group()
    await send_index(redis, "24010.00")
    await send_index(redis, "24015.00", revision=2)
    outcomes = await consumer.run_once(block_ms=10)
    assert [o.result for o in outcomes] == ["processed", "processed"]
    held = source._sorted("nifty_50", "5m")
    assert [(c.close, c.revision, c.session_vwap) for c in held] == [(Decimal("24015.00"), 2, Decimal("24005.10"))]
    assert sample(signals, "tbo_technical_worker_last_handled_seconds", worker="indices") is not None


async def test_a_lost_index_group_asks_for_the_history_again(redis: Any) -> None:
    source = FeedIndexSource(FakeFeed().client())
    source._warm["nifty_50"] = date(2026, 10, 15)
    consumer = index_consumer(redis, PROFILE, source, "w1")
    await consumer.ensure_group()
    await redis.delete(consumer.stream)  # Redis came back without its data: the group is gone
    assert await consumer.run_once(block_ms=10) == []
    assert source._warm == {}
