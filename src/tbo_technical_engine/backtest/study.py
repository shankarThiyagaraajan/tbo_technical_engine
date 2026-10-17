"""One whole study of a strategy: the run at the file's parameters, the other parameter sets of its grid, and the
walk-forward and statistics built from them. Synchronous and free of storage, so the job (`services/backtests.py`) and
the report script (`scripts/backtest_report.py`) run exactly the same code.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date

from tbo_costs import ScheduleBook

from tbo_technical_engine.backtest.engine import DayRules, coverage, run_grid
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
    costs: ScheduleBook,
    rules: DayRules | None,
    *,
    tick: Callable[[], None] | None = None,
    monotonic: Callable[[], float],
    deadline: float,
) -> Study:
    """Parameter sets beyond the file's own only when they fit in the time left: after the first stock, the time it took
    for all sets times the number of stocks must fit before `deadline`, else the walk-forward uses the fixed parameters
    and the result says why."""
    config = for_backtest(file, strategy, overrides, params_version)
    cover, sessions = coverage(sessions_by_isin, len(isins), start, end, gate_applied=rules is not None)
    base_params = config.strategies[strategy].params
    grid = settings.grids.get(strategy, {}) if not overrides else {}
    base_combo: Combo = tuple((n, str(base_params[n])) for n in sorted(grid))
    all_combos = combos(grid, {n: str(base_params[n]) for n in grid})
    configs = {
        combo: (
            config
            if combo == base_combo
            else for_backtest(file, strategy, [ParamOverride(name=n, value=v) for n, v in combo], params_version)
        )
        for combo in sorted(all_combos, key=lambda c: c != base_combo)  # the file's own parameters first
    }
    outcomes, skipped = run_grid(
        configs, strategy, isins, load, start, end, settings.execution, costs, rules, tick,
        monotonic=monotonic, deadline=deadline,
    )  # fmt: skip
    runs: dict[Combo, list[Trade]] = {combo: out.trades for combo, out in outcomes.items()}
    base_out = outcomes[base_combo]
    counts = {
        "candidates": base_out.candidates,
        "gated_out": base_out.gated_out,
        "never_filled_or_invalid": base_out.unfilled_or_invalid,
        "skipped_position_open": base_out.skipped_busy,
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
