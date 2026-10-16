"""Walk-forward evaluation and the parameter-stability report.

Each fold chooses, among the grid in `config/backtest.yaml`, the parameter set with the best expectancy on its TRAIN
window, then is judged on the TEST window that follows. The out-of-sample result is the test windows only, pooled; train
results are reported on their own and never mixed in. Windows are trading days, counted from the sessions the candles
actually cover, and they do not overlap in test.

A parameter set is run once over the whole range and its trades are cut into windows by entry date. That equals running
each window separately because the strategies are pure rules and the only state (cooldown, open position) never survives
a session.
"""

from __future__ import annotations

import itertools
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal

from tbo_platform_kit.calendar import IST

from tbo_technical_engine.backtest.metrics import summarize
from tbo_technical_engine.schemas.backtest import Fold, Metrics, Trade, WalkForward, WalkForwardSettings

Combo = tuple[tuple[str, str], ...]
"""A parameter set as sorted (name, value) pairs: hashable, and the same set always has the same key."""


def combos(grid: Mapping[str, Sequence[str]], base: Mapping[str, str]) -> list[Combo]:
    """Every combination of the grid, plus the base values if they are not one of them. No grid: just the base."""
    names = sorted(grid)
    out = [tuple(zip(names, values, strict=True)) for values in itertools.product(*(grid[n] for n in names))]
    base_combo: Combo = tuple((n, base[n]) for n in names)
    if base_combo not in out:
        out.append(base_combo)
    return out


def windows(sessions: Sequence[date], settings: WalkForwardSettings) -> list[tuple[list[date], list[date]]]:
    """(train days, test days) pairs, moving forward by `test_days`; none when the data is too short for one."""
    out = []
    i = 0
    while i + settings.train_days + settings.test_days <= len(sessions):
        train = list(sessions[i : i + settings.train_days])
        test = list(sessions[i + settings.train_days : i + settings.train_days + settings.test_days])
        out.append((train, test))
        i += settings.test_days
    return out


def in_window(trades: Sequence[Trade], days: Sequence[date]) -> list[Trade]:
    """The trades whose entry day falls inside the given sessions."""
    wanted = set(days)
    return [t for t in trades if t.entry_at.astimezone(IST).date() in wanted]


def _neighbours(chosen: Combo, others: Sequence[Combo]) -> list[Combo]:
    """Sets that differ from `chosen` in exactly one parameter."""
    return [c for c in others if c != chosen and sum(a != b for a, b in zip(c, chosen, strict=True)) == 1]


def walk_forward(
    results: Mapping[Combo, Sequence[Trade]], base: Combo, sessions: Sequence[date], settings: WalkForwardSettings
) -> WalkForward:
    """`results` maps each parameter set to the trades it made over the whole range."""
    has_grid = len(results) > 1
    folds: list[Fold] = []
    train_all: list[Trade] = []
    test_all: list[Trade] = []
    choices: dict[str, dict[str, int]] = {}
    for train_days, test_days in windows(sessions, settings):
        train_metrics: dict[Combo, Metrics] = {c: summarize(in_window(t, train_days)) for c, t in results.items()}
        eligible = {c: m for c, m in train_metrics.items() if m.trade_count >= settings.min_trades_train}
        # Ties go to the base set (the one in strategies.yaml), then to the sorted key.
        chosen = max(eligible, key=lambda c: (eligible[c].expectancy_r, c == base, c)) if eligible else base
        stable: bool | None = None
        if has_grid:
            best = train_metrics[chosen].expectancy_r
            near = [n for n in _neighbours(chosen, list(eligible)) if n in eligible]
            stable = (
                bool(eligible)
                and best > 0
                and all(eligible[n].expectancy_r >= best * settings.stability_band for n in near)
            )
        train_trades = in_window(results[chosen], train_days)
        test_trades = in_window(results[chosen], test_days)
        train_all += train_trades
        test_all += test_trades
        for name, value in chosen if has_grid else ():
            choices.setdefault(name, {}).setdefault(value, 0)
            choices[name][value] += 1
        folds.append(
            Fold(
                train_from=train_days[0],
                train_to=train_days[-1],
                test_from=test_days[0],
                test_to=test_days[-1],
                chosen=dict(chosen) if has_grid else {},
                train=summarize(train_trades),
                test=summarize(test_trades),
                stable=stable,
            )
        )
    stability = None
    if has_grid and folds:
        stability = (Decimal(sum(1 for f in folds if f.stable)) * 100 / len(folds)).quantize(Decimal("0.01"))
    return WalkForward(
        folds=folds,
        out_of_sample=summarize(test_all),
        in_sample=summarize(train_all),
        stability_pct=stability,
        parameter_choices=choices,
    )
