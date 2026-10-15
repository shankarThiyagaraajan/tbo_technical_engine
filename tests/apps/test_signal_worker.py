from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

import httpx

from tbo_technical_engine.apps.signal_worker import SignalWorker
from tbo_technical_engine.settings import Settings
from tests.support import CALENDAR, ISIN_A, FakeFeed, fundamental_client

TODAY = date(2026, 10, 15)


def worker(redis: Any, universe_status: int = 200) -> SignalWorker:
    def handle(request: httpx.Request) -> httpx.Response:
        if universe_status != 200:
            return httpx.Response(universe_status)
        item = {"trade_date": "2026-10-15", "isin": ISIN_A, "universe_version": 3}
        return httpx.Response(200, json={"items": [item], "next_cursor": None})

    settings = Settings(redis_url="redis://unused", research_cache_dir="/nonexistent")  # type: ignore[arg-type]
    return SignalWorker(settings, redis, FakeFeed().client(), fundamental_client(handle), calendar=CALENDAR)


async def test_reads_the_universe_over_rest_when_the_stream_has_not_given_it(redis: Any, monkeypatch: Any) -> None:
    monkeypatch.setenv("TBO_MODE", "lab")
    w = worker(redis)
    await w.ensure_universe(TODAY)
    assert (
        w.universe.isins == {ISIN_A} and w.universe.snapshot is not None and w.universe.snapshot.universe_version == 3
    )


async def test_keeps_the_last_universe_when_the_fundamental_engine_is_down(redis: Any, monkeypatch: Any) -> None:
    monkeypatch.setenv("TBO_MODE", "lab")
    w = worker(redis)
    await w.ensure_universe(TODAY)
    broken = worker(redis, universe_status=503)
    broken.universe = w.universe
    await broken.ensure_universe(date(2026, 10, 16))  # a new day, REST failing
    assert broken.universe.isins == {ISIN_A}  # yesterday's list stays in force


async def test_starts_and_stops_cleanly(redis: Any, monkeypatch: Any) -> None:
    monkeypatch.setenv("TBO_MODE", "lab")
    w = worker(redis)
    stop = asyncio.Event()
    task = asyncio.create_task(w.run(stop))
    await asyncio.sleep(0.3)
    assert not task.done()
    stop.set()
    await asyncio.wait_for(task, 15)
