from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

import httpx

from tbo_technical_engine.apps.signal_worker import SignalWorker
from tbo_technical_engine.config.settings import Settings
from tbo_technical_engine.services.index_feed import FeedIndexSource
from tests.support import CALENDAR, ISIN_A, FakeFeed, fundamental_client

TODAY = date(2026, 10, 15)


def worker(redis: Any, sessions: Any, universe_status: int = 200) -> SignalWorker:
    def handle(request: httpx.Request) -> httpx.Response:
        if universe_status != 200:
            return httpx.Response(universe_status)
        item = {"trade_date": "2026-10-15", "isin": ISIN_A, "universe_version": 3}
        return httpx.Response(200, json={"items": [item], "next_cursor": None})

    settings = Settings(redis_url="redis://unused", research_cache_dir="/nonexistent")
    return SignalWorker(settings, redis, sessions, FakeFeed().client(), fundamental_client(handle), calendar=CALENDAR)


async def test_reads_the_universe_over_rest_when_the_stream_has_not_given_it(
    redis: Any, sessions: Any, monkeypatch: Any
) -> None:
    monkeypatch.setenv("TBO_MODE", "lab")
    w = worker(redis, sessions)
    await w.ensure_universe(TODAY)
    assert w.universe.isins == {ISIN_A}
    assert w.universe.snapshot is not None
    assert w.universe.snapshot.universe_version == 3


async def test_keeps_the_last_universe_when_the_fundamental_engine_is_down(
    redis: Any, sessions: Any, monkeypatch: Any
) -> None:
    monkeypatch.setenv("TBO_MODE", "lab")
    w = worker(redis, sessions)
    await w.ensure_universe(TODAY)
    broken = worker(redis, sessions, universe_status=503)
    broken.universe = w.universe
    await broken.ensure_universe(date(2026, 10, 16))  # a new day, REST failing
    assert broken.universe.isins == {ISIN_A}  # yesterday's list stays in force


async def test_starts_and_stops_cleanly(redis: Any, sessions: Any, monkeypatch: Any) -> None:
    monkeypatch.setenv("TBO_MODE", "lab")
    w = worker(redis, sessions)
    stop = asyncio.Event()
    task = asyncio.create_task(w.run(stop))
    await asyncio.sleep(0.3)
    assert not task.done()
    stop.set()
    await asyncio.wait_for(task, 15)


def test_the_feeds_index_source_brings_its_consumer(redis: Any, sessions: Any, monkeypatch: Any) -> None:
    monkeypatch.setenv("TBO_MODE", "lab")
    plain = worker(redis, sessions)
    settings = Settings(redis_url="redis://unused", research_cache_dir="/nonexistent")
    feed = FakeFeed().client()
    source = FeedIndexSource(feed)
    with_indices = SignalWorker(
        settings,
        redis,
        sessions,
        feed,
        fundamental_client(lambda r: httpx.Response(503)),
        calendar=CALENDAR,
        index_source=source,
    )
    assert len(with_indices.consumers) == len(plain.consumers) + 1
    assert with_indices.consumers[-1].stream.endswith("tbo.market_feed.index_candle")
    assert with_indices.context.source is source


async def test_a_missing_universe_is_asked_for_once_a_minute_not_every_tick(
    redis: Any, sessions: Any, monkeypatch: Any
) -> None:
    """9.26: on a holiday or a late build, every 15 s tick used to call the fundamental engine."""
    monkeypatch.setenv("TBO_MODE", "lab")
    calls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={"items": [], "next_cursor": None})

    settings = Settings(redis_url="redis://unused", research_cache_dir="/nonexistent")
    w = SignalWorker(settings, redis, sessions, FakeFeed().client(), fundamental_client(handle), calendar=CALENDAR)
    now = [1000.0]
    w.monotonic = lambda: now[0]
    for _ in range(4):  # one minute of 15 s ticks
        await w.ensure_universe(TODAY)
        now[0] += 15
    assert len(calls) == 1
    await w.ensure_universe(TODAY)
    assert len(calls) == 2 and w.universe.isins == set()
