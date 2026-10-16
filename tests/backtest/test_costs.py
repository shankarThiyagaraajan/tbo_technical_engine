"""The cost model on hand numbers (the placeholder schedule in config/costs.yaml)."""

from __future__ import annotations

from decimal import Decimal

from tbo_technical_engine.backtest.costs import leg_charges
from tests.backtest.builders import COSTS, free


def test_a_buy_of_100_at_500_costs_what_the_schedule_says() -> None:
    # brokerage 15.00 + exchange 1.535 + SEBI 0.05 + IPFT 0.00005 + GST 18% of those (2.985309) + stamp 1.50
    assert leg_charges(COSTS, "buy", Decimal("500"), 100) == Decimal("21.070359")


def test_a_sell_pays_stt_instead_of_stamp_duty() -> None:
    assert leg_charges(COSTS, "sell", Decimal("500"), 100) == Decimal("32.070359")


def test_brokerage_is_capped_per_order() -> None:
    # 10,00,000 turnover: 0.03% would be 300, the cap is 20.
    big = leg_charges(COSTS, "buy", Decimal("1000"), 1000)
    uncapped = COSTS.model_copy(update={"brokerage_cap": Decimal("1000000")})
    assert leg_charges(uncapped, "buy", Decimal("1000"), 1000) - big > Decimal("280")


def test_a_free_schedule_costs_nothing() -> None:
    assert leg_charges(free(), "sell", Decimal("500"), 100) == 0


def test_the_shipped_figures_are_marked_placeholder() -> None:
    """Until the owner confirms the figures, every backtest says its costs are a placeholder."""
    assert COSTS.status == "placeholder"
