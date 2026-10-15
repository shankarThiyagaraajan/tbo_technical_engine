"""The signal worker starts and stops cleanly."""

from __future__ import annotations

import asyncio

from tbo_technical_engine.apps.signal_worker import run


async def test_runs_until_stopped() -> None:
    stop = asyncio.Event()
    task = asyncio.create_task(run(stop))
    await asyncio.sleep(0)
    assert not task.done()
    stop.set()
    await asyncio.wait_for(task, 1)
