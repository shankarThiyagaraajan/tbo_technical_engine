"""Backtest jobs: take a request, run it on the research queue, store the result and the strategy's evidence.

`submit` runs in the API (caps, row, enqueue); `execute` is the body of the Celery task and is safe to run twice: a
finished job is left alone, a running one is claimed only when its worker is long gone, and a re-run replaces the
earlier run's trades in one transaction.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tbo_platform_kit.ids import new_ulid
from tbo_platform_kit.logging import get_logger
from tbo_platform_kit.problems import Problem

from tbo_technical_engine.backtest.engine import DayRules
from tbo_technical_engine.backtest.evidence import build_evidence
from tbo_technical_engine.backtest.index_side import PRIMARY_INDEX, index_sides
from tbo_technical_engine.backtest.overrides import OverrideError, for_backtest
from tbo_technical_engine.backtest.report import contract_metrics, study_result
from tbo_technical_engine.backtest.study import StudyData, run_study
from tbo_technical_engine.schemas.backtest import BacktestCreate, BacktestSettings, ParamOverride, Trade
from tbo_technical_engine.schemas.costs import CostSchedule
from tbo_technical_engine.schemas.strategy import StrategyFile
from tbo_technical_engine.store import backtests as store
from tbo_technical_engine.store.research_cache import ResearchCache

log = get_logger(__name__)
BACKEND = "vectorbt"
"""Reported as the contract's `backend`: the harness whose statistics are used. The fill/exit loop is plain Python
(`backtest/simulate.py`) because its exits depend on the path (target 1, then a stop at entry)."""
Enqueue = Callable[[str], None]
RulesLoader = Callable[[date, date], Any]
"""`async (start, end) -> DayRules`; None when no fundamental engine is configured (explicit ISINs still work)."""


def enqueue_with_celery(backtest_id: str) -> None:
    """Send the job to the research queue. Imported here, not at the top: `tasks` builds the Celery app from the
    environment, which the API process needs only when it really sends. A failed send is not an error for the caller:
    the row is already queued and the Beat sweep (`requeue_waiting_backtests`) sends it again."""
    try:
        from tbo_technical_engine.tasks import run_backtest

        run_backtest.delay(backtest_id)
    except Exception as exc:
        log.warning("backtest_enqueue_failed", backtest_id=backtest_id, error=type(exc).__name__)


class BudgetExceeded(RuntimeError):
    """The job ran past its time budget and stops before the broker would redeliver it."""

    pass


class BlackoutReached(RuntimeError):
    """The job was still running when the mode's research blackout (the trading session) began (review 9.12)."""

    pass


def load_settings(path: Path) -> BacktestSettings:
    """Read and validate the backtest settings file."""
    return BacktestSettings.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def check_request(body: BacktestCreate, strategies: StrategyFile, settings: BacktestSettings) -> None:
    """The caps and the strategy name. Raises Problem 422 with every field that is wrong."""
    errors: list[dict[str, str]] = []
    if body.to_date < body.from_date:
        errors.append({"field": "to_date", "message": "to_date is before from_date"})
    elif (body.to_date - body.from_date).days + 1 > settings.caps.max_range_days:
        errors.append({"field": "to_date", "message": f"the range is longer than {settings.caps.max_range_days} days"})
    if len(body.isins) > settings.caps.max_isins:
        errors.append({"field": "isins", "message": f"more than {settings.caps.max_isins} ISINs"})
    days = (body.to_date - body.from_date).days + 1
    if body.isins and len(body.isins) * days > settings.caps.max_stock_days:
        errors.append(
            {"field": "isins", "message": f"ISINs x days is over {settings.caps.max_stock_days}: fewer stocks or days"}
        )
    try:
        for_backtest(strategies, body.strategy, body.params_overrides, body.params_version)
    except OverrideError as exc:
        errors.append({"field": exc.field, "message": exc.message})
    if errors:
        raise Problem("validation_failed", "The backtest request is not valid.", errors=errors)


async def submit(
    session: AsyncSession,
    body: BacktestCreate,
    strategies: StrategyFile,
    settings: BacktestSettings,
    *,
    requested_by: str,
    code_version: str,
    now: datetime,
) -> str:
    """Checks the caps, writes the queued row (inside the caller's transaction) and returns its ID."""
    check_request(body, strategies, settings)
    await store.lock_submissions(session)  # two POSTs at once must not both see "one active" (review 9.12)
    if await store.active_count(session) >= settings.caps.max_concurrent_jobs:
        raise Problem(
            "rate_limited",
            f"{settings.caps.max_concurrent_jobs} backtests are already queued or running; try again later.",
            headers={"Retry-After": "300"},
        )
    backtest_id = new_ulid()
    await store.create(
        session,
        backtest_id=backtest_id,
        strategy=body.strategy,
        params_version=strategies.params_version,
        params_overrides=[o.model_dump() for o in body.params_overrides],
        from_date=body.from_date,
        to_date=body.to_date,
        isins=body.isins,
        code_version=code_version,
        backend=BACKEND,
        requested_by=requested_by,
        now=now,
    )
    return backtest_id


