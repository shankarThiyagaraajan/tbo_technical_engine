"""The shipped backtest and cost files: they load, and everything they name exists."""

from __future__ import annotations

from tbo_costs import default_book

from tests.backtest.builders import SETTINGS
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


def test_costs_are_the_kits_schedule_and_say_they_are_not_confirmed_yet() -> None:
    costs = default_book()
    assert costs.status == "unverified" and "zerodha.com" in costs.entries[-1].source
