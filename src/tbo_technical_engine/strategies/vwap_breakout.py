"""VWAP breakout (5-minute)."""

from __future__ import annotations

from tbo_technical_engine.schemas.signal import Draft
from tbo_technical_engine.schemas.strategy import Params
from tbo_technical_engine.strategies.base import Context, Side, Strategy, component, direction, finite
from tbo_technical_engine.strategies.pricing import finish, levels_beyond, pivot_levels, second_target


class VwapBreakout(Strategy):
    """Long: the close is above session VWAP after `bars_below` bars right before it closed below VWAP, on volume of at
    least `volume_ratio_min` times the 20-bar average. Stop: the breakout candle's low or VWAP minus `stop_atr` ATR,
    whichever is nearer. T1: the next pivot level; T2: `target_2_r` risks. Short: the mirror image.

    The plan's "prefer the first retest within 3 bars" is not a condition: it describes a different entry and is left
    to the backtest to justify (docs/PROGRESS.md)."""

    name = "vwap_breakout"

    def evaluate(self, ctx: Context, p: Params, side: Side) -> Draft | None:
        n = p.integer("bars_below")
        if len(ctx.rows) < n + 1:
            return None
        d = direction(side)
        now = ctx.rows[-1]
        c = float(now.candle.close)
        vwap, atr, ratio = now.v["vwap"], now.v["atr_14"], now.v["vol_ratio_20"]
        if not finite(vwap, atr, ratio, c):
            return None
        before = ctx.rows[-1 - n : -1]
        if before[0].candle.start_at < ctx.session[0].start_at:
            return None  # VWAP restarts each session: yesterday's last bar was measured against yesterday's VWAP (9.9)
        if not all(finite(r.v["vwap"]) for r in before):
            return None
        sustained = all(d * (float(r.candle.close) - r.v["vwap"]) < 0 for r in before)
        crossed = d * (c - vwap) > 0
        loud = ratio >= p.number("volume_ratio_min")
        if not (sustained and crossed and loud):
            return None
        extreme = float(now.candle.low) if d > 0 else float(now.candle.high)
        stop = vwap - d * p.number("stop_atr") * atr
        stop = max(extreme, stop) if d > 0 else min(extreme, stop)
        risk = d * (c - stop)
        ahead = levels_beyond(pivot_levels(now.v), c, d)
        if risk <= 0 or not ahead:
            return None
        level_name, level = ahead[0]
        if d * (level - c) < p.number("target_1_min_r") * risk:
            return None
        return finish(
            ctx,
            side,
            p,
            atr=atr,
            stop=stop,
            target_1=level,
            target_2=second_target(c, d, risk, level, p),
            parts=[ratio / 3, abs(c - vwap) / atr, d * (level - c) / risk / 2],
            components=[
                component("bars_on_other_side_of_vwap", sustained, float(n)),
                component("close_crossed_vwap", crossed, vwap),
                component("volume_ratio_at_least_min", loud, ratio),
                component("pivot_target_in_reach", True, level),
            ],
            indicators={"vwap": vwap, "atr_14": atr, "vol_ratio_20": ratio, level_name: level},
        )
