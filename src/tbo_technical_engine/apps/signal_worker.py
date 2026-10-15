"""The signal worker process: `uv run python -m tbo_technical_engine.apps.signal_worker`.

One process handles the whole universe (plan: "one signal worker at the planned scale"). It runs three consumers
(candles, feed health, universe) and a supervisor that warms the buffer from REST history. Every evaluated candle
goes to the indicator recorder, then to the strategies (`SignalRunner`).
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import socket
from datetime import date, timedelta

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tbo_platform_kit.calendar import IST, TradingCalendar, now_utc
from tbo_platform_kit.db import create_engine, session_factory
from tbo_platform_kit.events import EventPublisher
from tbo_platform_kit.http_client import IssuerTokenProvider, ServiceClient, UpstreamUnavailable
from tbo_platform_kit.logging import configure_logging, get_logger
from tbo_platform_kit.metrics import Metrics

from tbo_technical_engine.clients import fundamental, market_feed
from tbo_technical_engine.consumers.handlers import candle_consumer, feed_consumer, universe_consumer
from tbo_technical_engine.context.index_source import IndexSource
from tbo_technical_engine.services.candle_buffer import CandleBuffer
from tbo_technical_engine.services.context_service import ContextService
from tbo_technical_engine.services.cooldowns import CooldownStore
from tbo_technical_engine.services.evaluator import StrategyEvaluator
from tbo_technical_engine.services.indicator_engine import IndicatorEngine
from tbo_technical_engine.services.ingest import CandleIngest
from tbo_technical_engine.services.readiness import Readiness
from tbo_technical_engine.services.recorder import IndicatorRecorder
from tbo_technical_engine.services.signal_runner import SignalRunner
from tbo_technical_engine.services.strategy_loader import load_strategies
from tbo_technical_engine.services.supervisor import WarmupSupervisor
from tbo_technical_engine.services.universe import UniverseHolder
from tbo_technical_engine.services.warmup import WarmupSpec
from tbo_technical_engine.settings import REPOSITORY, Settings
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
    ) -> None:
        self.settings = settings
        self.funds = funds
        self.buffer = CandleBuffer()
        self.readiness = Readiness()
        self.universe = UniverseHolder()
        self.spec = WarmupSpec()
        calendar = calendar or TradingCalendar.load()
        self.ingest = CandleIngest(
            self.buffer,
            self.readiness,
            self.universe,
            self.spec,
            now_utc,
            max_age=timedelta(seconds=settings.max_candle_age_seconds),
        )
        self.supervisor = WarmupSupervisor(
            feed,
            self.buffer,
            self.readiness,
            self.universe,
            self.spec,
            calendar,
            now_utc,
            cache=ResearchCache(settings.research_cache_dir),
        )
        name = f"{socket.gethostname()}-{id(self) % 10000}"
        profile = settings.profile
        self.engine = IndicatorEngine(self.buffer)
        self.ingest.evaluators.append(IndicatorRecorder(self.engine, sessions, now_utc))
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
        self.evaluator.index_side = self.context.index_side
        self.ingest.evaluators.append(
            SignalRunner(self.evaluator, sessions, publisher, CooldownStore(redis, profile), now_utc)
        )
        self.consumers = [
            candle_consumer(redis, profile, self.ingest, name, metrics),
            feed_consumer(redis, profile, self.readiness, name, metrics),
            universe_consumer(redis, profile, self.universe, self.buffer, name, metrics),
        ]

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
            self.universe.offer(fetched)

    async def supervise(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.ensure_universe(now_utc().astimezone(IST).date())
                await self.supervisor.tick()
                await self.context.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # one bad tick must not end the worker
                log.error("supervisor_tick_failed", error=type(exc).__name__)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), TICK_SECONDS)

    async def run(self, stop: asyncio.Event) -> None:
        log.info("signal_worker_started", mode=self.settings.profile.mode.value)
        tasks = [asyncio.create_task(c.run(stop)) for c in self.consumers] + [asyncio.create_task(self.supervise(stop))]
        await stop.wait()
        for task in tasks:
            await task
        log.info("signal_worker_stopped")


async def run(stop: asyncio.Event, settings: Settings | None = None) -> None:
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
    try:
        worker = SignalWorker(
            settings,
            redis,
            session_factory(engine),
            market_feed.MarketFeedClient(feed_http),
            fundamental.FundamentalClient(fund_http),
            metrics,
        )
        await worker.run(stop)
    finally:
        await feed_http.aclose()
        await fund_http.aclose()
        await redis.aclose()
        await engine.dispose()


def main() -> None:
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
