"""One backtest: candles in, trades out, using the live strategy code.

`run_grid` builds each stock's tape once (`build_tape`) and replays it for every parameter set (`replay_tape`), drops
candidates the point-in-time universe or gate would not have allowed that day, and simulates each remaining one. It is
synchronous and CPU-bound on purpose: it runs on the research queue's own workers, never in the API process or the
signal worker.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, time
from typing import Literal

from tbo_platform_kit.config.calendar import IST

from tbo_technical_engine.backtest.replay import Tape, build_tape, entry_windows, replay_tape
from tbo_technical_engine.backtest.simulate import simulate
from tbo_technical_engine.backtest.walkforward import Combo
from tbo_technical_engine.schemas.backtest import Coverage, Execution, Side, StockData, Trade
from tbo_technical_engine.schemas.costs import CostSchedule
from tbo_technical_engine.schemas.strategy import StrategyFile
from tbo_technical_engine.services.evaluator import IndexSide
from tbo_technical_engine.utils.timeframes import length

GateStatus = Literal["block", "long_only", "short_only", "both"]
ALLOWED: dict[str, tuple[Side, ...]] = {
    "block": (),
    "long_only": ("buy",),
    "short_only": ("sell",),
    "both": ("buy", "sell"),
}


@dataclass(frozen=True)
class DayRules:
    """The point-in-time universe and gate: for each trading day, the gate status of each stock that was in the
    universe that day. A stock missing for a day was not tradable; a day missing altogether means no trades that day."""

    by_day: Mapping[date, Mapping[str, str]]

    def allows(self, isin: str, day: date, side: Side) -> bool:
        status = self.by_day.get(day, {}).get(isin)
        return status is not None and side in ALLOWED.get(status, ())


@dataclass
class RunOutcome:
    """What one strategy run produced: its trades and how much of the requested range had data."""

    trades: list[Trade] = field(default_factory=list)
    candidates: int = 0
    gated_out: int = 0
    unfilled_or_invalid: int = 0
    """Candidates whose order never filled in time, or filled at or beyond the stop."""
    skipped_busy: int = 0
    """Candidates skipped because the stock + strategy already had a trade open (`one_position_per_stock`)."""


Progress = Callable[[], None]


@dataclass(frozen=True)
class _Grid:
    """What every stock of one grid run shares: the parameter sets, the range, and the fill and cost rules."""

    configs: Mapping[Combo, StrategyFile]
    base: Combo
    start: date
    end: date
    windows: list[tuple[time, time]]
    index_side: IndexSide | None
    execution: Execution
    schedule: CostSchedule
    rules: DayRules | None


def run_grid(
    configs: Mapping[Combo, StrategyFile],
    isins: Sequence[str],
    load: Callable[[str], StockData | None],
    start: date,
    end: date,
    execution: Execution,
    schedule: CostSchedule,
    rules: DayRules | None,
    tick: Progress | None = None,
    *,
    monotonic: Callable[[], float] | None = None,
    deadline: float = float("inf"),
    index_side: IndexSide | None = None,
) -> tuple[dict[Combo, RunOutcome], str | None]:
    """Run every parameter set of `configs` over the stocks, one stock at a time.

    The stock is loaded and its tape built once, then each parameter set replays the tape. (Run set after set, each
    would load and replay the stock again; the indicators are the same for all of them.)

    The first entry of `configs` is the file's own parameter set and always runs. After the first stock the others
    are dropped when `first stock's time x number of stocks` would not fit before `deadline`; the second return value
    then says why (None when everything ran). `load` returns a stock's candles (None: no data); each config has one
    strategy active and the others off (`overrides.for_backtest`). `tick` is called once per stock so a caller can
    enforce a time budget. `index_side` is the primary index's VWAP side for `vwap_pivot` (None: no index data).
    """
    base = next(iter(configs))
    grid = _Grid(
        configs=configs,
        base=base,
        start=start,
        end=end,
        windows=[w for config in configs.values() for w in entry_windows(config)],
        index_side=index_side,
        execution=execution,
        schedule=schedule,
        rules=rules,
    )
    outcomes: dict[Combo, RunOutcome] = {combo: RunOutcome() for combo in configs}
    clock = monotonic or (lambda: 0.0)
    skipped: str | None = None
    first = True
    for isin in sorted(isins):
        stock = load(isin)  # one stock at a time: 500 stocks of a year of candles do not fit in memory together
        if stock is not None:
            began = clock()
            _replay_stock(grid, outcomes, isin, stock)
            if first and monotonic is not None and len(outcomes) > 1:
                first = False
                skipped = _over_budget(clock() - began, len(configs), len(isins), deadline - clock())
                if skipped is not None:
                    outcomes = {base: outcomes[base]}  # the first stock's trades of the dropped sets go with them
        if tick is not None:
            tick()
    for out in outcomes.values():
        out.trades.sort(key=lambda t: (t.entry_at, t.isin, t.signal_id))
    return outcomes, skipped


def _replay_stock(grid: _Grid, outcomes: dict[Combo, RunOutcome], isin: str, stock: StockData) -> None:
    """Build the stock's tape once and trade it for every parameter set still in `outcomes`."""
    tape = build_tape(
        grid.configs[grid.base],
        stock.five,
        stock.fifteen,
        stock.daily,
        grid.start,
        grid.end,
        grid.windows,
        grid.index_side,
    )
    for combo, out in outcomes.items():
        _trade_stock(out, grid.configs[combo], tape, isin, stock, grid.execution, grid.schedule, grid.rules)


