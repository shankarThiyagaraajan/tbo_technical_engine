"""Hand-built contexts for the strategy rules. Prices mirror around 200, so a short test is the long one reflected."""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date, timedelta
from decimal import Decimal

from tbo_technical_engine.schemas.candle import Candle
from tbo_technical_engine.schemas.signal import Draft
from tbo_technical_engine.schemas.strategy import Params
from tbo_technical_engine.services.indicator_engine import default_suites
from tbo_technical_engine.strategies.base import Context, Row
from tests.support import ISIN_A, session_start

DAY = date(2026, 10, 15)
FIVE = default_suites()["5m"].outputs
BLANK = dict.fromkeys(FIVE, math.nan)
PIVOT_MIRROR = {
    "pivot_s3": "pivot_r3",
    "pivot_s2": "pivot_r2",
    "pivot_s1": "pivot_r1",
    "pivot_p": "pivot_p",
    "pivot_r1": "pivot_s1",
    "pivot_r2": "pivot_s2",
    "pivot_r3": "pivot_s3",
    "or_high": "or_low",
    "or_low": "or_high",
}
PRICE_KEYS = {"ema_9", "ema_21", "vwap", "supertrend_10_3", "or_mid"}
SIGN_KEYS = {"supertrend_dir_10_3"}
MIDDLE = 200.0

PARAMS: dict[str, str | int | bool] = {
    "entry_zone_atr": "0.1",
    "adx_min": "20",
    "swing_bars": 5,
    "stop_atr": "1",
    "target_1_r": "1.5",
    "target_2_r": "2.5",
}


def params(**over: str | int | bool) -> Params:
    return Params({**PARAMS, **over})


def candle(i: int, o: float, h: float, low: float, c: float, volume: int = 1000, day: date = DAY) -> Candle:
    return Candle(
        isin=ISIN_A,
        timeframe="5m",
        start_at=session_start(day) + timedelta(minutes=5 * i),
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(low)),
        close=Decimal(str(c)),
        volume=volume,
    )


def row(i: int, o: float, h: float, low: float, c: float, **values: float) -> Row:
    return Row(candle(i, o, h, low, c), {**BLANK, **values})


def context(
    rows: list[Row],
    *,
    trend15: Mapping[str, float] | None = None,
    daily: Mapping[str, float] | None = None,
    prev_close: float | None = None,
    index_side: int | None = None,
) -> Context:
    return Context(
        isin=ISIN_A,
        rows=rows,
        trend15=trend15,
        daily=daily,
        session=[r.candle for r in rows],
        prev_close=prev_close,
        index_side=index_side,
    )


def mirror(ctx: Context) -> Context:
    """The same situation seen in a falling market: every price p becomes 200 - p, highs and lows swap."""

    def m(x: float) -> float:
        return MIDDLE - x

    rows = []
    for r in ctx.rows:
        c = r.candle
        flipped = Candle(
            isin=c.isin,
            timeframe=c.timeframe,
            start_at=c.start_at,
            open=Decimal(str(m(float(c.open)))),
            high=Decimal(str(m(float(c.low)))),
            low=Decimal(str(m(float(c.high)))),
            close=Decimal(str(m(float(c.close)))),
            volume=c.volume,
        )
        rows.append(Row(flipped, mirror_values(r.v)))
    trend = None if ctx.trend15 is None else {k: -v if k in SIGN_KEYS else v for k, v in ctx.trend15.items()}
    return Context(
        isin=ctx.isin,
        rows=rows,
        trend15=trend,
        daily=ctx.daily,
        session=[r.candle for r in rows],
        prev_close=None if ctx.prev_close is None else m(ctx.prev_close),
        index_side=None if ctx.index_side is None else -ctx.index_side,
    )


def mirror_values(values: Mapping[str, float]) -> dict[str, float]:
    out = dict(values)
    for k in values:
        if k in PIVOT_MIRROR:
            out.pop(k, None)
    for k, v in values.items():
        if k in PIVOT_MIRROR:
            out[PIVOT_MIRROR[k]] = MIDDLE - v
        elif k in PRICE_KEYS:
            out[k] = MIDDLE - v
        elif k in SIGN_KEYS:
            out[k] = -v
    return out


def reflect(draft: Draft) -> dict[str, Decimal]:
    """A draft's prices reflected around 200, for comparing a short with its long twin."""
    mid = Decimal(200)
    return {
        "entry_low": mid - draft.entry_high,
        "entry_high": mid - draft.entry_low,
        "stop": mid - draft.stop,
        "target_1": mid - draft.target_1,
        "target_2": mid - draft.target_2,
    }


def prices(draft: Draft) -> dict[str, Decimal]:
    return {
        "entry_low": draft.entry_low,
        "entry_high": draft.entry_high,
        "stop": draft.stop,
        "target_1": draft.target_1,
        "target_2": draft.target_2,
    }


def money(**kw: str) -> dict[str, Decimal]:
    return {k: Decimal(v) for k, v in kw.items()}
