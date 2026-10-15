"""Celery wiring: the outbox sweep is scheduled, and research work obeys the mode's blackout."""

from __future__ import annotations

import importlib
from datetime import UTC, datetime
from types import ModuleType

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