def _over_budget(took: float, sets: int, stocks: int, left: float) -> str | None:
    """Why the extra parameter sets are dropped, when the first stock's time for all of them, times the stocks, will
    not fit in the time left; None when it fits."""
    if took * stocks <= left:
        return None
    return (
        f"{sets} parameter sets would not fit in the time budget (the first stock took "
        f"{took:.0f} s for all of them, and there are {stocks} stocks): walk-forward ran on the "
        "fixed parameters"
    )


def _trade_stock(
    out: RunOutcome,
    config: StrategyFile,
    tape: Tape,
    isin: str,
    stock: StockData,
    execution: Execution,
    schedule: CostSchedule,
    rules: DayRules | None,
) -> None:
    """One parameter set's candidates for one stock, filled and exited, added to `out`."""
    candidates = replay_tape(config, tape)
    out.candidates += len(candidates)
    starts = {c.start_at: i for i, c in enumerate(stock.five)}
    open_until = None
    for cand in candidates:
        if rules is not None and not rules.allows(isin, cand.candle_start_at.astimezone(IST).date(), cand.side):
            out.gated_out += 1
            continue
        i = starts.get(cand.candle_start_at)
        if i is None:
            continue
        close_at = cand.candle_start_at + length("5m")
        if execution.one_position_per_stock and open_until is not None and close_at < open_until:
            out.skipped_busy += 1
            continue
        trade = simulate(cand, stock.five[i + 1 :], execution, schedule)
        if trade is None:
            out.unfilled_or_invalid += 1
            continue
        out.trades.append(trade)
        open_until = trade.exit_at


def run_strategy(
    config: StrategyFile,
    isins: Sequence[str],
    load: Callable[[str], StockData | None],
    start: date,
    end: date,
    execution: Execution,
    schedule: CostSchedule,
    rules: DayRules | None,
    tick: Progress | None = None,
) -> RunOutcome:
    """One parameter set over the stocks (`run_grid` with a single set)."""
    outcomes, _ = run_grid({(): config}, isins, load, start, end, execution, schedule, rules, tick)
    return outcomes[()]


def coverage(
    sessions_by_isin: Mapping[str, set[date]], requested: int, start: date, end: date, gate_applied: bool
) -> tuple[Coverage, list[date]]:
    """What the candles really cover. `sessions` are the dates in [start, end] on which any stock has 5-minute candles.
    A report over 12 months on 3 months of candles must say so, so partial coverage is written into `note`."""
    every = sorted({d for days in sessions_by_isin.values() for d in days})
    sessions = [d for d in every if start <= d <= end]
    with_data = sum(1 for days in sessions_by_isin.values() if any(start <= d <= end for d in days))
    span = (end - start).days + 1
    if sessions:
        note = f"{len(sessions)} sessions with candles between {start} and {end} ({sessions[0]} to {sessions[-1]})"
        if len(sessions) < span * 5 / 7 * 0.85:
            note += f"; the range is {span} calendar days, so coverage is partial"
    else:
        note = f"no candles between {start} and {end}"
    return (
        Coverage(
            requested_isins=requested,
            isins_with_data=with_data,
            first_candle_date=sessions[0] if sessions else None,
            last_candle_date=sessions[-1] if sessions else None,
            trading_days=len(sessions),
            skipped_no_data=requested - with_data,
            gate_applied=gate_applied,
            note=note,
        ),
        sessions,
    )
