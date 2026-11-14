"""The event consumers of the signal worker, as EventConsumer instances over the kit."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession
from tbo_platform_kit.events import EventConsumer, Outcome
from tbo_platform_kit.logging import get_logger
from tbo_platform_kit.metrics import Metrics
from tbo_platform_kit.mode import ModeProfile
from tbo_signals import Signals

from tbo_technical_engine.consumers.retry import is_outage
from tbo_technical_engine.schemas.candle import FeedStatus, IndexCandle, UniverseSnapshot
from tbo_technical_engine.services.candle_buffer import CandleBuffer
from tbo_technical_engine.services.index_feed import FeedIndexSource
from tbo_technical_engine.services.ingest import CandleIngest
from tbo_technical_engine.services.market_calendar import LiveCalendar
from tbo_technical_engine.services.readiness import Readiness
from tbo_technical_engine.services.universe import UniverseHolder

log = get_logger(__name__)
CANDLE_STREAM = "tbo.market_feed.candle"
FEED_STREAM = "tbo.market_feed.feed"
UNIVERSE_STREAM = "tbo.fundamental.universe"
CALENDAR_STREAM = "tbo.filings.market_calendar"
INDEX_STREAM = "tbo.market_feed.index_candle"
WORKER_SIGNAL = "technical.worker"  # the signal worker's activity, one `worker` label per consumer


class RecreatingConsumer(EventConsumer):
    """The kit's consumer with this service's retry rule (`consumers/retry.py`: an outage is held for as long as it
    lasts, as on kit 1.0), plus a callback when Redis lost the group. When Redis comes back without its data (a lost
    volume, a flush) every read fails with NOGROUP. Kit 2.5 forgets the group and creates it again on the next cycle
    but tells nobody; this class tells `on_group_lost` (TE8 drill: Redis restart without persistence), which asks
    for the warm-up or history that the events published meanwhile would have carried. Every consumer here is built
    through this class, so none can miss the retry rule (`tests/consumers/test_retry_and_order.py`).
    """

    def __init__(self, *args: Any, on_group_lost: Callable[[], None] | None = None, **kwargs: Any) -> None:
        super().__init__(*args, transient=is_outage, transient_max_age=None, **kwargs)
        self.on_group_lost = on_group_lost

    async def run_once(self, block_ms: int | None = None) -> list[Outcome]:
        """One cycle of the kit's consumer; if the kit found the group gone (it clears `_group_ready`), tell the
        callback."""
        had_group = self._group_ready
        outcomes = await super().run_once(block_ms)
        if had_group and not self._group_ready and self.on_group_lost is not None:
            self.on_group_lost()
        return outcomes


def candle_consumer(
    redis: Redis, profile: ModeProfile, ingest: CandleIngest, name: str, metrics: Metrics | None = None
) -> EventConsumer:
    # start_id "$": a new group reads only what arrives from now on; history comes from REST at warm-up, not from
    # replaying three days of stream. An existing group resumes where it stopped.
    """Reads `candle.closed` events and hands each to the ingest, which stores and evaluates it."""
    # A recreated group starts at `$`, so the candles published while it was missing never arrive: ask for a fresh
    # warm-up, which reads them back over REST.
    return RecreatingConsumer(
        redis,
        profile,
        on_group_lost=ingest.readiness.request_warmup,
        stream=CANDLE_STREAM,
        group="technical.candles",
        consumer=name,
        handler=ingest.handle,
        metrics=metrics,
        start_id="$",
    )


def feed_consumer(
    redis: Redis,
    profile: ModeProfile,
    readiness: Readiness,
    name: str,
    metrics: Metrics | None = None,
    signals: Signals | None = None,
) -> EventConsumer:
    """Reads `feed.status` events so readiness knows whether the feed is healthy."""

    async def handle(event: dict[str, Any], session: AsyncSession | None) -> None:
        readiness.on_feed_status(FeedStatus.model_validate(event["data"]))
        if signals is not None:
            signals.activity(WORKER_SIGNAL, True, worker="feed")

    return RecreatingConsumer(
        redis, profile, stream=FEED_STREAM, group="technical.feed", consumer=name, handler=handle, metrics=metrics
    )


def universe_consumer(
    redis: Redis,
    profile: ModeProfile,
    universe: UniverseHolder,
    buffer: CandleBuffer,
    name: str,
    metrics: Metrics | None = None,
    signals: Signals | None = None,
    on_dropped: Callable[[frozenset[str]], None] | None = None,
) -> EventConsumer:
    """Reads `universe.published` events: a newer universe replaces the stock list, and candles of stocks that dropped
    out are forgotten. `on_dropped` also drops the other per-stock state (indicator streams, cooldowns; review 9.13)."""

    async def handle(event: dict[str, Any], session: AsyncSession | None) -> None:
        data = event["data"]
        before = universe.isins
        snapshot = UniverseSnapshot(
            trade_date=data["trade_date"], universe_version=data["universe_version"], isins=tuple(data["isins"])
        )
        if universe.offer(snapshot):
            dropped = frozenset(before - universe.isins)
            buffer.forget(dropped)
            if on_dropped is not None and dropped:
                on_dropped(dropped)
        if signals is not None:
            signals.activity(WORKER_SIGNAL, True, worker="universe")

    return RecreatingConsumer(
        redis,
        profile,
        stream=UNIVERSE_STREAM,
        group="technical.universe",
        consumer=name,
        handler=handle,
        metrics=metrics,
    )


def calendar_consumer(
    redis: Redis, profile: ModeProfile, calendar: LiveCalendar, name: str, metrics: Metrics | None = None
) -> EventConsumer:
    """Reads `market_calendar.updated` events and rebuilds the calendar at once. The hourly refresh is the backstop, so
    a group created now starts at the end of the stream (the 3 days of history add nothing the first refresh lacks)."""

    async def handle(event: dict[str, Any], session: AsyncSession | None) -> None:
        await calendar.refresh()

    return RecreatingConsumer(
        redis,
        profile,
        stream=CALENDAR_STREAM,
        group="technical.calendar",
        consumer=name,
        handler=handle,
        metrics=metrics,
        start_id="$",
    )


def index_consumer(
    redis: Redis,
    profile: ModeProfile,
    source: FeedIndexSource,
    name: str,
    metrics: Metrics | None = None,
    signals: Signals | None = None,
) -> EventConsumer:
    """Reads `index_candle.closed` events (ADR-019) into the index source. A group created now starts at the end of
    the stream: the first context of the day reads the earlier candles over REST, and so does a recreated group."""

    async def handle(event: dict[str, Any], session: AsyncSession | None) -> None:
        source.offer(IndexCandle.model_validate(event["data"]))
        if signals is not None:
            signals.activity(WORKER_SIGNAL, True, worker="indices")

    return RecreatingConsumer(
        redis,
        profile,
        on_group_lost=source.reset,
        stream=INDEX_STREAM,
        group="technical.indices",
        consumer=name,
        handler=handle,
        metrics=metrics,
        start_id="$",
    )
