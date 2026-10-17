"""Celery wiring: the outbox sweep is scheduled, and research work obeys the mode's blackout."""

from __future__ import annotations

import importlib
from datetime import UTC, date, datetime, timedelta
from types import ModuleType
from typing import Any

import pytest
from tbo_platform_kit.celery_app import ResearchBlackout, research_task
from tbo_platform_kit.mode import Mode, profile_for


@pytest.fixture
def tasks(monkeypatch: pytest.MonkeyPatch, database_url: str, redis_url: str) -> ModuleType:
    # tasks.py reads its settings when imported, so the environment is set first.
    monkeypatch.setenv("TBO_MODE", "lab")
    monkeypatch.setenv("TBO_TECHNICAL_DATABASE_URL", database_url)
    monkeypatch.setenv("TBO_TECHNICAL_REDIS_URL", redis_url)
    return importlib.import_module("tbo_technical_engine.tasks")


def test_beat_schedules_the_outbox_sweep_on_its_own_queue(tasks: ModuleType) -> None:
    entry = tasks.celery.conf.beat_schedule["flush_outbox"]
    assert entry["task"] == "technical.flush_outbox"
    assert entry["options"]["queue"] == "technical.outbox"


def test_backtests_have_their_own_queue(tasks: ModuleType) -> None:
    assert tasks.run_backtest.queue == "technical.research"


@pytest.mark.parametrize("mode", [Mode.PAPER, Mode.LIVE])
def test_research_is_refused_during_the_session_in_paper_and_live(mode: Mode) -> None:
    # 2026-10-15 is a Thursday; 05:00Z is 10:30 IST, inside 09:00-15:45.
    during = research_task(profile_for(mode), lambda: datetime(2026, 10, 15, 5, 0, tzinfo=UTC))(lambda: "ran")
    with pytest.raises(ResearchBlackout):
        during()
    after = research_task(profile_for(mode), lambda: datetime(2026, 10, 15, 11, 0, tzinfo=UTC))(lambda: "ran")
    assert after() == "ran"


def test_research_always_runs_in_lab() -> None:
    during = research_task(profile_for(Mode.LAB), lambda: datetime(2026, 10, 15, 5, 0, tzinfo=UTC))(lambda: "ran")
    assert during() == "ran"


def test_beat_runs_the_swing_analysis_after_the_close_and_a_refresh_before_the_open(tasks: ModuleType) -> None:
    schedule = tasks.celery.conf.beat_schedule
    assert schedule["daily_snapshot"]["task"] == "technical.daily_snapshot"
    assert schedule["morning_snapshot"]["task"] == "technical.morning_snapshot"
    assert {schedule[k]["options"]["queue"] for k in ("daily_snapshot", "morning_snapshot")} == {"technical.swing"}
    # 16:00 IST is 10:30 UTC, 08:00 IST is 02:30 UTC, Monday to Friday.
    close, morning = schedule["daily_snapshot"]["schedule"], schedule["morning_snapshot"]["schedule"]
    assert (close.hour, close.minute, morning.hour, morning.minute) == ({10}, {30}, {2}, {30})


def test_the_snapshot_tasks_pass_their_kind_and_report_the_summary(
    tasks: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tbo_technical_engine.services.snapshot_service import Summary

    seen: list[str] = []

    async def fake(settings: object, kind: str) -> Summary:
        seen.append(kind)
        return Summary(kind, snapshots_new=3)  # type: ignore[arg-type]

    monkeypatch.setattr(tasks, "run_job", fake)
    assert tasks.daily_snapshot()["snapshots_new"] == 3
    assert tasks.morning_snapshot()["kind"] == "morning"
    assert seen == ["close", "morning"]


def test_an_incomplete_run_is_retried_not_swallowed(tasks: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    from tbo_technical_engine.services.snapshot_service import IncompleteRun

    async def broken(settings: object, kind: str) -> None:
        raise IncompleteRun("3 of 6 stocks could not be loaded")

    monkeypatch.setattr(tasks, "run_job", broken)
    with pytest.raises(IncompleteRun):  # called directly, a retry re-raises; in a worker it schedules the next attempt
        tasks.daily_snapshot()


def test_a_backtest_never_outlives_the_brokers_redelivery_window(tasks: ModuleType) -> None:
    """The broker hands an unacknowledged task to a second worker after 3600 s; a job must end before that."""
    assert tasks.run_backtest.time_limit < 3600
    assert tasks.run_backtest.soft_time_limit < tasks.run_backtest.time_limit


def test_beat_resends_backtests_whose_send_was_lost(tasks: ModuleType) -> None:
    entry = tasks.celery.conf.beat_schedule["requeue_waiting_backtests"]
    assert (
        entry["task"] == "technical.requeue_waiting_backtests" and entry["options"]["queue"] == "technical.maintenance"
    )


async def test_the_sweep_sends_only_queued_jobs_older_than_five_minutes(
    tasks: ModuleType, monkeypatch: pytest.MonkeyPatch, sessions: Any
) -> None:
    from tbo_technical_engine.store import backtests as store

    old = datetime.now(UTC) - timedelta(minutes=10)
    async with sessions() as session, session.begin():
        for backtest_id, created in (
            ("01J00000000000000000000001", old),
            ("01J00000000000000000000002", datetime.now(UTC)),
        ):
            await store.create(
                session,
                backtest_id=backtest_id,
                strategy="orb_15m",
                params_version="v",
                params_overrides=[],
                from_date=date(2026, 10, 1),
                to_date=date(2026, 10, 2),
                isins=[],
                code_version="0",
                backend="vectorbt",
                requested_by="t",
                now=created,
            )
    sent: list[str] = []
    monkeypatch.setattr(tasks.run_backtest, "delay", sent.append)
    assert await tasks._requeue() == 1
    assert sent == ["01J00000000000000000000001"]
