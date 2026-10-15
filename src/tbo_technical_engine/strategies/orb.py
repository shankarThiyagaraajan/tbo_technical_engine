"""15-minute opening range breakout (entry on 5-minute candles)."""

from __future__ import annotations

from tbo_technical_engine.schemas.signal import Draft
from tbo_technical_engine.schemas.strategy import Params
from tbo_technical_engine.strategies.base import Context, Side, Strategy, component, direction, finite
from tbo_technical_engine.strategies.pricing import finish


class OpeningRangeBreakout(Strategy):
    """Long: a 5-minute close above the 09:15-09:30 high on volume of at least `volume_ratio_min` times average.
    Filters: the range is at most `max_range_daily_atr` daily ATR; a gap beyond `gap_skip_pct` is skipped unless the
    15-minute Supertrend agrees with the trade. Stop: the range midpoint, or the far edge of a narrow range.
    Targets: `target_1_range` and `target_2_range` range heights from the entry. Short: the mirror image.

    A filter that cannot be checked (no daily ATR, no previous close) blocks the signal: the strategy does not guess."""

    name = "orb_15m"

    def evaluate(self, ctx: Context, p: Params, side: Side) -> Draft | None:
        d = direction(side)
        now = ctx.rows[-1]
        c = float(now.candle.close)
        v = now.v
        high, low, mid, height = v["or_high"], v["or_low"], v["or_mid"], v["or_height"]
        ratio, atr = v["vol_ratio_20"], v["atr_14"]
        daily_atr = ctx.daily["atr_14"] if ctx.daily is not None else float("nan")
        if not finite(high, low, mid, height, ratio, atr, daily_atr, c) or ctx.prev_close is None or not ctx.session:
            return None
        if height <= 0:
            return None
        broke = c > high if d > 0 else c < low
        loud = ratio >= p.number("volume_ratio_min")
        compact = height <= p.number("max_range_daily_atr") * daily_atr
        gap_pct = (float(ctx.session[0].open) - ctx.prev_close) / ctx.prev_close * 100
        trend = ctx.trend15["supertrend_dir_10_3"] if ctx.trend15 is not None else float("nan")
        gap_ok = abs(gap_pct) <= p.number("gap_skip_pct") or (finite(trend) and d * trend > 0)
        if not (broke and loud and compact and gap_ok):
            return None
        narrow = height / mid * 100 < p.number("narrow_range_pct")
        stop = (low if d > 0 else high) if narrow else mid
        return finish(
            ctx,
            side,
            p,
            atr=atr,
            stop=stop,
            target_1=c + d * p.number("target_1_range") * height,
            target_2=c + d * p.number("target_2_range") * height,
            parts=[ratio / 3, 1 - height / daily_atr if daily_atr else 0.0, abs(c - (high if d > 0 else low)) / height],
            components=[
                component("close_beyond_opening_range", broke, high if d > 0 else low),
                component("volume_ratio_at_least_min", loud, ratio),
                component("range_within_daily_atr", compact, height),
                component("gap_acceptable", gap_ok, gap_pct),
            ],
            indicators={"or_high": high, "or_low": low, "or_mid": mid, "or_height": height, "vol_ratio_20": ratio},
        )
