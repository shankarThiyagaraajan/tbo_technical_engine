"""The signal worker process: `uv run python -m tbo_technical_engine.apps.signal_worker`.

One process handles the whole universe (plan: "one signal worker at the planned scale"). It runs its consumers
(candles, feed health, universe, the trading calendar and, with the feed's index source, index candles), a supervisor
that warms the buffer from REST history, and the indicator recorder's write loop. Each 5-minute candle's outcome goes
to the latency tracker; the supervise loop turns closed boundaries into `evaluation_boundary` rows (TE8). Every
evaluated candle goes to the indicator recorder (a snapshot in memory), then to the strategies (`SignalRunner`).
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import socket
from datetime import date, timedelta

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tbo_platform_kit.calendar import TradingCalendar, now_utc
from tbo_platform_kit.config.calendar import IST
from tbo_platform_kit.db import create_engine, session_factory
from tbo_platform_kit.events import EventPublisher
from tbo_platform_kit.http_client import IssuerTokenProvider, ServiceClient, UpstreamUnavailable
from tbo_platform_kit.logging import configure_logging, get_logger
from tbo_platform_kit.metrics import Metrics
from tbo_signals import Signals

from tbo_technical_engine import outbox
from tbo_technical_engine.clients import fundamental, market_feed
from tbo_technical_engine.config.settings import REPOSITORY, Settings
from tbo_technical_engine.consumers.handlers import (
    calendar_consumer,
    candle_consumer,
    feed_consumer,
    index_consumer,
    universe_consumer,
)
from tbo_technical_engine.context.index_source import IndexSource
from tbo_technical_engine.schemas.latency import BoundaryLatency
from tbo_technical_engine.services.candle_buffer import CandleBuffer
from tbo_technical_engine.services.context_service import ContextService
from tbo_technical_engine.services.cooldowns import CooldownStore
from tbo_technical_engine.services.evaluator import StrategyEvaluator
from tbo_technical_engine.services.index_feed import FeedIndexSource
from tbo_technical_engine.services.indicator_engine import IndicatorEngine
from tbo_technical_engine.services.ingest import CandleIngest
from tbo_technical_engine.services.latency import LatencyRecorder, LatencyTracker
from tbo_technical_engine.services.market_calendar import LiveCalendar, build_calendar
from tbo_technical_engine.services.platform_signals import (
    build_signals,
    report_boundaries,
    serve_metrics,
    stream_backlog,
)
from tbo_technical_engine.services.readiness import Readiness
from tbo_technical_engine.services.recorder import IndicatorRecorder
from tbo_technical_engine.services.signal_runner import SignalRunner
from tbo_technical_engine.services.strategy_loader import load_strategies
from tbo_technical_engine.services.supervisor import WarmupSupervisor
from tbo_technical_engine.services.universe import UniverseHolder
from tbo_technical_engine.services.warmup import WarmupSpec
from tbo_technical_engine.store.research_cache import ResearchCache

log = get_logger(__name__)
TICK_SECONDS = 15


class SignalWorker:
    """The wired-up worker: everything it uses is built here so tests can substitute any part."""

    def __init__(
        self,
        settings: Settings,
        redis: aioredis.Redis,
        sessions: async_sessionmaker[AsyncSession],
        feed: market_feed.MarketFeedClient,
        funds: fundamental.FundamentalClient,
        metrics: Metrics | None = None,
        calendar: TradingCalendar | None = None,
        index_source: IndexSource | None = None,
        signals: Signals | None = None,
    ) -> None:
        self.settings = settings
        self.signals = signals
        self.redis = redis
        self.sessions = sessions
        self.funds = funds
        self.buffer = CandleBuffer()
        self.readiness = Readiness()
        self.universe = UniverseHolder()
        self.spec = WarmupSpec()
        calendar = calendar or TradingCalendar.load()
        self.live = calendar if isinstance(calendar, LiveCalendar) else None
        self.ingest = CandleIngest(
            self.buffer,
            self.readiness,
            self.universe,
            self.spec,
            now_utc,
            max_age=timedelta(seconds=settings.max_candle_age_seconds),
        )
        self.tracker = LatencyTracker(calendar, now_utc)
        self.ingest.tracker = self.tracker
        self.ingest.signals = signals
        self.latency = LatencyRecorder(sessions)
        self.supervisor = WarmupSupervisor(
            feed,
            self.buffer,
            self.readiness,
            self.universe,
            self.spec,
            calendar,
            now_utc,
            cache=ResearchCache(settings.research_cache_dir),
        )  # `prime` is set below, once the indicator engine exists
        name = f"{socket.gethostname()}-{id(self) % 10000}"
        profile = settings.profile
        self.engine = IndicatorEngine(self.buffer)
        self.supervisor.prime = self.engine.prime
        self.recorder = IndicatorRecorder(self.engine, sessions, now_utc)
        self.ingest.evaluators.append(self.recorder)
        self.evaluator = StrategyEvaluator(self.engine, self.buffer, load_strategies(settings.strategies_file))
        publisher = EventPublisher(redis, profile, source=REPOSITORY, metrics=metrics)
        self.context = ContextService(
            self.buffer,
            self.universe,
            self.readiness,
            sessions,
            publisher,
            calendar,
            now_utc,
            source=index_source,
            narrow_pct=settings.cpr_narrow_pct,
            wide_pct=settings.cpr_wide_pct,
        )
        self.context.signals = signals
        self.evaluator.index_side = self.context.index_side
        runner = SignalRunner(self.evaluator, sessions, publisher, CooldownStore(redis, profile), now_utc)
        runner.signals = signals
        self.ingest.evaluators.append(runner)
        self.consumers = [
            candle_consumer(redis, profile, self.ingest, name, metrics),
            feed_consumer(redis, profile, self.readiness, name, metrics, signals),
            universe_consumer(
                redis, profile, self.universe, self.buffer, name, metrics, signals, on_dropped=self.drop_stocks
            ),
        ]
        if self.live is not None and self.live.configured:
            self.consumers.append(calendar_consumer(redis, profile, self.live, name, metrics))
        if isinstance(index_source, FeedIndexSource):
            self.consumers.append(index_consumer(redis, profile, index_source, name, metrics, signals))

    def drop_stocks(self, isins: frozenset[str]) -> None:
        """Forget what the engine holds for stocks that left the universe: indicator streams and cooldowns (9.13)."""
        self.engine.forget(isins)
        self.evaluator.forget(isins)

    async def ensure_universe(self, today: date) -> None:
        """Read the universe over REST when the stream has not given us today's (a restart after 08:20)."""
        snapshot = self.universe.snapshot
        if snapshot is not None and snapshot.trade_date == today.isoformat():
            return
        try:
            fetched = await self.funds.universe(today)
        except UpstreamUnavailable as exc:
            log.warning("universe_unavailable", error=str(exc)[:200])
            return
        if fetched is not None:
            before = self.universe.isins
            if self.universe.offer(fetched):
                dropped = frozenset(before - self.universe.isins)
                self.buffer.forget(dropped)
                self.drop_stocks(dropped)

    async def report_signals(self, rows: list[BoundaryLatency]) -> None:
        """The supervisor's numbers for the board: boundaries that closed, the candle backlog, the outbox queue. A tick
        that got this far is the supervisor's heartbeat."""
        if self.signals is None:
            return
        report_boundaries(self.signals, rows)
        candles = self.consumers[0]
        self.signals.emit("technical.candles_waiting", await stream_backlog(self.redis, candles.stream, candles.group))
        self.signals.emit("technical.outbox_waiting", await outbox.waiting(self.sessions))
        self.signals.activity("technical.worker", True, worker="supervisor")

    async def supervise(self, stop: asyncio.Event) -> None:
        """The 15-second loop: make sure a universe is known, then warm-up/readiness and the market-context boundary.
        A failing tick is logged and the loop goes on; only a stop request or cancellation ends it."""
        while not stop.is_set():
            try:
                await self.ensure_universe(now_utc().astimezone(IST).date())
                await self.supervisor.tick()
                await self.context.tick()
                rows = self.tracker.close_due(len(self.universe.isins), self.readiness.feed_healthy)
                self.latency.add(rows)
                await self.latency.flush()
                await self.report_signals(rows)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # one bad tick must not end the worker
                log.error("supervisor_tick_failed", error=type(exc).__name__)
                if self.signals is not None:
                    self.signals.activity("technical.worker", False, worker="supervisor")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), TICK_SECONDS)

    async def run(self, stop: asyncio.Event) -> None:
        """Run the stream consumers and the supervisor until `stop` is set, then wait for them to finish."""
        log.info("signal_worker_started", mode=self.settings.profile.mode.value)
        tasks = [asyncio.create_task(c.run(stop)) for c in self.consumers] + [
            asyncio.create_task(self.supervise(stop)),
            asyncio.create_task(self.recorder.run(stop)),
        ]
        if self.live is not None:
            tasks.append(asyncio.create_task(self.live.run(stop)))
        await stop.wait()
        for task in tasks:
            await task
        log.info("signal_worker_stopped")


