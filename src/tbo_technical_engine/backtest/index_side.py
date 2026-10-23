"""The primary index's side of its VWAP for a backtest (TE5 with `require_index`, ADR-019).

Live, `vwap_pivot` reads the side from the market context (`ContextService.index_side`): +1 when nifty_50 was above its
session VWAP, -1 when not. A backtest has no context, so the side is rebuilt from the index's 5-minute candles in the
research cache, with the feed's definition of an index VWAP (no volume: the session's mean of the 5-minute typical price
(high + low + close) / 3 up to that candle). The side at an entry candle is the one at the newest index candle that had
closed by the entry candle's start, in the same session: never a later candle. Live, the context can lag by one more
candle (the feed polls 20 s after a boundary), so a backtest side may be one candle fresher than the live one.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Sequence
from datetime import date, datetime

from tbo_platform_kit.config.calendar import IST

from tbo_technical_engine.schemas.candle import Candle
from tbo_technical_engine.services.evaluator import IndexSide
from tbo_technical_engine.utils.timeframes import length

PRIMARY_INDEX = "nifty_50"


def index_sides(candles: Sequence[Candle]) -> IndexSide | None:
    """A lookup of the index's VWAP side by entry candle start; None when there are no index candles (the backtest
    then runs as live does without index data)."""
    if not candles:
        return None
    ends: list[datetime] = []
    sides: list[tuple[date, int]] = []
    day: date | None = None
    total, count = 0.0, 0
    for c in sorted(candles, key=lambda c: c.start_at):
        local = c.start_at.astimezone(IST).date()
        if local != day:
            day, total, count = local, 0.0, 0
        total += (float(c.high) + float(c.low) + float(c.close)) / 3
        count += 1
        ends.append(c.start_at + length("5m"))
        sides.append((local, 1 if float(c.close) > total / count else -1))

    def side(candle_start: datetime) -> int | None:
        at = bisect_right(ends, candle_start) - 1
        if at < 0 or sides[at][0] != candle_start.astimezone(IST).date():
            return None
        return sides[at][1]

    return side
