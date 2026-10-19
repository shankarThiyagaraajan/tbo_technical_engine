"""Celery worker and Beat schedule: `celery -A tbo_technical_engine.tasks worker` and `... beat`.

Queues are `technical.<purpose>`; every task must be idempotent (a killed worker's task runs again).
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

import redis.asyncio as aioredis
from celery import Task
from tbo_platform_kit.calendar import now_utc
from tbo_platform_kit.celery_app import create_celery, crontab_ist, every, queue_name, research_task
from tbo_platform_kit.db import create_engine, session_factory
from tbo_platform_kit.events import EventPublisher
from tbo_platform_kit.http_client import IssuerTokenProvider, ServiceClient
from tbo_platform_kit.logging import get_logger

from tbo_technical_engine import outbox
from tbo_technical_engine.backtest.costs import load_costs
from tbo_technical_engine.backtest.engine import DayRules
from tbo_technical_engine.clients import fundamental
from tbo_technical_engine.config.settings import REPOSITORY, SHORT_NAME, Settings
from tbo_technical_engine.services import backtests as backtest_service
from tbo_technical_engine.services import point_in_time
from tbo_technical_engine.services.platform_signals import process_signals
from tbo_technical_engine.services.snapshot_job import run_job
from tbo_technical_engine.services.snapshot_service import IncompleteRun, Kind
from tbo_technical_engine.services.strategy_loader import load_strategies
from tbo_technical_engine.store import backtests as backtest_store
from tbo_technical_engine.store import indicators as indicator_store
from tbo_technical_engine.store.research_cache import ResearchCache

log = get_logger(__name__)
settings = Settings()
celery = create_celery(settings)
OUTBOX = queue_name(SHORT_NAME, "outbox")
MAINTENANCE = queue_name(SHORT_NAME, "maintenance")
SWING = queue_name(SHORT_NAME, "swing")
"""The daily swing run (snapshots and setups). Its worker must consume this queue (`-Q ...,technical.swing`)."""
celery.conf.beat_schedule = {
    "flush_outbox": every(30, f"{SHORT_NAME}.flush_outbox", queue=OUTBOX),
    # 02:30 IST, long after the session: drop audit rows older than the retention.
    "prune_indicator_snapshots": {
        "task": f"{SHORT_NAME}.prune_indicator_snapshots",
        "schedule": crontab_ist(2, 30, day_of_week="*"),
        "options": {"queue": MAINTENANCE},
    },
    # Swing analysis and the technical snapshot: after the close, and a refresh before the open (plan: "Time (IST)").
    "daily_snapshot": {
        "task": f"{SHORT_NAME}.daily_snapshot",
        "schedule": crontab_ist(16, 0),
        "options": {"queue": SWING},
    },
    "morning_snapshot": {
        "task": f"{SHORT_NAME}.morning_snapshot",
        "schedule": crontab_ist(8, 0),
        "options": {"queue": SWING},
    },
    "requeue_waiting_backtests": every(300, f"{SHORT_NAME}.requeue_waiting_backtests", queue=MAINTENANCE),
}
SNAPSHOT_RETENTION = timedelta(days=14)
REQUEUE_AFTER = timedelta(minutes=5)


async def _flush() -> int:
    assert settings.redis_url is not None
    engine = create_engine(settings)
    redis = aioredis.Redis.from_url(settings.redis_url.get_secret_value())
    try:
        publisher = EventPublisher(redis, settings.profile, source=REPOSITORY)
        return await outbox.flush(session_factory(engine), publisher, process_signals(settings))
    finally:
        await redis.aclose()
        await engine.dispose()


@celery.task(name=f"{SHORT_NAME}.flush_outbox", queue=OUTBOX)
def flush_outbox() -> int:
    """Publish events whose request committed but whose publish did not happen (a crash, Redis down)."""
    return asyncio.run(_flush())


RESEARCH = queue_name(SHORT_NAME, "research")
"""Backtests run on their own queue and workers, so they cannot slow down live signals (plan, security notes)."""
BACKTEST_SETTINGS = backtest_service.load_settings(settings.backtest_file)
BACKTEST_SOFT_LIMIT = BACKTEST_SETTINGS.caps.max_runtime_seconds + 120
"""The job stops itself at `max_runtime_seconds` (between stocks); these limits catch one stock that never returns. Both
stay under the broker's 3600 s redelivery window, so a running job is never handed to a second worker."""


