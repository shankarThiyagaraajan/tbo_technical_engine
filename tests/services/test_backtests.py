"""Backtest jobs against a real database: what a run stores, and that running a job twice is safe."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select, update

from tbo_technical_engine.backtest import engine as engine_module
from tbo_technical_engine.backtest.replay import build_tape
from tbo_technical_engine.services import backtests as service
from tbo_technical_engine.store import backtests as store
from tbo_technical_engine.store.tables import backtest, backtest_trade
from tests.backtest.builders import SETTINGS
from tests.backtest.data import FIRST, LAST, NOW, filled_cache, rules_for, runner
from tests.support import ISIN_A, ISIN_B


async def queue(sessions: Any, *, isins: list[str] | None = None, overrides: list[dict[str, str]] | None = None) -> str:
    async with sessions() as session, session.begin():
        return await _submit(session, isins, overrides)


async def _submit(session: Any, isins: list[str] | None, overrides: list[dict[str, str]] | None) -> str:
    from tbo_technical_engine.schemas.backtest import BacktestCreate
    from tests.strategies.session import CONFIG

    body = BacktestCreate.model_validate(
        {
            "strategy": "vwap_pivot",
            "from_date": FIRST,
            "to_date": LAST,
            "isins": [ISIN_A] if isins is None else isins,
            "params_overrides": overrides or [],
        }
    )
    return await service.submit(session, body, CONFIG, SETTINGS, requested_by="tester", code_version="0.0.0", now=NOW)


async def row(sessions: Any, backtest_id: str) -> Any:
    async with sessions() as session:
        return await store.get(session, backtest_id)


async def trade_count(sessions: Any, backtest_id: str) -> int:
    async with sessions() as session:
        stmt = select(func.count()).select_from(backtest_trade).where(backtest_trade.c.backtest_id == backtest_id)
        return int((await session.execute(stmt)).scalar_one())


async def test_a_run_stores_metrics_trades_coverage_costs_and_the_evidence(sessions: Any, tmp_path: Path) -> None:
    backtest_id = await queue(sessions)
    assert await runner(sessions, filled_cache(tmp_path)).execute(backtest_id) == "succeeded"
    r = await row(sessions, backtest_id)
    assert r.status == "succeeded" and r.error is None and r.finished_at == NOW
    assert r.metrics["trade_count"] == await trade_count(sessions, backtest_id) > 0

    result = r.result
    assert result["coverage"]["isins_with_data"] == 1 and result["coverage"]["trading_days"] == 8
    assert result["coverage"]["first_candle_date"] == FIRST.isoformat() and result["coverage"]["gate_applied"] is False
    assert result["costs"]["status"] == "placeholder"  # the owner must see that the figures are not his
    assert result["settings"]["execution"]["slippage_bps"] == "2"
    assert result["statistics"]["sharpe"] is not None
    counts = result["counts"]
    assert counts["candidates"] >= r.metrics["trade_count"] > 0
    assert result["grid"] and set(result["grid"]) == {"confluence_pct", "stop_atr"}  # the grid was walked

    async with sessions() as session:
        assert await store.evidence(session, "vwap_pivot") is None  # listed stocks have no gate: not evidence (9.10)


async def test_a_gated_run_stores_the_evidence_and_a_narrow_run_cannot_replace_it(
    sessions: Any, tmp_path: Path
) -> None:
    cache = filled_cache(tmp_path)
    backtest_id = await queue(sessions, isins=[])
    assert await runner(sessions, cache, rules=rules_for(ISIN_A, "both")).execute(backtest_id) == "succeeded"
    r = await row(sessions, backtest_id)
    async with sessions() as session:
        evidence = await store.evidence(session, "vwap_pivot")
    assert evidence is not None
    assert evidence["backtest_id"] == backtest_id and evidence["low_sample"] is True  # 8 sessions: few trades
    assert evidence["costs_status"] == "placeholder" and evidence["sessions_tested"] == 8
    assert evidence["all_periods"]["trade_count"] == r.metrics["trade_count"]
    assert evidence["out_of_sample"]["trade_count"] == 0 and evidence["out_of_sample_folds"] == 0
    narrow = await queue(sessions)  # one listed stock, no gate
    assert await runner(sessions, cache).execute(narrow) == "succeeded"
    async with sessions() as session:
        again = await store.evidence(session, "vwap_pivot")
    assert again is not None and again["backtest_id"] == backtest_id


async def test_running_a_finished_job_again_changes_nothing(sessions: Any, tmp_path: Path) -> None:
    backtest_id = await queue(sessions)
    run = runner(sessions, filled_cache(tmp_path))
    assert await run.execute(backtest_id) == "succeeded"
    before = await row(sessions, backtest_id)
    assert await run.execute(backtest_id) == "succeeded"  # the broker delivered it twice
    after = await row(sessions, backtest_id)
    assert (after.metrics, after.started_at, after.finished_at) == (
        before.metrics,
        before.started_at,
        before.finished_at,
    )
    assert await trade_count(sessions, backtest_id) == before.metrics["trade_count"]


async def test_a_job_another_worker_is_running_is_left_to_it_until_it_is_long_overdue(
    sessions: Any, tmp_path: Path
) -> None:
    backtest_id = await queue(sessions)
    async with sessions() as session, session.begin():
        await session.execute(update(backtest).where(backtest.c.backtest_id == backtest_id).values(
            status="running", started_at=NOW - timedelta(minutes=5)))  # fmt: skip
    run = runner(sessions, filled_cache(tmp_path))
    assert await run.execute(backtest_id) == "busy"
    async with sessions() as session, session.begin():  # its worker died: older than the budget plus a minute
        await session.execute(update(backtest).where(backtest.c.backtest_id == backtest_id).values(
            started_at=NOW - timedelta(hours=2)))  # fmt: skip
    assert await run.execute(backtest_id) == "succeeded"


async def test_a_rerun_replaces_the_trades_of_the_earlier_run(sessions: Any, tmp_path: Path) -> None:
    backtest_id = await queue(sessions)
    cache = filled_cache(tmp_path)
    assert await runner(sessions, cache).execute(backtest_id) == "succeeded"
    first = await trade_count(sessions, backtest_id)
    async with sessions() as session, session.begin():
        await session.execute(update(backtest).where(backtest.c.backtest_id == backtest_id).values(status="queued"))
    assert await runner(sessions, cache).execute(backtest_id) == "succeeded"
    assert await trade_count(sessions, backtest_id) == first


async def test_overrides_run_but_leave_the_evidence_alone(sessions: Any, tmp_path: Path) -> None:
    backtest_id = await queue(sessions, overrides=[{"name": "confluence_pct", "value": "0.15"}])
    assert await runner(sessions, filled_cache(tmp_path)).execute(backtest_id) == "succeeded"
    r = await row(sessions, backtest_id)
    assert r.params_overrides == [{"name": "confluence_pct", "value": "0.15"}] and r.result["grid"] == {}
    async with sessions() as session:
        assert await store.evidence(session, "vwap_pivot") is None  # only the file's own parameters make evidence


async def test_walk_forward_folds_are_stored_and_the_out_of_sample_is_apart(sessions: Any, tmp_path: Path) -> None:
    short = SETTINGS.model_copy(update={"walk_forward": SETTINGS.walk_forward.model_copy(
        update={"train_days": 4, "test_days": 2, "min_trades_train": 1})})  # fmt: skip
    backtest_id = await queue(sessions)
    assert await runner(sessions, filled_cache(tmp_path), settings=short).execute(backtest_id) == "succeeded"
    wf = (await row(sessions, backtest_id)).result["walk_forward"]
    assert len(wf["folds"]) == 2  # 8 sessions: (8 - 4) / 2
    assert wf["folds"][0]["test_from"] > wf["folds"][0]["train_to"]
    assert wf["out_of_sample"]["trade_count"] == sum(f["test"]["trade_count"] for f in wf["folds"])
    assert wf["stability_pct"] is not None and wf["parameter_choices"]


async def test_the_gate_and_universe_come_from_the_rules_when_no_stocks_are_listed(
    sessions: Any, tmp_path: Path
) -> None:
    both = await queue(sessions, isins=[])
    assert await runner(sessions, filled_cache(tmp_path), rules=rules_for(ISIN_A, "both")).execute(both) == "succeeded"
    blocked = await queue(sessions, isins=[])
    assert (
        await runner(sessions, filled_cache(tmp_path), rules=rules_for(ISIN_A, "block")).execute(blocked) == "succeeded"
    )
    r_both, r_blocked = await row(sessions, both), await row(sessions, blocked)
    assert r_both.result["coverage"]["gate_applied"] is True and r_both.metrics["trade_count"] > 0
    assert r_blocked.metrics["trade_count"] == 0 and r_blocked.result["counts"]["gated_out"] > 0


async def test_a_stock_without_cached_candles_is_counted_not_hidden(sessions: Any, tmp_path: Path) -> None:
    backtest_id = await queue(sessions, isins=[ISIN_A, ISIN_B])
    assert await runner(sessions, filled_cache(tmp_path)).execute(backtest_id) == "succeeded"
    cover = (await row(sessions, backtest_id)).result["coverage"]
    assert (cover["requested_isins"], cover["isins_with_data"], cover["skipped_no_data"]) == (2, 1, 1)


async def test_no_data_at_all_is_a_result_that_says_so(sessions: Any, tmp_path: Path) -> None:
    backtest_id = await queue(sessions)
    assert await runner(sessions, filled_cache(tmp_path / "other", isins=())).execute(backtest_id) == "succeeded"
    r = await row(sessions, backtest_id)
    assert r.metrics["trade_count"] == 0 and r.result["coverage"]["note"].startswith("no candles between")


async def test_a_failure_is_recorded_with_its_reason_and_never_left_running(sessions: Any, tmp_path: Path) -> None:
    backtest_id = await queue(sessions, isins=[])
    assert await runner(sessions, filled_cache(tmp_path)).execute(backtest_id) == "failed"  # no fundamental engine
    r = await row(sessions, backtest_id)
    assert r.status == "failed" and "no ISINs given" in r.error and r.finished_at == NOW
    assert await runner(sessions, filled_cache(tmp_path)).execute(backtest_id) == "failed"  # and it stays failed


async def test_a_job_over_its_time_budget_stops_and_says_so(sessions: Any, tmp_path: Path) -> None:
    backtest_id = await queue(sessions)
    ticks = iter(range(0, 100_000_000, 10_000))  # every reading of the clock is 10,000 s later
    run = runner(sessions, filled_cache(tmp_path), monotonic=lambda: float(next(ticks)))
    assert await run.execute(backtest_id) == "failed"
    assert "time budget" in (await row(sessions, backtest_id)).error


async def test_the_other_parameter_sets_are_skipped_when_they_would_not_fit_and_the_result_says_so(
    sessions: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backtest_id = await queue(sessions)
    clock = {"now": 0.0}

    def monotonic() -> float:
        clock["now"] += 0.001
        return clock["now"]

    # The first (and only) stock "takes" 3,000 s of a 3,300 s budget with all six parameter sets: the grid cannot fit.
    run = runner(sessions, filled_cache(tmp_path), monotonic=monotonic)
    original = build_tape

    def slow(*args: Any, **kwargs: Any) -> Any:
        out = original(*args, **kwargs)
        clock["now"] += 3000
        return out

    monkeypatch.setattr(engine_module, "build_tape", slow)
    assert await run.execute(backtest_id) == "succeeded"
    result = (await row(sessions, backtest_id)).result
    assert result["grid"] == {} and "would not fit" in result["grid_skipped"]


async def test_a_queued_job_whose_send_was_lost_is_found_by_the_sweep(sessions: Any) -> None:
    backtest_id = await queue(sessions)
    async with sessions() as session:
        assert await store.waiting(session, NOW - timedelta(minutes=5)) == []  # too new
        assert await store.waiting(session, NOW + timedelta(minutes=5)) == [backtest_id]


async def test_a_job_still_running_when_the_trading_session_begins_stops_and_says_so(
    sessions: Any, tmp_path: Path
) -> None:
    backtest_id = await queue(sessions)
    run = runner(sessions, filled_cache(tmp_path))
    run.research_allowed = lambda _now: False
    assert await run.execute(backtest_id) == "failed"
    assert "trading session" in (await row(sessions, backtest_id)).error


async def test_two_submissions_at_once_cannot_both_pass_the_cap(sessions: Any) -> None:
    """9.12: the cap is 2 active jobs; ten simultaneous POSTs leave at most 2 queued."""
    import asyncio

    from tbo_platform_kit.problems import Problem

    async def one() -> bool:
        try:
            await queue(sessions)
        except Problem:
            return False
        return True

    results = await asyncio.gather(*(one() for _ in range(10)))
    assert sum(results) == SETTINGS.caps.max_concurrent_jobs
