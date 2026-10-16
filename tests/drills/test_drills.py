"""TE8 failure drills: a feed outage, a Redis restart and a worker crash in the middle of a synthetic session.

Each drill states what the engine is meant to do, runs the real worker through it, and checks both that and what the
evaluation record shows afterwards. Where the honest answer is "the engine misses evaluations here", the test asserts
that the record says so: the point of the record is that a miss cannot go unseen.
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import socket
import subprocess
import time
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import redis.asyncio as aioredis

from tbo_technical_engine.config import latency as cfg
from tbo_technical_engine.schemas.latency import BoundaryLatency
from tbo_technical_engine.services.latency import session_boundaries
from tbo_technical_engine.services.session_report import report
from tbo_technical_engine.store import latency as store
from tests.consumers.test_handlers import send_status
from tests.drills.harness import (
    FIRST_BOUNDARY,
    TODAY,
    Clock,
    Market,
    candidate_ids,
    drain,
    make_worker,
    publish_batch,
    reset_tables,
    settle,
    warm,
)

FIRST, LAST = FIRST_BOUNDARY, FIRST_BOUNDARY + 5


@pytest.fixture
def market() -> Market:
    return Market()


async def rows_of(sessions: Any) -> dict[int, BoundaryLatency]:
    """The recorded rows by boundary index (0 = the 09:20 close)."""
    marks = {b: i for i, b in enumerate(session_boundaries(TODAY))}
    async with sessions() as s:
        return {marks[r.boundary_at]: r for r in await store.for_day(s, TODAY)}


async def start(market: Market, redis: Any, sessions: Any, monkeypatch: Any, name: str = "w") -> tuple[Any, Clock]:
    """A warm, active worker one minute before the first live boundary closes."""
    monkeypatch.setenv("TBO_MODE", "lab")
    clock = Clock(market.close_of(FIRST) - timedelta(minutes=1))
    worker = make_worker(market, redis, sessions, clock, name)
    for consumer in worker.consumers:  # the candle group starts at `$`: it must exist before the first event
        await consumer.ensure_group()
    await warm(worker, clock, clock.now)
    assert worker.readiness.active
    return worker, clock


async def deliver(worker: Any, clock: Clock, market: Market, redis: Any, index: int) -> int:
    """Publish boundary `index`'s candles a second after it closes and let the candle consumer handle them."""
    clock.now = market.close_of(index) + timedelta(seconds=1)
    await publish_batch(redis, market.batch(index))
    return await drain(worker.consumers[0])


async def test_a_normal_session_evaluates_every_stock_at_every_boundary(
    market: Market, redis: Any, sessions: Any, monkeypatch: Any
) -> None:
    await reset_tables(sessions)
    worker, clock = await start(market, redis, sessions, monkeypatch)
    for index in range(FIRST, LAST + 1):
        assert await deliver(worker, clock, market, redis, index) == len(market.isins)
        await settle(worker, clock, index, market)
    rows = await rows_of(sessions)
    assert sorted(rows) == list(range(FIRST, LAST + 1))
    assert all(r.evaluated == len(market.isins) and r.missed == 0 and not r.suspended for r in rows.values())
    assert all(r.p95_seconds is not None and r.p95_seconds <= cfg.TARGET_P95_SECONDS for r in rows.values())
    assert await candidate_ids(sessions)  # the synthetic day fires strategies, so the comparison below means something


async def test_feed_outage_the_engine_goes_quiet_then_recovers_after_a_fresh_warm_up(
    market: Market, redis: Any, sessions: Any, monkeypatch: Any
) -> None:
    """Meant to happen: no candidates while the feed is down (silence is correct, and is not a miss); after it is back,
    evaluation resumes only once the warm-up has refilled the buffer. The recovery window is a miss, and is shown."""
    await reset_tables(sessions)
    worker, clock = await start(market, redis, sessions, monkeypatch)
    for index in (FIRST, FIRST + 1):
        await deliver(worker, clock, market, redis, index)
        await settle(worker, clock, index, market)

    await send_status(redis, "down")
    await drain(worker.consumers[1])
    assert not worker.readiness.active

    # Boundary FIRST+2: the feed is silent. Boundary FIRST+3: late candles arrive anyway (a gap refill after reconnect).
    clock.now = market.close_of(FIRST + 2) + timedelta(seconds=1)
    await settle(worker, clock, FIRST + 2, market)
    assert await deliver(worker, clock, market, redis, FIRST + 3) == len(market.isins)
    await settle(worker, clock, FIRST + 3, market)

    # Back to healthy, but the warm-up has not run yet: candles that arrive now are stored and not evaluated.
    await send_status(redis, "healthy")
    await drain(worker.consumers[1])
    assert worker.readiness.feed_healthy and not worker.readiness.active
    await deliver(worker, clock, market, redis, FIRST + 4)
    await settle(worker, clock, FIRST + 4, market)

    await warm(worker, clock, market.close_of(FIRST + 4) + timedelta(seconds=40))
    assert worker.readiness.active
    await deliver(worker, clock, market, redis, FIRST + 5)
    await settle(worker, clock, FIRST + 5, market)

    rows = await rows_of(sessions)
    quiet = [rows[FIRST + 2], rows[FIRST + 3]]
    assert all(r.suspended and r.missed == 0 and r.evaluated == 0 for r in quiet)
    assert rows[FIRST + 2].absent == len(market.isins) and rows[FIRST + 3].inactive == len(market.isins)
    window = rows[FIRST + 4]
    assert not window.suspended and window.inactive == len(market.isins) and window.missed == len(market.isins)
    assert rows[FIRST + 5].evaluated == len(market.isins) and rows[FIRST + 5].missed == 0
    assert not report(TODAY, rows.values()).clean  # a session with a recovery window is not a clean one


