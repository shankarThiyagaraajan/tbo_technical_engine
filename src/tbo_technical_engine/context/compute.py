"""The market context as pure functions: stock breadth, one index's state, the VIX change, the CPR class.

No I/O and no clock: the service gathers the inputs, these turn them into numbers, so each can be checked by hand.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from datetime import date
from decimal import Decimal
from typing import Literal

from tbo_platform_kit.calendar import IST

from tbo_technical_engine import indicators
from tbo_technical_engine.context.index_source import IndexSeries, VixReading
from tbo_technical_engine.indicators.bars import Bars
from tbo_technical_engine.indicators.format import decimal_text
from tbo_technical_engine.indicators.suite import Suite
from tbo_technical_engine.schemas.candle import Candle
from tbo_technical_engine.schemas.context import Breadth, IndexState, Vix
from tbo_technical_engine.services.candle_buffer import CandleBuffer

CENT = Decimal("0.01")
NARROW_PCT = 0.25
WIDE_PCT = 0.5
"""Starting CPR width limits, in percent of the pivot: narrower than the first is a likely trend day, wider than the
second a likely range day. Settings override them (`cpr_narrow_pct`, `cpr_wide_pct`); the backtest should tune them."""
SIDEWAYS_PCT = 0.05
"""EMA(9) and EMA(21) of the 15-minute candles closer than this many percent of the price are not a trend."""


def _local_day(c: Candle) -> date:
    return c.start_at.astimezone(IST).date()


def breadth(buffer: CandleBuffer, isins: Iterable[str], day: date) -> Breadth:
    """Stocks up, down and flat against the previous session's last close, by the latest 5-minute close of `day`.
    A stock without a candle today or without a previous session is left out, not counted as unchanged."""
    up = down = flat = 0
    for isin in isins:
        series = buffer.series(isin, "5m")
        today = [c for c in series if _local_day(c) == day]
        before = [c for c in series if _local_day(c) < day]
        if not today or not before:
            continue
        last, previous = today[-1].close, before[-1].close
        if last > previous:
            up += 1
        elif last < previous:
            down += 1
        else:
            flat += 1
    return Breadth(advancing=up, declining=down, unchanged=flat)


def price_text(value: float) -> str:
    return format(Decimal(repr(round(value, 2))).quantize(CENT), "f")


def index_state(series: IndexSeries, day: date) -> IndexState | None:
    """The state at the newest 5-minute candle of `day`; None when the index has none yet."""
    today = [c for c in series.candles_5m if _local_day(c) == day]
    if not today:
        return None
    last = float(today[-1].close)
    _, values = Suite([indicators.get("vwap")]).replay(Bars.from_candles(today))
    vwap = values["vwap"]
    has_vwap = math.isfinite(vwap)
    return IndexState(
        index_code=series.code,
        last_price=price_text(last),
        vwap=price_text(vwap) if has_vwap else None,
        is_above_vwap=bool(has_vwap and last > vwap),
        trend_15m=trend_15m(series.candles_15m),
    )


def trend_15m(candles: list[Candle]) -> Literal["up", "down", "sideways"]:
    """Up when the 15-minute Supertrend(10, 3) is up and EMA 9 is above EMA 21 by more than the sideways band; down
    when both say down; otherwise sideways (including while the indicators are still warming up)."""
    if not candles:
        return "sideways"
    suite = Suite(
        [
            indicators.get("ema", period=9),
            indicators.get("ema", period=21),
            indicators.get("supertrend", period=10, multiplier=3.0),
        ]
    )
    _, v = suite.replay(Bars.from_candles(candles))
    e9, e21, direction = v["ema_9"], v["ema_21"], v["supertrend_dir_10_3"]
    if not all(math.isfinite(x) for x in (e9, e21, direction)):
        return "sideways"
    gap = (e9 - e21) / e21 * 100
    if direction > 0 and gap > SIDEWAYS_PCT:
        return "up"
    if direction < 0 and gap < -SIDEWAYS_PCT:
        return "down"
    return "sideways"


def vix_state(reading: VixReading | None) -> Vix | None:
    if reading is None or not all(math.isfinite(x) for x in (reading.level, reading.previous_close)):
        return None
    if reading.previous_close <= 0:
        return None
    change = (reading.level - reading.previous_close) / reading.previous_close * 100
    return Vix(level=decimal_text(reading.level, 2) or "0", change_pct=decimal_text(change, 2) or "0")


def cpr_width_pct(candle: Candle) -> float:
    """|TC - BC| / P * 100 for the CPR of one session's high, low and close."""
    h, low, c = float(candle.high), float(candle.low), float(candle.close)
    pivot = (h + low + c) / 3
    bc = (h + low) / 2
    tc = 2 * pivot - bc
    return abs(tc - bc) / pivot * 100 if pivot else math.nan


def cpr_class(
    candle: Candle | None, narrow: float = NARROW_PCT, wide: float = WIDE_PCT
) -> Literal["narrow", "normal", "wide"] | None:
    """The day class from the previous session's CPR width; None without that session."""
    if candle is None:
        return None
    width = cpr_width_pct(candle)
    if not math.isfinite(width):
        return None
    if width < narrow:
        return "narrow"
    if width > wide:
        return "wide"
    return "normal"
