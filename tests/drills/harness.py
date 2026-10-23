"""A signal worker over a synthetic session, driven boundary by boundary with a clock the test controls.

The worker is the real one (real PostgreSQL and Redis, the kit's consumers and publisher); only the market feed's REST
API is a fake serving synthetic candles, and time is injected. The drills (feed outage, Redis restart, worker crash)
share it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast

from sqlalchemy import text
from tbo_platform_kit.events import EventPublisher
from tbo_platform_kit.mode import Mode, profile_for

from tbo_technical_engine.apps.signal_worker import SignalWorker
from tbo_technical_engine.config.settings import Settings
from tbo_technical_engine.consumers.handlers import CANDLE_STREAM
from tbo_technical_engine.schemas.candle import Candle, UniverseSnapshot
from tbo_technical_engine.schemas.synthetic import SyntheticSpec, isin_of
from tbo_technical_engine.synthetic.generate import StockCandles, stock_candles
from tests.support import CALENDAR, FakeFeed, candle_json

TODAY = date(2026, 10, 15)
STOCKS = 12
HISTORY_SESSIONS = 20
FIRST_BOUNDARY = 3  # index of the first 5-minute candle delivered live (09:30 start): earlier ones are history
PROFILE = profile_for(Mode.LAB)


@dataclass
class Clock:
    """The time the worker sees."""

    now: datetime

    def __call__(self) -> datetime:
        return self.now


class Market:
    """The synthetic day: every candle of every stock, the fake feed serving the closed ones, and the live batches."""

    def __init__(self, stocks: int = STOCKS, seed: int = 11) -> None:
        spec = SyntheticSpec(stocks=stocks, sessions=HISTORY_SESSIONS + 1, end=TODAY, seed=seed)
        self.data: list[StockCandles] = [stock_candles(spec, i) for i in range(stocks)]
        self.isins = tuple(isin_of(i) for i in range(stocks))
        every = [c for s in self.data for c in (*s.five, *s.fifteen, *s.daily)]
        self.feed = FakeFeed(every, page=2000)

    def batch(self, index: int) -> list[Candle]:
        """The 5-minute candles that close at boundary `index` of today (one per stock)."""
        today = [[c for c in s.five if c.start_at.astimezone(UTC).date() == TODAY] for s in self.data]
        return [candles[index] for candles in today]

    def close_of(self, index: int) -> datetime:
        """When boundary `index` closes."""
        first = self.batch(index)[0]
        return first.start_at + timedelta(minutes=5)


def make_worker(market: Market, redis: Any, sessions: Any, clock: Clock, name: str = "w") -> SignalWorker:
    """A worker whose every clock is `clock`, with the universe known and the feed fake."""
    settings = Settings(redis_url="redis://unused", research_cache_dir="/nonexistent")
    worker = SignalWorker(settings, redis, sessions, market.feed.client(), cast(Any, None), calendar=CALENDAR)
    worker.ingest.clock = clock
    worker.tracker.clock = clock
    worker.tracker.since = clock.now
    worker.supervisor.clock = clock
    worker.recorder.clock = clock
    worker.universe.offer(UniverseSnapshot(trade_date=TODAY.isoformat(), universe_version=1, isins=market.isins))
    for consumer in worker.consumers:
        consumer.consumer = f"{name}-{consumer.group}"
        consumer.block_ms = 50
    return worker


async def publish_batch(redis: Any, batch: list[Candle]) -> None:
    """Publish a boundary's candles on the candle stream as the feed does."""
    pub = EventPublisher(redis, PROFILE, source="tbo_market_feed_service")
    for c in batch:
        event = pub.build(
            "tbo.market_feed.candle.closed.v1",
            subject=c.isin,
            data=candle_json(c),
            dataschema="market_feed/candle.v1.json",
        )
        await pub.publish(CANDLE_STREAM, event)


async def drain(consumer: Any, *, rounds: int = 40) -> int:
    """Run a consumer until a read finds nothing; returns how many entries it handled."""
    handled = 0
    for _ in range(rounds):
        outcomes = await consumer.run_once(block_ms=20)
        if not outcomes:
            break
        handled += len(outcomes)
    return handled


async def warm(worker: SignalWorker, clock: Clock, at: datetime) -> None:
    """One supervisor tick at `at`: reads the feed's status and warms the buffer from REST history."""
    clock.now = at
    await worker.supervisor.tick()


async def reset_tables(sessions: Any) -> None:
    """Empty the tables the drills read."""
    async with sessions() as session, session.begin():
        await session.execute(
            text(
                "TRUNCATE technical.signal_candidate, technical.indicator_snapshot, technical.evaluation_boundary, "
                "technical.outbox"
            )
        )


async def candidate_ids(sessions: Any) -> list[str]:
    """Every stored candidate's ID, sorted."""
    async with sessions() as session:
        found = await session.execute(text("SELECT signal_id FROM technical.signal_candidate ORDER BY signal_id"))
        return [r[0] for r in found]


async def settle(worker: SignalWorker, clock: Clock, index: int, market: Market) -> None:
    """Move time past boundary `index` and the worker's grace period (it covers the evaluated candle age, review 9.26),
    then write the rows that are due (what the supervise loop does every 15 s)."""
    clock.now = market.close_of(index) + worker.tracker.grace
    worker.latency.add(worker.tracker.close_due(len(worker.universe.isins), worker.readiness.feed_healthy))
    await worker.latency.flush()
