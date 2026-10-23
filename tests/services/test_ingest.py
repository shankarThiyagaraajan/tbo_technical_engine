from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from tbo_technical_engine.schemas.candle import Candle, FeedStatus, UniverseSnapshot
from tbo_technical_engine.services.candle_buffer import CandleBuffer
from tbo_technical_engine.services.ingest import CandleIngest
from tbo_technical_engine.services.readiness import Readiness
from tbo_technical_engine.services.universe import UniverseHolder
from tbo_technical_engine.services.warmup import WarmupSpec
from tests.support import ISIN_A, ISIN_B, candle, candle_json, history

TODAY = date(2026, 10, 15)
NOW = datetime(2026, 10, 15, 4, 30, tzinfo=UTC)  # 10:00 IST
LIVE_START = datetime(2026, 10, 15, 4, 25, tzinfo=UTC)  # the candle that closed at 10:00 IST


class Rig:
    def __init__(self, *, warm: bool = True, healthy: bool = True, in_universe: tuple[str, ...] = (ISIN_A,)) -> None:
        self.buffer = CandleBuffer()
        self.readiness = Readiness()
        self.universe = UniverseHolder()
        self.universe.offer(UniverseSnapshot(trade_date="2026-10-15", universe_version=1, isins=in_universe))
        if warm:
            self.buffer.add_many(c for tf in history(ISIN_A, TODAY).values() for c in tf)
        if healthy:
            self.readiness.on_feed_status(FeedStatus(state="healthy", changed_at=NOW))
            self.readiness.mark_warm(self.readiness.generation)
        self.clock = NOW
        self.ingest = CandleIngest(self.buffer, self.readiness, self.universe, WarmupSpec(), lambda: self.clock)
        self.seen: list[Candle] = []

        async def evaluate(c: Candle) -> None:
            self.seen.append(c)

        self.ingest.evaluators.append(evaluate)

    async def deliver(self, c: Candle) -> None:
        event: dict[str, Any] = {"data": candle_json(c)}
        await self.ingest.handle(event, None)


async def test_a_fresh_five_minute_candle_is_evaluated() -> None:
    rig = Rig()
    live = candle(ISIN_A, "5m", LIVE_START)
    await rig.deliver(live)
    assert rig.seen == [live]


async def test_a_redelivered_event_is_not_evaluated_twice() -> None:
    rig = Rig()
    live = candle(ISIN_A, "5m", LIVE_START)
    await rig.deliver(live)
    await rig.deliver(live)
    assert rig.seen == [live]


async def test_a_corrected_candle_is_evaluated_again() -> None:
    rig = Rig()
    await rig.deliver(candle(ISIN_A, "5m", LIVE_START))
    await rig.deliver(candle(ISIN_A, "5m", LIVE_START, "101.00", revision=2))
    assert [c.revision for c in rig.seen] == [1, 2]


@pytest.mark.parametrize("timeframe", ["15m"])
async def test_slower_timeframes_are_stored_but_never_evaluated(timeframe: str) -> None:
    rig = Rig()
    await rig.deliver(candle(ISIN_A, timeframe, LIVE_START))
    assert rig.seen == [] and rig.buffer.last(ISIN_A, timeframe) is not None


async def test_other_timeframes_are_ignored() -> None:
    rig = Rig()
    await rig.deliver(candle(ISIN_A, "1m", LIVE_START))
    await rig.deliver(candle(ISIN_A, "60m", LIVE_START))
    assert rig.seen == [] and rig.buffer.count(ISIN_A, "1m") == 0


async def test_a_stock_outside_the_universe_is_ignored() -> None:
    rig = Rig(in_universe=(ISIN_B,))
    await rig.deliver(candle(ISIN_A, "5m", LIVE_START))
    assert rig.seen == []
    assert rig.buffer.count(ISIN_A, "5m") == len(history(ISIN_A, TODAY)["5m"])  # nothing was added


async def test_a_cold_stock_is_silent_but_keeps_collecting() -> None:
    rig = Rig(warm=False)
    await rig.deliver(candle(ISIN_A, "5m", LIVE_START))
    assert rig.seen == [] and rig.buffer.count(ISIN_A, "5m") == 1


