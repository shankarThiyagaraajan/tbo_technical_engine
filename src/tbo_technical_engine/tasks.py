"""Celery worker and Beat schedule: `celery -A tbo_technical_engine.tasks worker` and `... beat`.

Queues are `technical.<purpose>`; every task must be idempotent (a killed worker's task runs again).
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import redis.asyncio as aioredis
from tbo_platform_kit.calendar import now_utc
from tbo_platform_kit.celery_app import create_celery, crontab_ist, every, queue_name, research_task
from tbo_platform_kit.db import create_engine, session_factory
from tbo_platform_kit.events import EventPublisher

from tbo_technical_engine import outbox
from tbo_technical_engine.settings import REPOSITORY, SHORT_NAME, Settings
from tbo_technical_engine.store import indicators as indicator_store

settings = Settings()
celery = create_celery(settings)
OUTBOX = queue_name(SHORT_NAME, "outbox")
MAINTENANCE = queue_name(SHORT_NAME, "maintenance")
celery.conf.beat_schedule = {
    "flush_outbox": every(30, f"{SHORT_NAME}.flush_outbox", queue=OUTBOX),
    # 02:30 IST, long after the session: drop audit rows older than the retention.
    "prune_indicator_snapshots": {
        "task": f"{SHORT_NAME}.prune_indicator_snapshots",
        "schedule": crontab_ist(2, 30, day_of_week="*"),
        "options": {"queue": MAINTENANCE},
    },
}
SNAPSHOT_RETENTION = timedelta(days=14)


async def _flush() -> int:
    assert settings.redis_url is not None
    engine = create_engine(settings)
    redis = aioredis.Redis.from_url(settings.redis_url.get_secret_value())
    try:
        publisher = EventPublisher(redis, settings.profile, source=REPOSITORY)
        return await outbox.flush(session_factory(engine), publisher)
    finally:
        await redis.aclose()
        await engine.dispose()


@celery.task(name=f"{SHORT_NAME}.flush_outbox", queue=OUTBOX)
def flush_outbox() -> int:
    """Publish events whose request committed but whose publish did not happen (a crash, Redis down)."""
    return asyncio.run(_flush())


RESEARCH = queue_name(SHORT_NAME, "research")
"""Backtests run on their own queue and workers, so they cannot slow down live signals (plan, security notes)."""


@celery.task(name=f"{SHORT_NAME}.run_backtest", queue=RESEARCH)
@research_task(settings.profile)
def run_backtest(backtest_id: str) -> str:
    """TE5. The decorator is the mode wiring: in paper and live this raises ResearchBlackout from 09:00 to 15:45 IST
    (weekdays), so research never competes with live signals; in lab it always runs."""
    raise NotImplementedError("backtests are built in TE5")


async def _prune() -> int:
    engine = create_engine(settings)
    try:
        async with session_factory(engine)() as session, session.begin():
            return await indicator_store.prune(session, now_utc() - SNAPSHOT_RETENTION)
    finally:
        await engine.dispose()


@celery.task(name=f"{SHORT_NAME}.prune_indicator_snapshots", queue=MAINTENANCE)
def prune_indicator_snapshots() -> int:
    """Delete audit rows older than 14 days. Idempotent: running it twice deletes nothing the second time."""
    return asyncio.run(_prune())
