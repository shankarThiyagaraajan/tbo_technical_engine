"""Entry zone, stop and targets on the tick grid, and the checks every candidate must pass.

Rounding goes the safe way for the trader: a buy's entry zone widens, its stop moves further away and its targets
nearer (a sell is the mirror image), so rounding never makes a trade look better than it is.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from tbo_technical_engine.schemas.signal import Component, Draft
from tbo_technical_engine.schemas.strategy import Params
from tbo_technical_engine.strategies.base import Context, Side, direction

TICK = Decimal("0.05")


def to_tick(value: float, *, up: bool) -> Decimal:
    rounding = ROUND_CEILING if up else ROUND_FLOOR
    steps = (Decimal(repr(round(value, 6))) / TICK).to_integral_value(rounding=rounding)
    return (steps * TICK).quantize(Decimal("0.01"))


def strength_of(parts: Sequence[float]) -> Decimal:
    """The mean of scores in [0, 1], four places."""
    clipped = [min(1.0, max(0.0, x)) if math.isfinite(x) else 0.0 for x in parts]
    return Decimal(repr(round(sum(clipped) / len(clipped), 4))).quantize(Decimal("0.0001"))


def finish(
    ctx: Context,
    side: Side,
    p: Params,
    *,
    atr: float,
    stop: float,
    target_1: float,
    target_2: float,
    parts: Sequence[float],
    components: Sequence[Component],
    indicators: Mapping[str, float],
) -> Draft | None:
    d = direction(side)
    close = float(ctx.now.candle.close)
    zone = p.number("entry_zone_atr") * atr
    low, high = (close, close + zone) if d > 0 else (close - zone, close)
    entry_low, entry_high = to_tick(low, up=False), to_tick(high, up=True)
    stop_p = to_tick(stop, up=d < 0)
    t1, t2 = to_tick(target_1, up=d < 0), to_tick(target_2, up=d < 0)
    if d > 0:
        valid = stop_p > 0 and stop_p < entry_low and entry_high < t1 < t2
    else:
        valid = 0 < t2 < t1 < entry_low and entry_high < stop_p
    if not valid:
        return None
    return Draft(
        side=side,
        entry_low=entry_low,
        entry_high=entry_high,
        stop=stop_p,
        target_1=t1,
        target_2=t2,
        strength=strength_of(parts),
        components=tuple(components),
        indicators=dict(indicators),
    )


PIVOT_KEYS = ("pivot_s3", "pivot_s2", "pivot_s1", "pivot_p", "pivot_r1", "pivot_r2", "pivot_r3")


def pivot_levels(values: Mapping[str, float]) -> list[tuple[str, float]]:
    """The classic pivot levels that exist, lowest first."""
    found = [(k, values.get(k, math.nan)) for k in PIVOT_KEYS]
    return sorted(((k, x) for k, x in found if math.isfinite(x)), key=lambda kv: kv[1])


def levels_beyond(levels: Sequence[tuple[str, float]], price: float, d: int) -> list[tuple[str, float]]:
    """Levels strictly beyond `price` in direction `d`, nearest first."""
    beyond = [(k, x) for k, x in levels if d * (x - price) > 0]
    return sorted(beyond, key=lambda kv: d * kv[1])


def second_target(close: float, d: int, risk: float, level: float, p: Params) -> float:
    """T2 for the strategies that aim at a pivot first: `target_2_r` risks away, and never closer to T1 than
    `target_2_min_gap_r` risks."""
    reach = max(p.number("target_2_r") * risk, d * (level - close) + p.number("target_2_min_gap_r") * risk)
    return close + d * reach
