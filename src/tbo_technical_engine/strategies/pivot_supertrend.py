"""Pivot with Supertrend (15-minute trend, 5-minute entry)."""

from __future__ import annotations

from tbo_technical_engine.schemas.signal import Draft
from tbo_technical_engine.schemas.strategy import Params
from tbo_technical_engine.strategies.base import Context, Side, Strategy, component, direction, finite
from tbo_technical_engine.strategies.pricing import finish, levels_beyond, pivot_levels


class PivotSupertrend(Strategy):
    """Long: the 15-minute Supertrend(10, 3) is up and this 5-minute close crosses above the pivot P or R1 (the previous
    close was at or below it). Stop: the 5-minute Supertrend line, which must be below the close. T1 and T2: the next
    two pivot levels. No entry closer than `min_room_pct` to the first of them. Short: the mirror image (P or S1)."""

    name = "pivot_supertrend"

    def evaluate(self, ctx: Context, p: Params, side: Side) -> Draft | None:
        if len(ctx.rows) < 2 or ctx.trend15 is None:
            return None
        d = direction(side)
        now, prev = ctx.rows[-1], ctx.rows[-2]
        c, pc = float(now.candle.close), float(prev.candle.close)
        v = now.v
        dir15, st5, dir5, atr = (
            ctx.trend15["supertrend_dir_10_3"],
            v["supertrend_10_3"],
            v["supertrend_dir_10_3"],
            v["atr_14"],
        )
        levels = pivot_levels(v)
        if not finite(dir15, st5, dir5, atr, c, pc) or not levels:
            return None
        trend_up = d * dir15 > 0
        edge = "pivot_r1" if d > 0 else "pivot_s1"
        triggers = [x for k, x in levels if k in ("pivot_p", edge) and d * (c - x) > 0 and d * (pc - x) <= 0]
        stop_ok = d * dir5 > 0 and d * (c - st5) > 0
        ahead = levels_beyond(levels, c, d)
        if not (trend_up and triggers and stop_ok) or len(ahead) < 2:
            return None
        (n1, t1), (n2, t2) = ahead[0], ahead[1]
        room = d * (t1 - c) / c * 100
        if room < p.number("min_room_pct"):
            return None
        risk = d * (c - st5)
        return finish(
            ctx,
            side,
            p,
            atr=atr,
            stop=st5,
            target_1=t1,
            target_2=t2,
            parts=[
                0.5 + 0.5 * min(1.0, abs(c - triggers[0]) / atr),
                min(1.0, d * (t1 - c) / risk / 2),
                min(1.0, room / 1.0),
            ],
            components=[
                component("supertrend_15m_with_trade", trend_up, dir15),
                component("close_crossed_pivot", True, triggers[0]),
                component("supertrend_5m_stop_valid", stop_ok, st5),
                component("room_to_next_pivot", True, room),
            ],
            indicators={"supertrend_10_3": st5, "atr_14": atr, n1: t1, n2: t2},
        )
