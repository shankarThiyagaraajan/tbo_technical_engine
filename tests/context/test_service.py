"""The context service against a real PostgreSQL and Redis: due times, one row per boundary, the event, degradation."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text
from tbo_contracts import validate
from tbo_platform_kit.config.calendar import IST

from tbo_technical_engine.context.index_source import IndexSeries, IndexSnapshot, VixReading
from tbo_technical_engine.schemas.candle import UniverseSnapshot
from tbo_technical_engine.services.candle_buffer import CandleBuffer
from tbo_technical_engine.services.context_service import CONTEXT_EVENT, ContextService, boundary_of
from tbo_technical_engine.services.readiness import Readiness
from tbo_technical_engine.services.universe import UniverseHolder
from tbo_technical_engine.store.tables import market_context, outbox
from tests.strategies.builders import DAY
from tests.support import CALENDAR, ISIN_A, ISIN_B, candle, session_start, trading_days_back


def ist(day: date, hh: int, mm: int, ss: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hh, mm, ss, tzinfo=IST).astimezone(UTC)


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class Source:
    def __init__(self, snapshot: IndexSnapshot | Exception) -> None:
        self.snapshot = snapshot

    async def fetch(self, now: datetime) -> IndexSnapshot:
        if isinstance(self.snapshot, Exception):
            raise self.snapshot
        return self.snapshot


def service(app: Any, clock: Clock, *, healthy: bool = True, source: Any = None) -> tuple[ContextService, CandleBuffer]:
    buffer = CandleBuffer()
    yesterday = trading_days_back(DAY, 1)[0]
    for isin, y, t in ((ISIN_A, "100", "101"), (ISIN_B, "100", "99")):
        buffer.add(candle(isin, "5m", session_start(yesterday) + timedelta(minutes=370), y))
        buffer.add(candle(isin, "5m", session_start(DAY) + timedelta(minutes=5), t))
    universe = UniverseHolder()
    universe.offer(UniverseSnapshot(trade_date=DAY.isoformat(), universe_version=1, isins=(ISIN_A, ISIN_B)))
    readiness = Readiness()
    if healthy:
        readiness.feed_state, readiness.needs_warmup = "healthy", False
    return (
        ContextService(buffer, universe, readiness, app.state.sessions, app.state.publisher, CALENDAR, clock, source),
        buffer,
    )


async def rows(sessions: Any, table: Any = market_context) -> int:
    async with sessions() as session:
        return int((await session.execute(select(func.count()).select_from(table))).scalar_one())


async def clear(sessions: Any) -> None:
    async with sessions() as session, session.begin():
        await session.execute(text("TRUNCATE technical.market_context"))


def test_boundaries() -> None:
    assert boundary_of(ist(DAY, 10, 32, 59)) == ist(DAY, 10, 30)
    assert boundary_of(ist(DAY, 9, 15)) == ist(DAY, 9, 15)


async def test_publishes_once_per_boundary_after_the_grace(app: Any, sessions: Any, redis: Any) -> None:
    await clear(sessions)
    clock = Clock(ist(DAY, 10, 30, 5))
    svc, _ = service(app, clock)
    assert await svc.tick() is False  # the candle of the boundary may not have arrived yet
    clock.now = ist(DAY, 10, 30, 20)
    assert await svc.tick() is True
    assert await svc.tick() is False  # same boundary
    clock.now = ist(DAY, 10, 35, 20)
    assert await svc.tick() is True
    assert await rows(sessions) == 2

    async with sessions() as session:
        events = [
            r.payload for r in (await session.execute(select(outbox.c.payload).order_by(outbox.c.created_at))).all()
        ]
        unpublished = (await session.execute(select(func.count()).where(outbox.c.published_at.is_(None)))).scalar_one()
    import json

    first = json.loads(events[-2])
    assert first["type"] == CONTEXT_EVENT and first["subject"] == "market"
    validate("technical/market_context.v1.json", first["data"])
    assert first["data"]["breadth"] == {"advancing": 1, "declining": 1, "unchanged": 0}
    assert first["data"]["data_quality"] == "ok"
    assert first["data"]["index_states"] == [] and first["data"]["vix"] is None and first["data"]["cpr_class"] is None
    assert first["data"]["context_at"] == "2026-10-15T05:00:00Z"
    assert unpublished == 0


async def test_a_second_process_in_the_same_boundary_publishes_nothing(app: Any, sessions: Any, redis: Any) -> None:
    await clear(sessions)
    clock = Clock(ist(DAY, 11, 0, 30))
    first, _ = service(app, clock)
    second, _ = service(app, clock)
    assert await first.tick() is True
    assert await second.tick() is False
    assert await rows(sessions) == 1


async def test_only_trading_sessions(app: Any, sessions: Any, redis: Any) -> None:
    await clear(sessions)
    clock = Clock(ist(DAY, 9, 10, 30))
    svc, _ = service(app, clock)
    assert await svc.tick() is False  # before 09:15
    clock.now = ist(DAY, 15, 35, 30)
    assert await svc.tick() is False  # after 15:30
    clock.now = ist(DAY, 15, 30, 30)
    assert await svc.tick() is True  # the 15:30 boundary is the last
    clock.now = ist(date(2026, 10, 2), 10, 0, 30)  # a holiday in the test calendar
    assert await svc.tick() is False
    clock.now = ist(date(2026, 10, 17), 10, 0, 30)  # a Saturday
    assert await svc.tick() is False
    assert await rows(sessions) == 1


async def test_an_unhealthy_feed_publishes_a_degraded_context(app: Any, sessions: Any, redis: Any) -> None:
    await clear(sessions)
    svc, _ = service(app, Clock(ist(DAY, 10, 0, 30)), healthy=False)
    await svc.tick()
    assert svc.latest is not None and svc.latest[1].data_quality == "degraded"


def index_snapshot() -> IndexSnapshot:
    yesterday = trading_days_back(DAY, 1)[0]
    today = [candle("nifty_50", "5m", session_start(DAY) + timedelta(minutes=5 * i), f"{100 + i}.00") for i in range(4)]
    trend = [
        candle("nifty_50", "15m", session_start(yesterday) + timedelta(minutes=15 * i), f"{100 + 0.8 * i:.2f}")
        for i in range(60)
    ]
    previous = candle("nifty_50", "1d", session_start(yesterday), "99.30").model_copy(update={"high": 101, "low": 99})
    return IndexSnapshot([IndexSeries("nifty_50", today, trend, previous)], VixReading(12.85, 13.12))


async def test_index_states_vix_and_the_day_class_when_a_source_exists(app: Any, sessions: Any, redis: Any) -> None:
    await clear(sessions)
    svc, _ = service(app, Clock(ist(DAY, 10, 0, 30)), source=Source(index_snapshot()))
    await svc.tick()
    assert svc.latest is not None
    context = svc.latest[1]
    validate("technical/market_context.v1.json", context.model_dump(mode="json"))
    [state] = context.index_states
    assert (state.index_code, state.last_price, state.is_above_vwap, state.trend_15m) == (
        "nifty_50",
        "103.00",
        True,
        "up",
    )
    assert context.vix is not None and context.vix.change_pct == "-2.06"
    assert context.cpr_class == "normal"
    assert context.data_quality == "ok"
    # the strategies get the index side for the candles around this boundary
    boundary = svc.latest[0]
    assert svc.index_side(boundary) == 1
    assert svc.index_side(boundary + timedelta(minutes=20)) is None


async def test_a_failing_index_source_degrades_the_context(app: Any, sessions: Any, redis: Any) -> None:
    await clear(sessions)
    svc, _ = service(app, Clock(ist(DAY, 10, 0, 30)), source=Source(RuntimeError("down")))
    await svc.tick()
    assert svc.latest is not None
    assert svc.latest[1].data_quality == "degraded" and svc.latest[1].index_states == []


async def test_the_day_class_is_kept_for_the_session(app: Any, sessions: Any, redis: Any) -> None:
    await clear(sessions)
    clock = Clock(ist(DAY, 9, 15, 30))
    snapshot = index_snapshot()
    source = Source(snapshot)
    svc, _ = service(app, clock, source=source)
    await svc.tick()
    assert svc.latest is not None and svc.latest[1].cpr_class == "normal"
    source.snapshot = IndexSnapshot(
        snapshot.indices[0:1] and [IndexSeries("nifty_50", snapshot.indices[0].candles_5m, [], None)], None
    )
    clock.now = ist(DAY, 9, 20, 30)
    await svc.tick()
    assert svc.latest[1].cpr_class == "normal"