@pytest.mark.parametrize("state", ["stale", "down", "connecting"])
async def test_nothing_is_evaluated_while_the_feed_is_unhealthy(state: str) -> None:
    rig = Rig()
    rig.readiness.on_feed_status(FeedStatus(state=state, changed_at=NOW))
    await rig.deliver(candle(ISIN_A, "5m", LIVE_START))
    assert rig.seen == []


async def test_after_recovery_nothing_is_evaluated_until_the_warm_up() -> None:
    rig = Rig()
    rig.readiness.on_feed_status(FeedStatus(state="down", changed_at=NOW))
    rig.readiness.on_feed_status(FeedStatus(state="healthy", changed_at=NOW))
    await rig.deliver(candle(ISIN_A, "5m", LIVE_START))
    assert rig.seen == []
    rig.readiness.mark_warm(rig.readiness.generation)
    await rig.deliver(candle(ISIN_A, "5m", LIVE_START + timedelta(minutes=5)))
    assert len(rig.seen) == 1


async def test_a_backlog_candle_is_stored_not_evaluated() -> None:
    rig = Rig()
    old = candle(ISIN_A, "5m", LIVE_START - timedelta(minutes=30))
    await rig.deliver(old)
    assert rig.seen == [] and old in rig.buffer.series(ISIN_A, "5m")


@pytest.mark.parametrize(("late", "evaluated"), [(120, True), (121, False)])
async def test_the_age_limit_is_measured_from_the_close(late: int, evaluated: bool) -> None:
    rig = Rig()
    rig.clock = LIVE_START + timedelta(minutes=5, seconds=late)
    await rig.deliver(candle(ISIN_A, "5m", LIVE_START))
    assert bool(rig.seen) is evaluated


class Tracked:
    """What the latency tracker was told: (candle start, outcome, seconds)."""

    def __init__(self) -> None:
        self.told: list[tuple[datetime, str, float | None]] = []

    def observe(self, start: datetime, outcome: str, seconds: float | None = None) -> None:
        self.told.append((start, outcome, seconds))


def tracked(rig: Rig) -> Tracked:
    t = Tracked()
    rig.ingest.tracker = t  # type: ignore[assignment]
    return t


async def test_an_evaluated_candle_is_reported_with_the_time_since_its_close() -> None:
    rig = Rig()
    t = tracked(rig)
    rig.clock = NOW + timedelta(seconds=1.5)
    await rig.deliver(candle(ISIN_A, "5m", LIVE_START))
    assert t.told == [(LIVE_START, "evaluated", 1.5)]


async def test_each_reason_a_candle_is_not_evaluated_is_reported() -> None:
    cold, down, late = Rig(warm=False), Rig(healthy=False), Rig()
    late.clock = NOW + timedelta(minutes=10)
    for rig, reason in ((cold, "cold"), (down, "inactive"), (late, "stale")):
        t = tracked(rig)
        await rig.deliver(candle(ISIN_A, "5m", LIVE_START))
        assert [o for _, o, _ in t.told] == [reason], reason


async def test_a_failing_evaluator_is_reported_as_failed_and_the_others_still_run() -> None:
    rig = Rig()
    t = tracked(rig)

    async def broken(_: Candle) -> None:
        raise RuntimeError("boom")

    rig.ingest.evaluators.insert(0, broken)
    await rig.deliver(candle(ISIN_A, "5m", LIVE_START))
    assert [o for _, o, _ in t.told] == ["failed"] and len(rig.seen) == 1


async def test_fifteen_minute_candles_and_redeliveries_are_not_reported() -> None:
    rig = Rig()
    t = tracked(rig)
    live = candle(ISIN_A, "5m", LIVE_START)
    await rig.deliver(live)
    await rig.deliver(live)
    await rig.deliver(candle(ISIN_A, "15m", datetime(2026, 10, 15, 4, 15, tzinfo=UTC)))
    assert len(t.told) == 1
