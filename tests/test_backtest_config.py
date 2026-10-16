"""The shipped backtest and cost files: they load, and everything they name exists."""

from __future__ import annotations

from tbo_technical_engine.backtest.costs import load_costs
from tests.backtest.builders import ROOT, SETTINGS
from tests.strategies.session import CONFIG


def test_every_grid_names_a_real_strategy_parameter_and_includes_the_current_value() -> None:
    for strategy, grid in SETTINGS.grids.items():
        assert strategy in CONFIG.strategies, strategy
        params = CONFIG.strategies[strategy].params
        for name, values in grid.items():
            assert name in params, f"{strategy}.{name}"
            assert str(params[name]) in values, (
                f"{strategy}.{name}: the current value {params[name]} is not in the grid"
            )


def test_the_runtime_cap_stays_under_the_brokers_redelivery_window() -> None:
    """A longer job would be handed to a second worker while the first still runs (visibility_timeout is 3600 s)."""
    assert SETTINGS.caps.max_runtime_seconds + 180 < 3600


def test_costs_are_a_labelled_placeholder_with_a_source_and_a_date() -> None:
    costs = load_costs(ROOT / "config" / "costs.yaml")
    assert costs.status == "placeholder" and "2026-10-15" in costs.source
