"""The service's signals (tbo_signals): the manifest, the process wiring and every place that emits."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import delete
from tbo_platform_kit.testing import TestKeys
from tbo_signals import ManifestError, load_manifest
from tbo_signals.config.constants import Level

from tbo_technical_engine import outbox
from tbo_technical_engine.apps.signal_worker import SignalWorker
from tbo_technical_engine.config.settings import REPOSITORY, Settings
from tbo_technical_engine.consumers.handlers import feed_consumer, universe_consumer
from tbo_technical_engine.schemas.latency import BoundaryLatency
from tbo_technical_engine.services.cooldowns import CooldownStore
from tbo_technical_engine.services.evaluator import StrategyEvaluator
from tbo_technical_engine.services.indicator_engine import IndicatorEngine
from tbo_technical_engine.services.platform_signals import (
    MANIFEST_PATH,
    build_signals,
    report_boundaries,
    serve_metrics,
    stream_backlog,
)
from tbo_technical_engine.services.signal_runner import SignalRunner
from tbo_technical_engine.store.tables import outbox as outbox_table
from tests.consumers.test_handlers import PROFILE, send_status, send_universe
from tests.services.test_ingest import LIVE_START, Rig
from tests.services.test_signal_runner import HISTORY, NOW, play
from tests.services.test_snapshot_service import World
from tests.strategies.session import CONFIG
from tests.support import CALENDAR, ISIN_A, ISIN_B, FakeFeed, candle, fundamental_client, spy_signals
from tests.swing.data import DAY

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "tbo_technical_engine"
MANIFEST = ROOT / "config" / "signals.yaml"
SIGNALS_SERVED = "tbo_technical_signal_candidates_candidates_total"


@pytest.fixture
def world(app: Any, sessions: Any) -> World:
    return World(app)


def test_the_manifest_validates_and_every_id_is_emitted_somewhere_in_the_code() -> None:
    declared = {s.id for s in load_manifest(MANIFEST).signals}
    code = "\n".join(p.read_text() for p in SRC.rglob("*.py") if p.name != "signals.py" or p.parent.name != "api")
    used = set(re.findall(r'\(\s*"(technical\.[a-z_]+)"', code))
    assert declared == used, f"declared but not emitted: {declared - used}; emitted but not declared: {used - declared}"


def test_the_manifest_names_the_stages_of_the_board() -> None:
    stages = {s.stage for s in load_manifest(MANIFEST).signals if s.stage}
    assert stages == {"candles", "boundaries", "missed", "signals", "outbox", "published"}


def test_a_broken_manifest_stops_the_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TBO_MODE", "lab")
    bad = tmp_path / "signals.yaml"
    bad.write_text("service: technical\nsignals:\n  - id: technical.x\n    kind: nonsense\n")
    with pytest.raises(ManifestError):
        build_signals(Settings(signals_file=bad))


@pytest.mark.parametrize(
    ("mode", "env", "level"),
    [
        ("lab", None, Level.OFF),
        ("paper", None, Level.METRICS),
        ("live", "full", Level.FULL),
        ("paper", "off", Level.OFF),
    ],
)
def test_the_level_follows_the_mode_and_the_environment(
    monkeypatch: pytest.MonkeyPatch, mode: str, env: str | None, level: Level
) -> None:
    monkeypatch.setenv("TBO_MODE", mode)
    monkeypatch.delenv("TBO_TECHNICAL_SIGNALS", raising=False)
    if env:
        monkeypatch.setenv("TBO_TECHNICAL_SIGNALS", env)
    signals = build_signals(Settings(redis_url="redis://127.0.0.1:1/0", signals_file=MANIFEST))
    try:
        assert signals.level is level
    finally:
        signals.close()


def test_the_worker_serves_its_metrics_only_when_asked_and_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TBO_MODE", "lab")
    on, _ = spy_signals(Level.METRICS)
    off, _ = spy_signals(Level.OFF)
    served: list[int] = []
    monkeypatch.setattr(
        "tbo_technical_engine.services.platform_signals.start_http_server", lambda port, registry: served.append(port)
    )
    assert not serve_metrics(Settings(), on)  # no port set
    assert not serve_metrics(Settings(signals_metrics_port=9100), off)  # switched off
    assert serve_metrics(Settings(signals_metrics_port=9100), on) and served == [9100]


def row(*, evaluated: int = 5, absent: int = 0, failed: int = 0, suspended: bool = False) -> BoundaryLatency:
    at = datetime(2026, 10, 15, 4, 30, tzinfo=UTC)
    return BoundaryLatency(
        boundary_at=at,
        trade_date=at.date(),
        expected=evaluated + absent + failed,
        evaluated=evaluated,
        stale=0,
        cold=0,
        inactive=0,
        failed=failed,
        absent=absent,
        suspended=suspended,
        written_at=at,
    )


def test_boundaries_count_as_evaluated_or_missed_and_a_silent_feed_is_neither() -> None:
    signals, sink = spy_signals()
    report_boundaries(signals, [row(), row(), row(absent=2), row(failed=1), row(absent=5, suspended=True)])
    ok = signals.registry.get_sample_value("tbo_technical_boundaries_evaluated_boundaries_total")
    missed = signals.registry.get_sample_value("tbo_technical_boundaries_missed_boundaries_total")
    assert (ok, missed) == (2.0, 2.0)
    events = [w for w in sink.written if w.get("signal_id") == "technical.boundary_missed"]
    assert [e["value"] for e in events] == [2, 1]


async def test_the_backlog_of_a_group_is_its_lag_plus_what_it_has_not_acknowledged(redis: Any) -> None:
    stream = "paper.tbo.market_feed.candle"
    assert await stream_backlog(redis, stream, "technical.candles") == 0  # no stream yet
    await redis.xadd(stream, {"a": "1"})
    await redis.xgroup_create(stream, "technical.candles", id="0")
    await redis.xadd(stream, {"a": "2"})
    assert await stream_backlog(redis, stream, "technical.candles") == 2
    await redis.xreadgroup("technical.candles", "w1", {stream: ">"}, count=1)  # one delivered, not acknowledged
    assert await stream_backlog(redis, stream, "technical.candles") == 2
    assert await stream_backlog(redis, stream, "someone.else") == 0


async def test_the_outbox_queue_counts_waiting_rows_but_not_the_ones_left_behind(sessions: Any) -> None:
    now = datetime.now(UTC)
    async with sessions() as session, session.begin():
        await session.execute(delete(outbox_table))
        for n, dead in enumerate((None, None, now)):
            await session.execute(
                outbox_table.insert().values(event_id=f"e{n}", stream="s", payload="{}", created_at=now, dead_at=dead)
            )
    assert await outbox.waiting(sessions) == 2


async def test_stored_candidates_and_their_events_are_counted(
    app: Any, settings: Any, redis: Any, sessions: Any
) -> None:
    from tbo_technical_engine.services.candle_buffer import CandleBuffer

    signals, _ = spy_signals(Level.METRICS)
    buffer = CandleBuffer()
    buffer.add_many(HISTORY.before(15))
    evaluator = StrategyEvaluator(IndicatorEngine(buffer), buffer, CONFIG)
    runner = SignalRunner(evaluator, sessions, app.state.publisher, CooldownStore(redis, settings.profile), lambda: NOW)
    runner.signals = signals
    await play(buffer, runner)
    candidates = signals.registry.get_sample_value(SIGNALS_SERVED)
    events = signals.registry.get_sample_value("tbo_technical_signal_events_events_total")
    assert candidates and candidates == events  # each stored candidate became one published event
    assert await outbox.waiting(sessions) == 0


async def test_the_candle_consumer_reports_handled_and_failed() -> None:
    signals, _ = spy_signals(Level.METRICS)
    rig = Rig()
    rig.ingest.signals = signals
    await rig.deliver(candle(ISIN_A, "5m", LIVE_START))
    handled = signals.registry.get_sample_value("tbo_technical_worker_last_handled_seconds", {"worker": "candles"})
    assert (
        handled
        and signals.registry.get_sample_value("tbo_technical_worker_last_error_seconds", {"worker": "candles"}) is None
    )

    async def broken(_: Any) -> None:
        raise RuntimeError("boom")

    rig.ingest.evaluators.append(broken)
    await rig.deliver(candle(ISIN_A, "5m", LIVE_START.replace(minute=30)))
    assert signals.registry.get_sample_value("tbo_technical_worker_last_error_seconds", {"worker": "candles"})


async def test_feed_and_universe_consumers_report_activity(redis: Any) -> None:
    from tbo_technical_engine.services.candle_buffer import CandleBuffer
    from tbo_technical_engine.services.readiness import Readiness
    from tbo_technical_engine.services.universe import UniverseHolder

    signals, _ = spy_signals(Level.METRICS)
    feed = feed_consumer(redis, PROFILE, Readiness(), "w1", signals=signals)
    universe = universe_consumer(redis, PROFILE, UniverseHolder(), CandleBuffer(), "w1", signals=signals)
    await send_status(redis, "healthy")
    await send_universe(redis, 1, ISIN_A, ISIN_B)
    await feed.run_once(block_ms=10)
    await universe.run_once(block_ms=10)
    for worker in ("feed", "universe"):
        assert signals.registry.get_sample_value("tbo_technical_worker_last_handled_seconds", {"worker": worker}), (
            worker
        )


async def test_the_supervisor_reports_the_backlog_the_queue_and_its_heartbeat(
    settings: Any, redis: Any, sessions: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    signals, _ = spy_signals(Level.METRICS)
    worker = SignalWorker(
        settings, redis, sessions, FakeFeed().client(), fundamental_client(lambda r: httpx.Response(404)),
        calendar=CALENDAR, signals=signals,
    )  # fmt: skip
    consumer = worker.consumers[0]
    await consumer.ensure_group()
    await redis.xadd(consumer.stream, {"a": "1"})
    await worker.report_signals([row(), row(absent=1)])
    get = signals.registry.get_sample_value
    assert get("tbo_technical_candles_waiting_items") == 1.0
    assert get("tbo_technical_outbox_waiting_items") == 0.0
    assert get("tbo_technical_boundaries_evaluated_boundaries_total") == 1.0
    assert get("tbo_technical_boundaries_missed_boundaries_total") == 1.0
    assert get("tbo_technical_worker_last_handled_seconds", {"worker": "supervisor"})


async def test_a_swing_run_reports_its_activity_and_a_failed_one_its_error(world: World, sessions: Any) -> None:
    signals, sink = spy_signals()
    service = world.service()
    service.signals = signals
    summary = await service.run("close", DAY)
    get = signals.registry.get_sample_value
    assert get("tbo_technical_swing_snapshot_last_handled_seconds", {"run": "close"})
    finished = [w for w in sink.written if w.get("signal_id") == "technical.swing_snapshot_finished"]
    assert [(w["value"], w["labels"]) for w in finished] == [(summary.snapshots_new, {"run": "close"})]
    assert get("tbo_technical_signal_events_events_total") == summary.snapshots_new + summary.setups_new

    broken = world.service(lambda request: httpx.Response(500))
    broken.signals = signals
    with pytest.raises(Exception):  # noqa: B017  # whatever the fake universe's failure raises
        await broken.run("morning", DAY)
    assert get("tbo_technical_swing_snapshot_last_error_seconds", {"run": "morning"})


async def test_a_skipped_day_tells_nothing(world: World) -> None:
    signals, sink = spy_signals()
    service = world.service()
    service.signals = signals
    saturday = datetime(2026, 10, 17).date()
    summary = await service.run("close", saturday)
    assert summary.skipped and sink.written == []
    assert (
        signals.registry.get_sample_value("tbo_technical_swing_snapshot_last_handled_seconds", {"run": "close"}) is None
    )


async def test_the_manifest_is_served_behind_the_read_scope(
    client: httpx.AsyncClient, keys: TestKeys, app: Any
) -> None:
    read = keys.bearer("tbo_platform", REPOSITORY, ["technical:read"])
    answer = await client.get(MANIFEST_PATH, headers=read)
    assert answer.status_code == 200
    body = answer.json()
    assert body["service"] == "technical" and body["level"] == "off"  # lab: off, and still served
    assert {s["id"] for s in body["signals"]} == {s.id for s in load_manifest(MANIFEST).signals}
    assert (await client.get(MANIFEST_PATH)).status_code == 401
    other = keys.bearer("tbo_platform", REPOSITORY, ["technical:research"])
    assert (await client.get(MANIFEST_PATH, headers=other)).status_code == 403


def test_a_finished_backtest_is_an_event_and_a_missing_or_busy_one_is_not(
    monkeypatch: pytest.MonkeyPatch, database_url: str, redis_url: str
) -> None:
    import asyncio
    import importlib

    monkeypatch.setenv("TBO_MODE", "lab")
    monkeypatch.setenv("TBO_TECHNICAL_DATABASE_URL", database_url)
    monkeypatch.setenv("TBO_TECHNICAL_REDIS_URL", redis_url)
    tasks = importlib.import_module("tbo_technical_engine.tasks")
    signals, sink = spy_signals()
    monkeypatch.setattr(tasks, "process_signals", lambda settings: signals)
    outcomes = iter(["succeeded", "failed", "busy", "missing"])

    class Runner:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def execute(self, backtest_id: str) -> str:
            return next(outcomes)

    monkeypatch.setattr(tasks.backtest_service, "BacktestRunner", Runner)
    for _ in range(4):
        asyncio.run(tasks._run_backtest("01JA8ZQ3W7K4N2C9V5T6B1M30A"))
    finished = [w["labels"] for w in sink.written if w.get("signal_id") == "technical.backtest_finished"]
    assert finished == [{"status": "succeeded"}, {"status": "failed"}]