async def test_worker_crash_nothing_is_duplicated_and_the_gap_is_visible(
    market: Market, redis: Any, sessions: Any, monkeypatch: Any
) -> None:
    """Meant to happen: a replacement worker takes over the dead one's unacknowledged entries, stores nothing twice, and
    evaluates again after its own warm-up. The candles that arrived while nobody was running were read back from REST at
    warm-up, so they count as already stored and are not evaluated (a backlog is not a signal); the record shows the
    boundary as missing, which is what makes the session not clean."""
    await reset_tables(sessions)
    a, clock = await start(market, redis, sessions, monkeypatch, "a")
    for index in (FIRST, FIRST + 1):
        await deliver(a, clock, market, redis, index)
        await settle(a, clock, index, market)
    before = await candidate_ids(sessions)

    # The crash: the candle consumer has read the next boundary's entries and dies before acknowledging any.
    clock.now = market.close_of(FIRST + 2) + timedelta(seconds=1)
    await publish_batch(redis, market.batch(FIRST + 2))
    stream = a.consumers[0].stream
    await redis.xreadgroup(a.consumers[0].group, "a-dead", {stream: ">"}, count=100)
    pending = await redis.xpending(stream, a.consumers[0].group)
    assert pending["pending"] == len(market.isins)

    # The replacement starts 45 s later and warms from REST history.
    b, clock_b = await start(market, redis, sessions, monkeypatch, "b")
    clock_b.now = market.close_of(FIRST + 2) + timedelta(seconds=45)
    b.tracker.since = clock_b.now  # a process reports only what happened since it started
    b.readiness.needs_warmup = True
    await warm(b, clock_b, clock_b.now)
    assert b.readiness.active
    b.consumers[0].claim_idle_ms = 0
    await drain(b.consumers[0])
    pending = await redis.xpending(stream, b.consumers[0].group)
    assert pending["pending"] == 0  # the dead worker's entries were taken over and acknowledged

    for index in (FIRST + 3, FIRST + 4):
        await deliver(b, clock_b, market, redis, index)
        await settle(b, clock_b, index, market)

    ids = await candidate_ids(sessions)
    assert len(ids) == len(set(ids)) and set(before) <= set(ids)
    rows = await rows_of(sessions)
    assert FIRST + 2 not in rows  # nobody was running at its close: no row, so the session report counts it missing
    assert rows[FIRST + 3].evaluated == len(market.isins) and rows[FIRST + 4].missed == 0
    summary = report(TODAY, rows.values())
    assert not summary.clean and summary.boundaries_missing == 75 - len(rows)


class RedisServer:
    """A real redis-server with an append-only file, so a restart keeps the streams and consumer groups."""

    def __init__(self, directory: Path, port: int, *, persistent: bool = True) -> None:
        self.directory, self.port, self.persistent = directory, port, persistent
        self.process: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        binary = shutil.which("redis-server")
        if binary is None:
            pytest.skip("redis-server is not installed")
        append = "yes" if self.persistent else "no"
        self.process = subprocess.Popen(
            [binary, "--port", str(self.port), "--dir", str(self.directory), "--appendonly", append,
             "--appendfsync", "always", "--save", ""],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )  # fmt: skip
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with contextlib.suppress(OSError), socket.create_connection(("127.0.0.1", self.port), timeout=0.2):
                return
            time.sleep(0.05)
        raise RuntimeError("redis-server did not start")

    def kill(self) -> None:
        assert self.process is not None
        self.process.kill()
        self.process.wait()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def until(condition: Callable[[], bool], seconds: float = 15.0) -> None:
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        await asyncio.sleep(0.05)


