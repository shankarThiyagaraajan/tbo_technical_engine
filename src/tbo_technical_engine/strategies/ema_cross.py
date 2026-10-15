"""9/21 EMA crossover (5-minute)."""

from __future__ import annotations

from tbo_technical_engine.schemas.signal import Draft
from tbo_technical_engine.schemas.strategy import Params
from tbo_technical_engine.strategies.base import Context, Side, Strategy, component, direction, finite
from tbo_technical_engine.strategies.pricing import finish


class EmaCrossover(Strategy):
    """Long: the 9 EMA crosses above the 21 EMA on this candle, the candle closes above both, above VWAP, ADX at least
    `adx_min`. Stop: the swing low of the last `swing_bars` bars or `stop_atr` ATR below the close, whichever is nearer.
    Targets: `target_1_r` and `target_2_r` times the risk. Short: the mirror image."""

    name = "ema_9_21_crossover"

    def evaluate(self, ctx: Context, p: Params, side: Side) -> Draft | None:
        bars = p.integer("swing_bars")
        if len(ctx.rows) < max(2, bars):
            return None
        d = direction(side)
        now, prev = ctx.rows[-1], ctx.rows[-2]
        c = float(now.candle.close)
        e9, e21, vwap, adx, atr = (now.v[k] for k in ("ema_9", "ema_21", "vwap", "adx_14", "atr_14"))
        p9, p21 = prev.v["ema_9"], prev.v["ema_21"]
        if not finite(e9, e21, p9, p21, vwap, adx, atr, c):
            return None
        crossed = d * (e9 - e21) > 0 and d * (p9 - p21) <= 0
        beyond_both = d * (c - e9) > 0 and d * (c - e21) > 0
        with_vwap = d * (c - vwap) > 0
        trending = adx >= p.number("adx_min")
        if not (crossed and beyond_both and with_vwap and trending):
            return None
        recent = ctx.rows[-bars:]
        swing = min(float(r.candle.low) for r in recent) if d > 0 else max(float(r.candle.high) for r in recent)
        risk = min(d * (c - swing), p.number("stop_atr") * atr)
        if risk <= 0:
            return None
        stop = c - d * risk
        return finish(
            ctx,
            side,
            p,
            atr=atr,
            stop=stop,
            target_1=c + d * p.number("target_1_r") * risk,
            target_2=c + d * p.number("target_2_r") * risk,
            parts=[adx / 40, abs(c - vwap) / atr, abs(e9 - e21) / atr],
            components=[
                component("ema_9_crossed_21", crossed, e9 - e21),
                component("close_beyond_both_emas", beyond_both, c),
                component("close_with_vwap", with_vwap, vwap),
                component("adx_at_least_min", trending, adx),
            ],
            indicators={"ema_9": e9, "ema_21": e21, "vwap": vwap, "adx_14": adx, "atr_14": atr},
        )
