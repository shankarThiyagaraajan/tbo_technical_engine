"""The three event consumers of the signal worker, as EventConsumer instances over the kit."""

from __future__ import annotations

from typing import Any

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession
from tbo_platform_kit.events import EventConsumer
from tbo_platform_kit.metrics import Metrics
from tbo_platform_kit.mode import ModeProfile

from tbo_technical_engine.schemas.candle import FeedStatus, UniverseSnapshot
from tbo_technical_engine.services.candle_buffer import CandleBuffer
from tbo_technical_engine.services.ingest import CandleIngest
from tbo_technical_engine.services.readiness import Readiness
from tbo_technical_engine.services.universe import UniverseHolder

CANDLE_STREAM = "tbo.market_feed.candle"
FEED_STREAM = "tbo.market_feed.feed"
UNIVERSE_STREAM = "tbo.fundamental.universe"


def candle_consumer(
    redis: Redis, profile: ModeProfile, ingest: CandleIngest, name: str, metrics: Metrics | None = None
) -> EventConsumer:
    # start_id "$": a new group reads only what arrives from now on; history comes from REST at warm-up, not from
    # replaying three days of stream. An existing group resumes where it stopped.
    """Reads `candle.closed` events and hands each to the ingest, which stores and evaluates it."""
    return EventConsumer(
        redis,
        profile,
        stream=CANDLE_STREAM,
        group="technical.candles",
        consumer=name,
        handler=ingest.handle,
        metrics=metrics,
        start_id="$",
    )


def feed_consumer(
    redis: Redis, profile: ModeProfile, readiness: Readiness, name: str, metrics: Metrics | None = None
) -> EventConsumer:
    """Reads `feed.status` events so readiness knows whether the feed is healthy."""

    async def handle(event: dict[str, Any], session: AsyncSession | None) -> None:
        readiness.on_feed_status(FeedStatus.model_validate(event["data"]))

    return EventConsumer(
        redis, profile, stream=FEED_STREAM, group="technical.feed", consumer=name, handler=handle, metrics=metrics
    )


def universe_consumer(
    redis: Redis,
    profile: ModeProfile,
    universe: UniverseHolder,
    buffer: CandleBuffer,
    name: str,
    metrics: Metrics | None = None,
) -> EventConsumer:
    """Reads `universe.published` events: a newer universe replaces the stock list, and candles of stocks that dropped
    out are forgotten."""

    async def handle(event: dict[str, Any], session: AsyncSession | None) -> None:
        data = event["data"]
        before = universe.isins
        snapshot = UniverseSnapshot(
            trade_date=data["trade_date"], universe_version=data["universe_version"], isins=tuple(data["isins"])
        )
        if universe.offer(snapshot):
            buffer.forget(before - universe.isins)

    return EventConsumer(
        redis,
        profile,
        stream=UNIVERSE_STREAM,
        group="technical.universe",
        consumer=name,
        handler=handle,
        metrics=metrics,
    )
