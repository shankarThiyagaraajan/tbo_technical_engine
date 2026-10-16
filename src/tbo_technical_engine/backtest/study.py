"""One whole study of a strategy: the run at the file's parameters, the other parameter sets of its grid, and the
walk-forward and statistics built from them. Synchronous and free of storage, so the job (`services/backtests.py`) and
the report script (`scripts/backtest_report.py`) run exactly the same code.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date

from tbo_technical_engine.backtest.engine import DayRules, coverage, run_strategy
from tbo_technical_engine.backtest.metrics import summarize
from tbo_technical_engine.backtest.overrides import for_backtest
from tbo_technical_engine.backtest.stats import equity_stats
from tbo_technical_engine.backtest.walkforward import Combo, combos, walk_forward
from tbo_technical_engine.schemas.backtest import (
    BacktestSettings,
    Coverage,
    Metrics,
    ParamOverride,
    StockData,
    Trade,
    WalkForward,
)
from tbo_technical_engine.schemas.costs import CostSchedule
from tbo_technical_engine.schemas.strategy import StrategyFile


@dataclass
class Study:
    """One strategy over one range: the base run, its walk-forward and the figures the report quotes."""

    trades: list[Trade]
    metrics: Metrics
    walk_forward: WalkForward
    statistics: dict[str, float | None]
    coverage: Coverage
    counts: dict[str, int]
    grid: dict[str, list[str]]
    """The grid that was walked; empty when there was none or it was skipped."""
    grid_skipped: str | None = None
    sessions: list[date] = field(default_factory=list)


def run_study(
    file: StrategyFile,
    strategy: str,
    overrides: Sequence[ParamOverride],
    params_version: str | None,
    isins: Sequence[str],
    load: Callable[[str], StockData | None],
    sessions_by_isin: Mapping[str, set[date]],
    start: date,
    end: date,
    settings: BacktestSettings,
    costs: CostSchedule,
    rules: DayRules | None,
    *,
    tick: Callable[[], None] | None = None,
    monotonic: Callable[[], float],
    deadline: float,
) -> Study:
    """Parameter sets beyond the file's own run only when the file's own run left time for them: the first run's
    duration times the number of other sets must fit before `deadline`, else the walk-forward uses the fixed parameters
    and the result says why."""
    config = for_backtest(file, strategy, overrides, params_version)
    cover, sessions = coverage(sessions_by_isin, len(isins), start, end, gate_applied=rules is not None)
    base_params = config.strategies[strategy].params
    grid = settings.grids.get(strategy, {}) if not overrides else {}
    base_combo: Combo = tuple((n, str(base_params[n])) for n in sorted(grid))
    all_combos = combos(grid, {n: str(base_params[n]) for n in grid})
    runs: dict[Combo, list[Trade]] = {}
    counts: dict[str, int] = {}
    skipped: str | None = None
    base_seconds = 0.0
    for combo in sorted(all_combos, key=lambda c: c != base_combo):  # the file's own parameters first
        if combo != base_combo and base_seconds * (len(all_combos) - 1) > deadline - monotonic():
            skipped = (
                f"{len(all_combos)} parameter sets would not fit in the time budget "
                f"(one takes {base_seconds:.0f} s): walk-forward ran on the fixed parameters"
            )
            runs = {base_combo: runs[base_combo]}
            break
        began = monotonic()
        this = (
            config
            if combo == base_combo
            else for_backtest(file, strategy, [ParamOverride(name=n, value=v) for n, v in combo], params_version)
        )
        out = run_strategy(this, strategy, isins, load, start, end, settings.execution, costs, rules, tick)
        runs[combo] = out.trades
        if combo == base_combo:
            base_seconds = monotonic() - began
            counts = {
                "candidates": out.candidates,
                "gated_out": out.gated_out,
                "never_filled_or_invalid": out.unfilled_or_invalid,
                "skipped_position_open": out.skipped_busy,
            }
    trades = runs[base_combo]
    return Study(
        trades=trades,
        metrics=summarize(trades),
        walk_forward=walk_forward(runs, base_combo, sessions, settings.walk_forward),
        statistics=equity_stats(trades, sessions, settings.reporting.reference_capital),
        coverage=cover,
        counts=counts,
        grid=grid if skipped is None else {},
        grid_skipped=skipped,
        sessions=sessions,
    )
