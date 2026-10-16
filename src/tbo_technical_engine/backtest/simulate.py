"""One candidate in, at most one trade out. Pure: no clock, no I/O, so a run is repeatable.

The fill and exit rules are the owner-visible assumptions of every backtest number, so they are spelled out:

- The signal is known at the close of its 5-minute candle. A limit order at the FAR edge of the entry zone (a buy at
  `entry_high`, a sell at `entry_low`) lives for `entry_wait_bars` candles. It fills at the candle's open if the open is
  already better than the limit, else at the limit if the candle reaches it, else the trade never happens.
- Slippage is applied against the trader on every fill.
- Exits: stop, target 1 (sells `t1_fraction`, then the stop on the rest moves to the entry), target 2, and at
  `square_off` whatever is left leaves at that candle's open. A gap through a level fills at the open, not the level.
- A candle that touches both an adverse and a favourable level counts the adverse one first (`stop_first`). Once the
  position is open this is also assumed for the fill candle itself. The backtester has candles, not ticks, so it
  cannot know the order, and guessing the good one flatters every strategy.
- A fill at or beyond the stop (a gap through it) is no trade: the risk would be zero or negative.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import time
from decimal import ROUND_HALF_EVEN, Decimal

from tbo_platform_kit.calendar import IST

from tbo_technical_engine.backtest.costs import leg_charges
from tbo_technical_engine.schemas.backtest import Execution, ExitReason, Side, Trade
from tbo_technical_engine.schemas.candle import Candle
from tbo_technical_engine.schemas.costs import CostSchedule
from tbo_technical_engine.schemas.signal import SignalCandidate
from tbo_technical_engine.utils.timeframes import length

CENT = Decimal("0.01")
BPS = Decimal(10_000)


def cents(value: Decimal) -> Decimal:
    """Round a rupee amount to paise (half up), the unit every charge is stated in."""
    return value.quantize(CENT, rounding=ROUND_HALF_EVEN)


@dataclass
class _Leg:
    quantity: int
    price: Decimal


def simulate(  # noqa: C901  one candle-by-candle state machine (fill, stop, targets, close); split pieces would share its mutable state
    candidate: SignalCandidate,
    after: Sequence[Candle],
    execution: Execution,
    schedule: CostSchedule,
) -> Trade | None:
    """`after` is the stock's 5-minute candles from the one that follows the signal candle onward, oldest first."""
    side: Side = candidate.side
    d = 1 if side == "buy" else -1
    slip = execution.slippage_bps / BPS
    stop = Decimal(candidate.stop)
    target_1 = Decimal(candidate.target_1)
    target_2 = Decimal(candidate.target_2)
    limit = Decimal(candidate.entry_high if side == "buy" else candidate.entry_low)
    signal_day = candidate.candle_start_at.astimezone(IST).date()
    square_off = time.fromisoformat(execution.square_off)

    fill: Decimal | None = None
    fill_index = -1
    for i, c in enumerate(after[: execution.entry_wait_bars]):
        if c.start_at.astimezone(IST).date() != signal_day:
            break  # an order does not live over the night
        better_open = c.open <= limit if side == "buy" else c.open >= limit
        reached = c.low <= limit if side == "buy" else c.high >= limit
        if better_open:
            fill, fill_index = c.open, i
            break
        if reached:
            fill, fill_index = limit, i
            break
    if fill is None:
        return None
    entry = cents(fill * (1 + d * slip))
    risk = (entry - stop) * d
    if risk <= 0:
        return None
    quantity = math.floor(execution.notional_per_trade / entry)
    if quantity < 1:
        return None
    first_qty = math.floor(quantity * execution.t1_fraction)

    legs: list[_Leg] = []
    remaining = quantity
    level = stop
    stop_moved = False
    t1_hit = False
    reason: ExitReason = "end_of_data"
    last_at = after[fill_index].start_at + length("5m")

    def sell(qty: int, at: Decimal) -> None:
        legs.append(_Leg(qty, cents(at * (1 - d * slip))))

    done = False
    for c in after[fill_index:]:
        local = c.start_at.astimezone(IST)
        if local.date() != signal_day:
            break  # the session ended without a square-off candle: close at the last close below
        last_at = c.start_at + length("5m")
        if local.time() >= square_off:
            sell(remaining, c.open)
            remaining, reason, last_at, done = 0, "time_exit", c.start_at, True
            break
        adverse = (c.low <= level) if side == "buy" else (c.high >= level)
        if adverse:
            gap = (c.open <= level) if side == "buy" else (c.open >= level)
            sell(remaining, c.open if gap else level)
            remaining, done = 0, True
            reason = "stop_at_entry" if stop_moved else "stop"
            break
        reached_1 = (c.high >= target_1) if side == "buy" else (c.low <= target_1)
        if not t1_hit and reached_1:
            t1_hit = True
            gap = (c.open >= target_1) if side == "buy" else (c.open <= target_1)
            if first_qty:
                sell(first_qty, c.open if gap else target_1)
                remaining -= first_qty
            if execution.stop_to_entry_after_t1:
                level, stop_moved = entry, True
                if (c.low <= level) if side == "buy" else (c.high >= level):
                    sell(remaining, level)
                    remaining, done, reason = 0, True, "stop_at_entry"
                    break
        reached_2 = (c.high >= target_2) if side == "buy" else (c.low <= target_2)
        if reached_2 and t1_hit:
            gap = (c.open >= target_2) if side == "buy" else (c.open <= target_2)
            sell(remaining, c.open if gap else target_2)
            remaining, done, reason = 0, True, "target_2"
            break
    if not done:
        # Data ended inside the session: close at the last close we have, and say so.
        seen = [c for c in after[fill_index:] if c.start_at.astimezone(IST).date() == signal_day]
        sell(remaining, seen[-1].close)
        last_at = seen[-1].start_at + length("5m")

    gross = sum(((leg.price - entry) * d * leg.quantity for leg in legs), Decimal(0))
    costs = leg_charges(schedule, side, entry, quantity)
    exit_side: Side = "sell" if side == "buy" else "buy"
    costs += sum((leg_charges(schedule, exit_side, leg.price, leg.quantity) for leg in legs), Decimal(0))
    r = (gross - costs) / (risk * quantity)
    gross_r = gross / (risk * quantity)
    return Trade(
        isin=candidate.isin,
        side=side,
        signal_id=candidate.signal_id,
        entry_at=after[fill_index].start_at,
        exit_at=last_at,
        entry_price=entry,
        exit_price=legs[-1].price,
        exit_reason=reason,
        quantity=quantity,
        gross_pnl=cents(gross),
        costs=cents(costs),
        r=r.quantize(Decimal("0.0001")),
        gross_r=gross_r.quantize(Decimal("0.0001")),
        target_1_hit=t1_hit,
    )
