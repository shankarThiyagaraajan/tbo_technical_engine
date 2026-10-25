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
- When the order fills at the limit (the candle opened beyond it and came back), the candle's extreme on the favourable
  side may have printed BEFORE the fill, so that candle can stop the trade out but credits no target; targets count
  from the next candle (review 9.2). A fill at the open has the whole candle after it, so its targets count.
- A fill at or beyond the stop (a gap through it) is no trade: the risk would be zero or negative.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import ROUND_HALF_EVEN, Decimal

from tbo_platform_kit.config.calendar import IST

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


def _against(side: Side, price: Decimal, level: Decimal) -> bool:
    """`price` is at `level` or beyond it against the trade (at or under it for a buy, at or over it for a sell)."""
    return price <= level if side == "buy" else price >= level


def _with(side: Side, price: Decimal, level: Decimal) -> bool:
    """`price` is at `level` or beyond it in the trade's favour (at or over it for a buy, at or under it for a sell)."""
    return price >= level if side == "buy" else price <= level


def _worst(side: Side, c: Candle) -> Decimal:
    """The candle's extreme against the trade: its low for a buy, its high for a sell."""
    return c.low if side == "buy" else c.high


def _best(side: Side, c: Candle) -> Decimal:
    """The candle's extreme in the trade's favour: its high for a buy, its low for a sell."""
    return c.high if side == "buy" else c.low


@dataclass
class _Position:
    """An open position, candle by candle: what is left, where the stop is, and every exit leg so far."""

    side: Side
    slip: Decimal
    entry: Decimal
    level: Decimal
    remaining: int
    first_qty: int
    last_at: datetime
    legs: list[_Leg] = field(default_factory=list)
    stop_moved: bool = False
    t1_hit: bool = False
    reason: ExitReason = "end_of_data"

    def sell(self, qty: int, at: Decimal) -> None:
        """One exit leg, slipped against the trader."""
        d = 1 if self.side == "buy" else -1
        self.legs.append(_Leg(qty, cents(at * (1 - d * self.slip))))

    def exit_all(self, at: Decimal, reason: ExitReason) -> None:
        """Close what is left at `at`."""
        self.sell(self.remaining, at)
        self.remaining, self.reason = 0, reason

    def stopped(self, c: Candle) -> bool:
        """Leave at the stop (or the open, on a gap through it) when the candle reaches it."""
        if not _against(self.side, _worst(self.side, c), self.level):
            return False
        gap = _against(self.side, c.open, self.level)
        self.exit_all(c.open if gap else self.level, "stop_at_entry" if self.stop_moved else "stop")
        return True

    def targets(self, c: Candle, target_1: Decimal, target_2: Decimal, *, stop_to_entry: bool) -> bool:
        """Target 1 (part out, maybe the stop to the entry), then target 2 (the rest out); True once closed."""
        hit_1 = not self.t1_hit and _with(self.side, _best(self.side, c), target_1)
        if hit_1 and self._first_target(c, target_1, stop_to_entry=stop_to_entry):
            return True
        if self.t1_hit and _with(self.side, _best(self.side, c), target_2):
            self.exit_all(c.open if _with(self.side, c.open, target_2) else target_2, "target_2")
            return True
        return False

    def _first_target(self, c: Candle, target_1: Decimal, *, stop_to_entry: bool) -> bool:
        """Part out at target 1 and maybe move the stop to the entry; True when this candle then stops it out there."""
        self.t1_hit = True
        if self.first_qty:
            self.sell(self.first_qty, c.open if _with(self.side, c.open, target_1) else target_1)
            self.remaining -= self.first_qty
        if not stop_to_entry:
            return False
        self.level, self.stop_moved = self.entry, True
        if not _against(self.side, _worst(self.side, c), self.level):
            return False
        self.exit_all(self.level, "stop_at_entry")
        return True


def simulate(
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
    signal_day = candidate.candle_start_at.astimezone(IST).date()
    filled = _fill(candidate, after, execution.entry_wait_bars, signal_day)
    if filled is None:
        return None
    fill, fill_index, filled_at_limit = filled
    entry = cents(fill * (1 + d * slip))
    risk = (entry - stop) * d
    if risk <= 0:
        return None
    quantity = math.floor(execution.notional_per_trade / entry)
    if quantity < 1:
        return None
    position = _Position(
        side=side,
        slip=slip,
        entry=entry,
        level=stop,
        remaining=quantity,
        first_qty=math.floor(quantity * execution.t1_fraction),
        last_at=after[fill_index].start_at + length("5m"),
    )
    _manage(position, candidate, after[fill_index:], execution, signal_day, filled_at_limit=filled_at_limit)
    return _trade(candidate, position, after[fill_index].start_at, quantity, risk, schedule)


def _fill(
    candidate: SignalCandidate, after: Sequence[Candle], wait_bars: int, signal_day: date
) -> tuple[Decimal, int, bool] | None:
    """The fill price, the index of its candle, and whether it filled at the limit (not at the open); None: no fill."""
    side: Side = candidate.side
    limit = Decimal(candidate.entry_high if side == "buy" else candidate.entry_low)
    for i, c in enumerate(after[:wait_bars]):
        if c.start_at.astimezone(IST).date() != signal_day:
            return None  # an order does not live over the night
        if _against(side, c.open, limit):
            return c.open, i, False
        if _against(side, _worst(side, c), limit):
            return limit, i, True
    return None


def _manage(
    position: _Position,
    candidate: SignalCandidate,
    held: Sequence[Candle],
    execution: Execution,
    signal_day: date,
    *,
    filled_at_limit: bool,
) -> None:
    """Walk the candles from the fill on: square-off, stop, then targets; data ending early closes at the last close."""
    square_off = time.fromisoformat(execution.square_off)
    target_1 = Decimal(candidate.target_1)
    target_2 = Decimal(candidate.target_2)
    for index, c in enumerate(held):
        local = c.start_at.astimezone(IST)
        if local.date() != signal_day:
            break  # the session ended without a square-off candle: close at the last close below
        position.last_at = c.start_at + length("5m")
        if local.time() >= square_off:
            position.exit_all(c.open, "time_exit")
            position.last_at = c.start_at
            return
        if position.stopped(c):
            return
        if index == 0 and filled_at_limit:
            continue  # its favourable extreme may predate the fill (9.2)
        if position.targets(c, target_1, target_2, stop_to_entry=execution.stop_to_entry_after_t1):
            return
    # Data ended inside the session: close at the last close we have, and say so.
    seen = [c for c in held if c.start_at.astimezone(IST).date() == signal_day]
    position.sell(position.remaining, seen[-1].close)
    position.last_at = seen[-1].start_at + length("5m")


def _trade(
    candidate: SignalCandidate,
    position: _Position,
    entry_at: datetime,
    quantity: int,
    risk: Decimal,
    schedule: CostSchedule,
) -> Trade:
    """The closed trade with its gross P&L, costs and R multiples."""
    side, entry, legs = position.side, position.entry, position.legs
    d = 1 if side == "buy" else -1
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
        entry_at=entry_at,
        exit_at=position.last_at,
        entry_price=entry,
        exit_price=legs[-1].price,
        exit_reason=position.reason,
        quantity=quantity,
        gross_pnl=cents(gross),
        costs=cents(costs),
        r=r.quantize(Decimal("0.0001")),
        gross_r=gross_r.quantize(Decimal("0.0001")),
        target_1_hit=position.t1_hit,
    )
