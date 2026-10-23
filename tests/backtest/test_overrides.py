"""Parameter overrides: typed like the file, never code, and a typo is an error."""

from __future__ import annotations

import pytest

from tbo_technical_engine.backtest.overrides import OverrideError, for_backtest
from tbo_technical_engine.schemas.backtest import ParamOverride
from tests.strategies.session import CONFIG


def over(name: str, value: str) -> ParamOverride:
    return ParamOverride(name=name, value=value)


def test_only_the_requested_strategy_is_active() -> None:
    config = for_backtest(CONFIG, "orb_15m", [], None)
    assert [n for n, s in config.strategies.items() if s.is_active] == ["orb_15m"]
    assert all(s.is_active for s in CONFIG.strategies.values())  # the file itself is not changed


def test_an_override_takes_the_type_of_the_value_in_the_file() -> None:
    config = for_backtest(CONFIG, "ema_9_21_crossover", [over("cooldown_minutes", "45"), over("adx_min", "25")], None)
    params = config.strategies["ema_9_21_crossover"].params
    assert params["cooldown_minutes"] == 45 and params["adx_min"] == "25"
    assert CONFIG.strategies["ema_9_21_crossover"].params["cooldown_minutes"] == 30


def test_a_flag_takes_true_or_false_only() -> None:
    config = for_backtest(CONFIG, "vwap_pivot", [over("require_index", "true")], None)
    assert config.strategies["vwap_pivot"].params["require_index"] is True
    with pytest.raises(OverrideError):
        for_backtest(CONFIG, "vwap_pivot", [over("require_index", "yes")], None)


@pytest.mark.parametrize(
    ("strategy", "overrides", "version", "field"),
    [
        ("nope", [], None, "strategy"),
        ("orb_15m", [over("stop_atr_typo", "1")], None, "params_overrides.stop_atr_typo"),
        ("orb_15m", [over("cooldown_minutes", "soon")], None, "params_overrides.cooldown_minutes"),
        ("orb_15m", [], "2020.01.1", "params_version"),
    ],
)
def test_bad_requests_name_the_field(
    strategy: str, overrides: list[ParamOverride], version: str | None, field: str
) -> None:
    with pytest.raises(OverrideError) as caught:
        for_backtest(CONFIG, strategy, overrides, version)
    assert caught.value.field == field


def test_the_current_version_is_accepted() -> None:
    assert for_backtest(CONFIG, "orb_15m", [], CONFIG.params_version).params_version == CONFIG.params_version