class BacktestRunner:
    """Runs one claimed backtest: loads data, runs the study, stores trades, metrics and evidence."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        strategies: StrategyFile,
        settings: BacktestSettings,
        costs: CostSchedule,
        cache: ResearchCache,
        rules: RulesLoader | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
        research_allowed: Callable[[datetime], bool] | None = None,
    ) -> None:
        self.sessions = sessions
        self.strategies = strategies
        self.settings = settings
        self.costs = costs
        self.cache = cache
        self.rules = rules
        self.clock = clock
        self.monotonic = monotonic
        self.research_allowed = research_allowed
        """The mode's blackout check; when set, a job still running at the blackout's start stops."""

    async def execute(self, backtest_id: str) -> str:
        """Run one queued backtest; returns the status it ended in (or `missing` / `busy`)."""
        budget = timedelta(seconds=self.settings.caps.max_runtime_seconds)
        async with self.sessions() as session, session.begin():
            row = await store.get(session, backtest_id)
            if row is None:
                return "missing"
            if row.status in ("succeeded", "failed"):
                return str(row.status)
            if not await store.claim(session, backtest_id, self.clock(), budget + timedelta(seconds=60)):
                return "busy"  # another worker has it, and it started recently
        deadline = self.monotonic() + budget.total_seconds()
        try:
            outcome = await self._run(row, deadline)
        except BudgetExceeded:
            await self._fail(backtest_id, f"stopped after the {budget.total_seconds():.0f} s time budget")
            return "failed"
        except BlackoutReached:
            await self._fail(backtest_id, "stopped when the trading session began; submit it again after 15:45 IST")
            return "failed"
        except Exception as exc:  # a failed job says why; it must never leave the row 'running'
            log.error("backtest_failed", backtest_id=backtest_id, error=type(exc).__name__)
            await self._fail(backtest_id, f"{type(exc).__name__}: {exc}")
            return "failed"
        metrics, result, trades, evidence = outcome
        async with self.sessions() as session, session.begin():
            now = self.clock()
            await store.finish(session, backtest_id, metrics=metrics, result=result, trades=trades, now=now)
            if evidence is not None:
                await store.save_evidence(session, row.strategy, row.params_version, backtest_id, evidence, now)
        return "succeeded"

    async def _fail(self, backtest_id: str, error: str) -> None:
        async with self.sessions() as session, session.begin():
            await store.fail(session, backtest_id, error, self.clock())

    async def _run(self, row: Any, deadline: float) -> tuple[dict[str, Any], dict[str, Any], list[Trade], Any]:
        overrides = [ParamOverride(**o) for o in row.params_overrides]
        start, end = row.from_date, row.to_date
        rules: DayRules | None = None
        if row.isins:
            isins: list[str] = list(row.isins)
        else:
            if self.rules is None:
                raise RuntimeError(
                    "no ISINs given and no fundamental engine configured (TBO_TECHNICAL_FUNDAMENTAL_URL)"
                )
            loaded: DayRules = await self.rules(start, end)
            rules = loaded
            isins = sorted({i for statuses in loaded.by_day.values() for i in statuses})

        def tick() -> None:
            if self.monotonic() > deadline:
                raise BudgetExceeded
            if self.research_allowed is not None and not self.research_allowed(self.clock()):
                raise BlackoutReached

        study = run_study(
            self.strategies,
            row.strategy,
            overrides,
            row.params_version,
            StudyData(isins, self.cache.stock, {i: self.cache.session_dates(i, "5m") for i in isins}, start, end),
            self.settings,
            self.costs,
            rules,
            tick=tick,
            monotonic=self.monotonic,
            deadline=deadline,
            index_side=index_sides(self.cache.indices.candles(PRIMARY_INDEX, "5m")),
        )
        result = study_result(study, self.settings, self.costs)
        evidence = None
        if not overrides and rules is not None:
            # Only a gated run over the whole universe is evidence. A run on a few listed stocks has no point-in-time
            # gate and would replace the broad result of the same strategy and version (review 9.10).
            evidence = build_evidence(
                strategy=row.strategy,
                params_version=row.params_version,
                backtest_id=row.backtest_id,
                from_date=start,
                to_date=end,
                metrics=study.metrics,
                walk_forward=study.walk_forward,
                coverage=study.coverage,
                costs_status=self.costs.status,
                min_trades=self.settings.evidence.min_trades,
            )
        return contract_metrics(study.metrics), result, study.trades, evidence
