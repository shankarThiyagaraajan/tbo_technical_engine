"""VWAP with pivot confluence (5-minute)."""

from __future__ import annotations

from tbo_technical_engine.schemas.signal import Draft
from tbo_technical_engine.schemas.strategy import Params
from tbo_technical_engine.strategies.base import Context, Side, Strategy, component, direction, finite
from tbo_technical_engine.strategies.pricing import finish, levels_beyond, pivot_levels, second_target


class VwapPivot(Strategy):
    """Long: VWAP and a pivot level are within `confluence_pct` of each other (the nearer pivot if several), and the
    candle either rejects the zone (its low reaches the zone, it closes above it and is bullish) or closes above it
    after the previous close was inside or below it. Stop: below the zone by `stop_atr` ATR. T1: the next pivot level;
    T2: `target_2_r` risks. With `require_index` the index must be above its VWAP too (the feed has no index data yet,
    so the switch ships off). Short: the mirror image."""

    name = "vwap_pivot"

    def evaluate(self, ctx: Context, p: Params, side: Side) -> Draft | None:
        if len(ctx.rows) < 2:
            return None
        d = direction(side)
        now, prev = ctx.rows[-1], ctx.rows[-2]
        candle = now.candle
        c, o, pc = float(candle.close), float(candle.open), float(prev.candle.close)
        vwap, atr = now.v["vwap"], now.v["atr_14"]
        levels = pivot_levels(now.v)
        if not finite(vwap, atr, c) or not levels:
            return None
        name, level = min(levels, key=lambda kv: abs(kv[1] - vwap))
        gap_pct = abs(level - vwap) / vwap * 100
        if gap_pct > p.number("confluence_pct"):
            return None
        lo, hi = min(vwap, level), max(vwap, level)
        near, far = (lo, hi) if d > 0 else (hi, lo)  # `far` is the edge the price must leave through
        worst = float(candle.low) if d > 0 else float(candle.high)
        rejection, broke = _entry(d, c, o, pc, worst, far)
        index_known = ctx.index_side is not None
        index_ok = (ctx.index_side == d) if index_known else not p.flag("require_index")
        if not ((rejection or broke) and index_ok):
            return None
        stop = near - d * p.number("stop_atr") * atr
        risk = d * (c - stop)
        ahead = levels_beyond(levels, c, d)
        if risk <= 0 or not ahead:
            return None
        next_name, nxt = ahead[0]
        if d * (nxt - c) < p.number("target_1_min_r") * risk:
            return None
        components = [
            component("vwap_pivot_confluence", True, gap_pct),
            component("rejection_of_zone", rejection, far),
            component("close_beyond_zone", broke, far),
        ]
        if index_known:
            components.append(component("index_same_side_of_vwap", index_ok))
        return finish(
            ctx,
            side,
            p,
            atr=atr,
            stop=stop,
            target_1=nxt,
            target_2=second_target(c, d, risk, nxt, p),
            parts=[1 - gap_pct / p.number("confluence_pct"), 0.75 if rejection else 0.5, d * (nxt - c) / risk / 2],
            components=components,
            indicators={"vwap": vwap, "atr_14": atr, name: level, next_name: nxt},
        )


def _entry(d: int, c: float, o: float, pc: float, worst: float, far: float) -> tuple[bool, bool]:
    """(rejection, broke): the candle reached the zone and closed beyond it in the trade's colour, or it closed beyond
    after the previous close was not; `worst` is its extreme against the trade, `far` the zone's edge to leave by."""
    beyond = d * (c - far) > 0
    touched = d * (worst - far) <= 0
    rejection = beyond and touched and d * (c - o) > 0
    broke = beyond and d * (pc - far) <= 0
    return rejection, broke
