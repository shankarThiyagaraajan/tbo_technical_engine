"""One candidate in, at most one trade out: the kit's fill model and cost calculator, in TE5's trade record.

The fill and exit rules (limit at the far edge of the entry zone, slippage against the trader, stop, target 1 with the
stop moved to the entry, target 2, square-off, a gap fills at the open, the adverse level first on a candle that
touches both) live in `tbo_paper_fills`, so backtests, replay and paper share one set; their text is in that
package's README. Charges come from `tbo_costs` (NSE, an individual account, intraday). Slippage moves the fill price,
so it shows in the gross result and is not counted twice. Pure: no clock, no I/O, so a run is repeatable.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from tbo_costs import Exchange, ScheduleBook
from tbo_paper_fills import FillRules, Setup, cents, gross_pnl, simulate_trade, trade_costs
from tbo_platform_kit.config.calendar import IST

from tbo_technical_engine.schemas.backtest import Execution, Trade
from tbo_technical_engine.schemas.candle import Candle
from tbo_technical_engine.schemas.signal import SignalCandidate

EXCHANGE: Exchange = "NSE"
"""TE5 has always assumed NSE; the instrument's own exchange arrives with the paper broker."""
FOUR_PLACES = Decimal("0.0001")


def fill_rules(execution: Execution) -> FillRules:
    """The execution settings of a backtest as the kit's fill rules."""
    return FillRules(
        notional_per_trade=execution.notional_per_trade,
        slippage_bps=execution.slippage_bps,
        entry_wait_bars=execution.entry_wait_bars,
        t1_fraction=execution.t1_fraction,
        stop_to_entry_after_t1=execution.stop_to_entry_after_t1,
        square_off=execution.square_off,
    )


def simulate(
    candidate: SignalCandidate,
    after: Sequence[Candle],
    execution: Execution,
    schedule: ScheduleBook,
) -> Trade | None:
    """`after` is the stock's 5-minute candles from the one that follows the signal candle onward, oldest first."""
    setup = Setup(
        side=candidate.side,
        entry_low=Decimal(candidate.entry_low),
        entry_high=Decimal(candidate.entry_high),
        stop=Decimal(candidate.stop),
        target_1=Decimal(candidate.target_1),
        target_2=Decimal(candidate.target_2),
        signal_at=candidate.candle_start_at,
    )
    done = simulate_trade(setup, after, fill_rules(execution))
    if done is None:
        return None
    gross = gross_pnl(done)
    trade_date = candidate.candle_start_at.astimezone(IST).date()
    costs = trade_costs(done, candidate.isin, EXCHANGE, trade_date, schedule)
    scale = done.risk * done.quantity
    return Trade(
        isin=candidate.isin,
        side=candidate.side,
        signal_id=candidate.signal_id,
        entry_at=done.entry_at,
        exit_at=done.exit_at,
        entry_price=done.entry_price,
        exit_price=done.legs[-1].price,
        exit_reason=done.exit_reason,
        quantity=done.quantity,
        gross_pnl=cents(gross),
        costs=cents(costs),
        r=((gross - costs) / scale).quantize(FOUR_PLACES),
        gross_r=(gross / scale).quantize(FOUR_PLACES),
        target_1_hit=done.target_1_hit,
    )
