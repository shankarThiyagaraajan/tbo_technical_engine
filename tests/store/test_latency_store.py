from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any, cast

from tbo_platform_kit.metrics import Metrics

from tbo_technical_engine.schemas.latency import BoundaryLatency
from tbo_technical_engine.services.latency import LatencyRecorder, session_boundaries
from tbo_technical_engine.services.latency_gauges import NO_RECORD, LatencyGauges, refresh_forever
from tbo_technical_engine.store import latency as store
from tests.support import CALENDAR

DAY = date(2026, 10, 15)


def row(index: int = 0, **changes: object) -> BoundaryLatency:
    at = session_boundaries(DAY)[index]
    base: dict[str, object] = {
        "boundary_at": at, "trade_date": DAY, "expected": 10, "evaluated": 10, "stale": 0, "cold": 0, "inactive": 0,
        "failed": 0, "absent": 0, "suspended": False, "p50_seconds": 0.1, "p95_seconds": 0.4, "max_seconds": 0.5,
        "written_at": at + timedelta(seconds=30),
    }  # fmt: skip
    return BoundaryLatency.model_validate({**base, **changes})


async def test_rows_round_trip_and_a_boundary_is_written_once(sessions: Any) -> None:
    async with sessions() as s, s.begin():
        await store.save_many(s, [row(0), row(1)])
        await store.save_many(s, [row(0, evaluated=1, absent=9)])  # a second writer: the first row stays
    async with sessions() as s:
        found = await store.for_day(s, DAY)
        assert [r.evaluated for r in found] == [10, 10] and found[0] == row(0)
        assert len(await store.since(s, session_boundaries(DAY)[1])) == 1
        assert await store.for_day(s, date(2026, 10, 14)) == []


async def test_the_recorder_keeps_rows_when_the_database_fails_and_writes_them_next_time(sessions: Any) -> None:
    calls = {"n": 0}

    class Flaky:
        def __call__(self) -> Any:
            calls["n"] += 1
            if calls["n"] == 1:
                raise ConnectionError("down")
            return sessions()

    recorder = LatencyRecorder(Flaky())  # type: ignore[arg-type]
    recorder.add([row(0)])
    assert await recorder.flush() == 0
    assert await recorder.flush() == 1
    assert await recorder.flush() == 0


async def test_the_recorder_drops_the_oldest_rows_beyond_its_limit(sessions: Any, monkeypatch: Any) -> None:
    from tbo_technical_engine.services import latency

    monkeypatch.setattr(latency, "MAX_ROWS_WAITING", 2)
    recorder = LatencyRecorder(sessions)
    recorder.add([row(0), row(1), row(2)])
    assert await recorder.flush() == 2
    async with sessions() as s:
        assert [r.boundary_at for r in await store.for_day(s, DAY)] == [
            session_boundaries(DAY)[1],
            session_boundaries(DAY)[2],
        ]


def samples(metrics: Metrics) -> dict[str, float]:
    out: dict[str, float] = {}
    for family in metrics.registry.collect():
        for sample in family.samples:
            if "evaluation" in sample.name:
                out[sample.name] = sample.value
    return out


def test_the_gauges_show_today_s_record() -> None:
    metrics = Metrics("technical", "t", "lab")
    gauges = LatencyGauges(metrics)
    now = datetime(2026, 10, 15, 5, 0, tzinfo=UTC)
    gauges.update([], now)
    assert samples(metrics)["tbo_technical_evaluation_last_record_age_seconds"] == NO_RECORD
    gauges.update([], now, trading_day=False)
    assert samples(metrics)["tbo_technical_evaluation_session_day_count"] == 0
    gauges.update([row(0, p95_seconds=3.0, max_seconds=4.0, evaluated=9, absent=1), row(1)], now)
    got = samples(metrics)
    assert got["tbo_technical_evaluation_missed_today_count"] == 1
    assert got["tbo_technical_evaluation_slow_boundaries_today_count"] == 1
    assert got["tbo_technical_evaluation_boundaries_today_count"] == 2
    assert got["tbo_technical_evaluation_latency_p95_seconds"] == 0.4  # the latest boundary, not the worst
    assert got["tbo_technical_evaluation_latency_max_seconds"] == 0.5
    assert got["tbo_technical_evaluation_last_record_age_seconds"] > 0
    assert got["tbo_technical_evaluation_session_day_count"] == 1
    assert got["tbo_technical_evaluation_gauges_refreshed_seconds"] == now.timestamp()


async def test_the_refresh_loop_reads_the_database_and_survives_a_failed_read(sessions: Any) -> None:
    import asyncio

    metrics = Metrics("technical", "t", "lab")
    gauges = LatencyGauges(metrics)
    async with sessions() as s, s.begin():
        await store.save_many(s, [row(0)])
    stop = asyncio.Event()
    now = session_boundaries(DAY)[0] + timedelta(minutes=1)
    task = asyncio.create_task(refresh_forever(gauges, sessions, lambda: now, CALENDAR, stop, interval=0.05))
    await asyncio.sleep(0.2)
    assert samples(metrics)["tbo_technical_evaluation_boundaries_today_count"] == 1

    def broken() -> Any:
        raise ConnectionError("down")

    stop.set()
    await task
    again = asyncio.Event()
    runner = asyncio.create_task(
        refresh_forever(gauges, cast(Any, broken), lambda: now, CALENDAR, again, interval=0.01)
    )
    await asyncio.sleep(0.1)
    again.set()
    await runner  # the loop logged the failures and kept going; it did not raise
    assert samples(metrics)["tbo_technical_evaluation_boundaries_today_count"] == 1