async def test_redis_restart_the_consumers_reconnect_and_nothing_is_lost(
    market: Market, sessions: Any, monkeypatch: Any, tmp_path: Path
) -> None:
    """Meant to happen: Redis restarts (with its append-only file); the consumers log the errors, retry, and carry
    on from where the group stopped. Candles published after it is back are evaluated, and none is evaluated twice."""
    server = RedisServer(tmp_path, free_port())
    server.start()
    redis = aioredis.Redis.from_url(f"redis://127.0.0.1:{server.port}/0")
    stop = asyncio.Event()
    tasks: list[asyncio.Task[None]] = []
    try:
        await reset_tables(sessions)
        worker, clock = await start(market, redis, sessions, monkeypatch)
        tasks = [asyncio.create_task(c.run(stop)) for c in worker.consumers]

        await deliver_and_wait(worker, clock, market, redis, FIRST)
        server.kill()
        await asyncio.sleep(1.5)  # the consumers are failing and retrying against a dead port
        assert not any(t.done() for t in tasks)
        server.start()

        clock.now = market.close_of(FIRST + 1) + timedelta(seconds=1)
        await publish_when_up(redis, market.batch(FIRST + 1))
        await until(lambda: worker.tracker.open_count(market.close_of(FIRST + 1)) == len(market.isins))
        for index in (FIRST, FIRST + 1):
            await settle(worker, clock, index, market)
        rows = await rows_of(sessions)
        assert all(rows[i].evaluated == len(market.isins) and rows[i].missed == 0 for i in (FIRST, FIRST + 1))
        ids = await candidate_ids(sessions)
        assert len(ids) == len(set(ids))
    finally:
        stop.set()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await redis.aclose()
        if server.process is not None and server.process.poll() is None:
            server.kill()


async def test_redis_comes_back_empty_the_consumers_recreate_their_groups(
    market: Market, sessions: Any, monkeypatch: Any, tmp_path: Path
) -> None:
    """Meant to happen: Redis restarts WITHOUT its data (a lost volume, a flush). The streams and groups are gone; the
    consumers must notice, recreate their groups and carry on, not retry a missing group for ever."""
    server = RedisServer(tmp_path, free_port(), persistent=False)
    server.start()
    redis = aioredis.Redis.from_url(f"redis://127.0.0.1:{server.port}/0")
    stop = asyncio.Event()
    tasks: list[asyncio.Task[None]] = []
    try:
        await reset_tables(sessions)
        worker, clock = await start(market, redis, sessions, monkeypatch)
        tasks = [asyncio.create_task(c.run(stop)) for c in worker.consumers]
        await deliver_and_wait(worker, clock, market, redis, FIRST)
        server.kill()
        server.start()
        await asyncio.sleep(1.5)  # the consumers find their group missing; the candle group restarts at `$`
        clock.now = market.close_of(FIRST + 1) + timedelta(seconds=1)
        await publish_when_up(redis, market.batch(FIRST + 1))
        await until(lambda: worker.tracker.open_count(market.close_of(FIRST + 1)) == len(market.isins))
        assert worker.readiness.needs_warmup  # candles of the gap are not in the buffer: a fresh warm-up is requested
        assert all(not t.done() for t in tasks)
        await settle(worker, clock, FIRST + 1, market)
        await warm(worker, clock, market.close_of(FIRST + 1) + timedelta(seconds=40))
        assert worker.readiness.active
        await deliver_and_wait(worker, clock, market, redis, FIRST + 2)
        await settle(worker, clock, FIRST + 2, market)
        rows = await rows_of(sessions)
        assert rows[FIRST + 1].inactive == len(market.isins)  # the recovery window is a visible miss
        assert rows[FIRST + 2].evaluated == len(market.isins) and rows[FIRST + 2].missed == 0
    finally:
        stop.set()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await redis.aclose()
        if server.process is not None and server.process.poll() is None:
            server.kill()


async def publish_when_up(redis: Any, batch: Any) -> None:
    """Publish once Redis answers (the client reconnects by itself; the test waits for the server)."""
    deadline = time.monotonic() + 10
    while True:
        try:
            await publish_batch(redis, batch)
            return
        except (ConnectionError, OSError):
            assert time.monotonic() < deadline, "redis did not come back"
            await asyncio.sleep(0.1)


async def deliver_and_wait(worker: Any, clock: Clock, market: Market, redis: Any, index: int) -> None:
    clock.now = market.close_of(index) + timedelta(seconds=1)
    await publish_batch(redis, market.batch(index))
    await until(lambda: worker.tracker.open_count(market.close_of(index)) == len(market.isins))
