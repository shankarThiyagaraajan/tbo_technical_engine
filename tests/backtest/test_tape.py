"""The tape split and the grid: sharing a stock's contexts between parameter sets changes nothing in the results."""

from __future__ import annotations

from typing import Any

from tbo_technical_engine.backtest import engine as engine_module
from tbo_technical_engine.backtest import replay as replay_module
from tbo_technical_engine.backtest.engine import run_grid, run_strategy
from tbo_technical_engine.backtest.overrides import for_backtest
from tbo_technical_engine.backtest.replay import build_tape, entry_windows, replay_stock, replay_tape
from tbo_technical_engine.backtest.walkforward import Combo
from tbo_technical_engine.strategies.base import finite
from tests.backtest.builders import COSTS, SETTINGS
from tests.backtest.test_replay import FIRST, HISTORY, LAST, load
from tests.strategies.session import CONFIG
from tests.support import ISIN_A

BASE: Combo = ()
OTHER: Combo = (("x", "1"),)


def configs() -> dict[Combo, Any]:
    return {BASE: for_backtest(CONFIG, "vwap_pivot", [], None), OTHER: for_backtest(CONFIG, "vwap_breakout", [], None)}


def test_a_tape_replayed_gives_what_replay_stock_gives() -> None:
    config = configs()[BASE]
    tape = build_tape(config, HISTORY.five, HISTORY.fifteen, HISTORY.daily, FIRST, LAST)
    expected = replay_stock(config, HISTORY.five, HISTORY.fifteen, HISTORY.daily, FIRST, LAST)
    assert replay_tape(config, tape) == expected and expected


def test_a_tape_built_for_the_union_of_windows_serves_another_parameter_set() -> None:
    both = configs()
    windows = [w for c in both.values() for w in entry_windows(c)]
    tape = build_tape(both[BASE], HISTORY.five, HISTORY.fifteen, HISTORY.daily, FIRST, LAST, windows)
    for config in both.values():
        expected = replay_stock(config, HISTORY.five, HISTORY.fifteen, HISTORY.daily, FIRST, LAST)
        assert replay_tape(config, tape) == expected


def test_the_grid_gives_each_set_exactly_what_a_run_of_its_own_gives() -> None:
    both = configs()
    outcomes, skipped = run_grid(both, "vwap_pivot", [ISIN_A], load, FIRST, LAST, SETTINGS.execution, COSTS, None)
    assert skipped is None
    for combo, config in both.items():
        alone = run_strategy(config, "vwap_pivot", [ISIN_A], load, FIRST, LAST, SETTINGS.execution, COSTS, None)
        assert outcomes[combo] == alone and alone.candidates > 0


def test_a_grid_that_does_not_fit_the_budget_keeps_only_the_base_set(monkeypatch: Any) -> None:
    clock = [0.0]
    real = replay_module.build_tape

    def slow(*args: Any, **kwargs: Any) -> Any:
        clock[0] += 3000.0
        return real(*args, **kwargs)

    monkeypatch.setattr(engine_module, "build_tape", slow)
    outcomes, skipped = run_grid(
        configs(), "vwap_pivot", [ISIN_A, "INE000002A02"], lambda _i: load(""), FIRST, LAST,
        SETTINGS.execution, COSTS, None, monotonic=lambda: clock[0], deadline=3600.0,
    )  # fmt: skip
    assert list(outcomes) == [BASE] and skipped is not None and "time budget" in skipped


def test_finite_accepts_numbers_and_refuses_nan_and_infinity() -> None:
    nan, inf = float("nan"), float("inf")
    assert finite(1.0, 2.0) and not finite(1.0, nan) and not finite(inf)
