"""The fill and exit rules, one scenario each, with numbers that can be checked on paper.

Candidate: buy, limit 100.20 (far edge of the zone), stop 99.00, T1 101.50, T2 102.50; 1,000 shares at 100.00
(notional 1,00,000), 500 sold at T1.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from tbo_technical_engine.backtest.costs import leg_charges
from tbo_technical_engine.backtest.simulate import simulate
from tbo_technical_engine.schemas.candle import Candle
from tbo_technical_engine.schemas.signal import SignalCandidate
from tests.backtest.builders import COSTS, at, bar, candidate, execution, flat, free

D = Decimal


def run(after: Sequence[Candle], cand: SignalCandidate | None = None, **exec_changes: Any) -> Any:
    return simulate(cand or candidate(), after, execution(**exec_changes), free())


def test_a_better_open_fills_at_the_open_and_both_targets_pay() -> None:
    trade = run(
        [
            bar(11, "100.00", "100.50", "99.50", "100.20"),
            bar(12, "100.20", "101.60", "100.10", "101.50"),  # T1: 500 at 101.50, stop to 100.00
            bar(13, "101.50", "102.60", "101.40", "102.50"),  # T2: 500 at 102.50
        ]
    )
    assert trade is not None
    assert (trade.entry_price, trade.quantity, trade.exit_reason, trade.target_1_hit) == (
        D("100.00"),
        1000,
        "target_2",
        True,
    )
    assert trade.gross_pnl == D("2000.00")  # 500 x 1.50 + 500 x 2.50
    assert trade.r == D("2.0000")  # risk 1.00 x 1000 shares
    assert trade.exit_price == D("102.50")


def test_the_stop_is_one_r() -> None:
    trade = run([bar(11, "100.00", "100.30", "98.90", "99.10")])
    assert trade is not None and trade.exit_reason == "stop"
    assert (trade.gross_pnl, trade.r) == (D("-1000.00"), D("-1.0000"))


def test_a_gap_through_the_stop_fills_at_the_open_not_the_stop() -> None:
    trade = run([bar(11, "100.00", "100.30", "99.50", "100.10"), bar(12, "98.50", "98.60", "98.40", "98.50")])
    assert trade is not None
    assert trade.exit_price == D("98.50") and trade.r == D("-1.5000")


def test_a_fill_already_beyond_the_stop_is_no_trade() -> None:
    assert run([bar(11, "98.50", "98.60", "98.40", "98.50")]) is None


def test_an_order_the_market_never_reaches_does_not_fill() -> None:
    assert run([bar(11, "101.00", "101.30", "100.50", "101.00")]) is None


def test_the_order_waits_the_configured_number_of_candles_and_fills_at_the_limit() -> None:
    after = [bar(11, "101.00", "101.30", "100.50", "101.00"), bar(12, "101.00", "101.10", "100.10", "100.40")]
    assert run(after) is None
    trade = run(after, entry_wait_bars=2)
    assert trade is not None and trade.entry_price == D("100.20") and trade.entry_at == at(12)
    assert trade.quantity == 998  # floor(100000 / 100.20)


def test_after_target_1_the_stop_on_the_rest_moves_to_the_entry() -> None:
    trade = run(
        [
            bar(11, "100.00", "100.50", "99.50", "100.20"),
            bar(12, "100.20", "101.60", "100.10", "101.50"),
            bar(13, "101.40", "101.50", "99.90", "100.00"),  # back through the entry: the rest stops at 100.00
        ]
    )
    assert trade is not None and trade.exit_reason == "stop_at_entry"
    assert trade.gross_pnl == D("750.00")  # 500 x 1.50 at T1, 500 x 0 at the entry
    assert trade.r == D("0.7500")


def test_the_stop_stays_put_when_the_setting_says_so() -> None:
    trade = run(
        [
            bar(11, "100.00", "100.50", "99.50", "100.20"),
            bar(12, "100.20", "101.60", "100.10", "101.50"),
            bar(13, "101.40", "101.50", "98.90", "99.00"),
        ],
        stop_to_entry_after_t1=False,
    )
    assert trade is not None and trade.exit_reason == "stop"
    assert trade.gross_pnl == D("250.00")  # 750 at T1 less 500 x 1.00 at the stop


def test_a_candle_that_touches_stop_and_target_counts_as_the_stop() -> None:
    trade = run([bar(11, "100.00", "101.60", "98.90", "100.50")])
    assert trade is not None and trade.exit_reason == "stop" and trade.target_1_hit is False


def test_whatever_is_left_leaves_at_the_open_of_the_square_off_candle() -> None:
    after = [
        bar(11, "100.00", "100.10", "99.90", "100.00"),
        *flat(12, 71, "100.00"),
        bar(72, "100.30", "100.40", "100.20", "100.35"),
    ]
    trade = run(after)
    assert trade is not None and trade.exit_reason == "time_exit"
    assert trade.exit_price == D("100.30") and trade.gross_pnl == D("300.00") and trade.exit_at == at(72)


def test_data_that_ends_inside_the_session_is_closed_at_the_last_close_and_says_so() -> None:
    trade = run([bar(11, "100.00", "100.10", "99.90", "100.00"), bar(12, "100.00", "100.20", "99.80", "100.15")])
    assert trade is not None and trade.exit_reason == "end_of_data" and trade.exit_price == D("100.15")


def test_a_short_is_the_mirror_image() -> None:
    cand = candidate("sell", low="100.00", high="100.20", stop="101.00", t1="98.50", t2="97.50")
    trade = run(
        [
            bar(11, "100.00", "100.50", "99.50", "99.80"),
            bar(12, "99.80", "99.90", "98.40", "98.50"),
            bar(13, "98.50", "98.60", "97.40", "97.50"),
        ],
        cand,
    )
    assert trade is not None and trade.side == "sell" and trade.exit_reason == "target_2"
    assert trade.gross_pnl == D("2000.00") and trade.r == D("2.0000")


def test_slippage_works_against_the_trader_on_both_fills() -> None:
    trade = run([bar(11, "100.00", "100.30", "98.90", "99.10")], slippage_bps=D(100))  # 1%
    assert trade is not None
    assert trade.entry_price == D("101.00")  # bought 1% dearer
    assert trade.exit_price == D("98.01")  # stop at 99.00 sold 1% cheaper
    assert trade.quantity == 990  # floor(100000 / 101.00)
    assert trade.gross_pnl == D("-2960.10")  # 990 x (98.01 - 101.00)
    assert trade.r == (D("-2960.10") / (D("2.00") * 990)).quantize(D("0.0001"))


def test_charges_are_taken_for_the_entry_and_every_exit_leg() -> None:
    trade = simulate(
        candidate(),
        [
            bar(11, "100.00", "100.50", "99.50", "100.20"),
            bar(12, "100.20", "101.60", "100.10", "101.50"),
            bar(13, "101.50", "102.60", "101.40", "102.50"),
        ],
        execution(),
        COSTS,
    )
    assert trade is not None
    expected = (
        leg_charges(COSTS, "buy", D("100.00"), 1000)
        + leg_charges(COSTS, "sell", D("101.50"), 500)
        + leg_charges(COSTS, "sell", D("102.50"), 500)
    )
    assert trade.costs == expected.quantize(D("0.01"))
    assert trade.r < trade.gross_r  # charges only ever reduce R


def test_an_order_does_not_live_over_the_night() -> None:
    from datetime import date

    next_day = bar(0, "100.00", "100.30", "99.90", "100.10", day=date(2026, 10, 16))
    assert run([next_day], candidate(index=74)) is None


def test_a_limit_fill_credits_no_target_from_the_fill_candle() -> None:
    """9.2: the candle opened above the limit and came back; its 102.60 high may have printed before the fill."""
    after = [bar(11, "100.60", "102.60", "100.10", "102.00"), *flat(12, 13, "100.40")]
    trade = run(after)
    assert trade is not None and trade.entry_price == D("100.20")
    assert trade.target_1_hit is False  # not +0.54 R booked on a high that predates the fill


def test_a_limit_fill_candle_can_still_stop_the_trade_out() -> None:
    trade = run([bar(11, "100.60", "102.60", "98.90", "99.50")])
    assert trade is not None and trade.exit_reason == "stop"


def test_a_better_open_fill_still_credits_the_fill_candle_target() -> None:
    trade = run([bar(11, "100.00", "101.60", "99.90", "101.50"), *flat(12, 13, "101.50")])
    assert trade is not None and trade.target_1_hit is True