async def run(stop: asyncio.Event, settings: Settings | None = None) -> None:
    """Build the worker from settings and run it until `stop`; every connection it opened is closed on the way out."""
    settings = settings or Settings()
    settings.require("database_url", "redis_url", "market_feed_url", "fundamental_url")
    assert settings.redis_url is not None
    assert settings.market_feed_url is not None
    assert settings.fundamental_url is not None
    redis = aioredis.Redis.from_url(settings.redis_url.get_secret_value())
    engine = create_engine(settings)
    tokens = IssuerTokenProvider.from_settings(settings)
    feed_http = ServiceClient(
        str(settings.market_feed_url), audience=market_feed.AUDIENCE, scopes=market_feed.SCOPES, tokens=tokens
    )
    fund_http = ServiceClient(
        str(settings.fundamental_url), audience=fundamental.AUDIENCE, scopes=fundamental.SCOPES, tokens=tokens
    )
    metrics = Metrics(settings.short_name, settings.version, settings.profile.mode.value)
    signals = build_signals(settings, metrics.registry)
    serve_metrics(settings, signals)
    calendar = build_calendar(settings, tokens, signals)
    try:
        feed = market_feed.MarketFeedClient(feed_http)
        worker = SignalWorker(
            settings,
            redis,
            session_factory(engine),
            feed,
            fundamental.FundamentalClient(fund_http),
            metrics,
            calendar=calendar,
            index_source=FeedIndexSource(feed, signals),
            signals=signals,
        )
        await worker.run(stop)
    finally:
        signals.close()
        if calendar.client is not None:
            calendar.client.close()
        await feed_http.aclose()
        await fund_http.aclose()
        await redis.aclose()
        await engine.dispose()


def main() -> None:
    """Process entry point: logging, SIGTERM/SIGINT turn into a clean stop (the container's stop signal is SIGTERM)."""
    settings = Settings()
    configure_logging(
        service=settings.short_name,
        version=settings.version,
        mode=settings.profile.mode.value,
        level=settings.log_level,
    )

    async def _main() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            with contextlib.suppress(NotImplementedError):  # not on Windows; the image is Linux
                loop.add_signal_handler(sig, stop.set)
        await run(stop, settings)

    asyncio.run(_main())


if __name__ == "__main__":
    main()
