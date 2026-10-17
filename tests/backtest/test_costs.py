"""The charges a backtest pays come from the kit's schedule; hand numbers for the figures TE5 has always used."""

from __future__ import annotations

from decimal import Decimal

from tbo_costs import Order, ScheduleBook, charge_orders

from tests.backtest.builders import COSTS, DAY, free
from tests.support import ISIN_A


def charge(side: str, price: str, quantity: int, book: ScheduleBook = COSTS) -> Decimal:
    order = Order(
        scrip=ISIN_A, segment="intraday", exchange="NSE", side=side, price=price, quantity=quantity, trade_date=DAY
    )
    return charge_orders([order], book).total


def test_a_buy_of_100_at_500_costs_what_the_schedule_says() -> None:
    # brokerage 15.00 + exchange 1.535 + SEBI 0.05 + IPFT 0.00005 + GST 18% of those (2.985309) + stamp 1.50
    assert charge("buy", "500", 100) == Decimal("21.070359")


def test_a_sell_pays_stt_instead_of_stamp_duty() -> None:
    assert charge("sell", "500", 100) == Decimal("32.070359")


def test_a_free_schedule_costs_nothing() -> None:
    assert charge("sell", "500", 100, free()) == 0