async def _run_backtest(backtest_id: str) -> str:
    # A job that would run into the trading session (08:10 + 55 min reaches the 09:00 warm-up) does not start; the row
    # stays queued and the sweep sends it again after the blackout (review 9.12).
    if not settings.profile.research_allowed(now_utc() + timedelta(seconds=BACKTEST_SETTINGS.caps.max_runtime_seconds)):
        log.info("backtest_deferred", backtest_id=backtest_id)
        return "deferred"
    engine = create_engine(settings)
    funds_http = None
    rules: backtest_service.RulesLoader | None = None
    if settings.fundamental_url is not None:
        funds_http = ServiceClient(
            str(settings.fundamental_url),
            audience=fundamental.AUDIENCE,
            scopes=fundamental.SCOPES,
            tokens=IssuerTokenProvider.from_settings(settings),
        )
        client = fundamental.FundamentalClient(funds_http)

        async def rules(start: date, end: date) -> DayRules:
            return await point_in_time.load_rules(client, start, end)

    try:
        runner = backtest_service.BacktestRunner(
            session_factory(engine),
            load_strategies(settings.strategies_file),
            BACKTEST_SETTINGS,
            load_costs(settings.costs_file),
            ResearchCache(settings.research_cache_dir),
            rules,
            research_allowed=settings.profile.research_allowed,
        )
        status = await runner.execute(backtest_id)
        if status in ("succeeded", "failed"):  # "missing" and "busy" are not an end
            process_signals(settings).event("technical.backtest_finished", status=status)
        return status
    finally:
        if funds_http is not None:
            await funds_http.aclose()
        await engine.dispose()


@celery.task(
    name=f"{SHORT_NAME}.run_backtest",
    queue=RESEARCH,
    soft_time_limit=BACKTEST_SOFT_LIMIT,
    time_limit=BACKTEST_SOFT_LIMIT + 60,
)
@research_task(settings.profile)
def run_backtest(backtest_id: str) -> str:
    """Run one queued backtest. The decorator is the mode wiring: in paper and live this raises ResearchBlackout from
    09:00 to 15:45 IST (weekdays), so research never competes with live signals; in lab it always runs. Idempotent: a
    finished job is left alone, and a running one is claimed only when its worker is long gone (`BacktestRunner`)."""
    return asyncio.run(_run_backtest(backtest_id))


async def _requeue() -> int:
    engine = create_engine(settings)
    try:
        async with session_factory(engine)() as session:
            ids = await backtest_store.waiting(session, now_utc() - REQUEUE_AFTER)
    finally:
        await engine.dispose()
    for backtest_id in ids:
        run_backtest.delay(backtest_id)
    return len(ids)


@celery.task(name=f"{SHORT_NAME}.requeue_waiting_backtests", queue=MAINTENANCE)
def requeue_waiting_backtests() -> int:
    """Send again every backtest still `queued` after a few minutes: the API commits the row before it sends the job, so
    a send that failed (Redis down) would otherwise wait for ever. Idempotent: `run_backtest` skips a job that is
    already running or done. Does nothing while research is blacked out."""
    if not settings.profile.research_allowed(now_utc()):
        return 0
    return asyncio.run(_requeue())


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


RETRY_DELAY_SECONDS = 600
MAX_RETRIES = 3


def _swing_run(task: Task[..., object], kind: Kind) -> dict[str, object]:
    try:
        summary = asyncio.run(run_job(settings, kind))
    except IncompleteRun as exc:
        # Too many stocks failed to load, so nothing was published. Try again a little later: the run is idempotent.
        raise task.retry(exc=exc, countdown=RETRY_DELAY_SECONDS, max_retries=MAX_RETRIES) from exc
    return {
        "kind": summary.kind,
        "as_of": summary.as_of.isoformat() if summary.as_of else None,
        "skipped": summary.skipped,
        "snapshots_new": summary.snapshots_new,
        "snapshots_unchanged": summary.snapshots_unchanged,
        "setups_new": summary.setups_new,
        "failed": len(summary.failed),
        "seconds": summary.seconds,
    }


@celery.task(name=f"{SHORT_NAME}.daily_snapshot", queue=SWING, bind=True)
def daily_snapshot(self: Task[..., object]) -> dict[str, object]:
    """16:00 IST: the snapshot of every stock as of today's close, and the swing setups. Idempotent: a repeat finds
    every snapshot and setup already stored and announces nothing."""
    return _swing_run(self, "close")


@celery.task(name=f"{SHORT_NAME}.morning_snapshot", queue=SWING, bind=True)
def morning_snapshot(self: Task[..., object]) -> dict[str, object]:
    """08:00 IST: the snapshots as of the previous close again; only a stock whose numbers changed is announced."""
    return _swing_run(self, "morning")
