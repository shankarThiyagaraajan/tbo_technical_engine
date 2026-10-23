"""Hand-built daily series for the swing tests. Sessions are weekdays ending on DAY; prices are shaped by hand so each
rule can be checked against numbers worked out on paper."""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

import numpy as np

from tbo_technical_engine.indicators.bars import Bars
from tbo_technical_engine.schemas.candle import Candle
from tbo_technical_engine.schemas.swing import SwingParams
from tbo_technical_engine.services.swing_loader import load_swing

DAY = date(2026, 10, 15)  # a Thursday
ROOT = Path(__file__).resolve().parents[2]
PARAMS: SwingParams = load_swing(ROOT / "config" / "swing.yaml")


def weekdays_ending(end: date, n: int) -> list[date]:
    """`n` weekday dates, oldest first, the last being `end` (which must itself be a weekday)."""
    out: list[date] = []
    day = end
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day)
        day -= timedelta(days=1)
    return out[::-1]


def bars_from(
    close: Sequence[float],
    *,
    high: Sequence[float] | None = None,
    low: Sequence[float] | None = None,
    open_: Sequence[float] | None = None,
    volume: Sequence[float] | None = None,
    end: date = DAY,
    spread: float = 1.0,
) -> Bars:
    """Bars from closes; high/low default to close +/- `spread`, open to the previous close, volume to 100,000."""
    n = len(close)
    c = np.array(close, dtype=np.float64)
    days = weekdays_ending(end, n)
    return Bars(
        ts=np.array([int(datetime.combine(d, time(3, 45), tzinfo=UTC).timestamp()) for d in days], dtype=np.int64),
        open=np.array(open_ if open_ is not None else np.r_[c[0], c[:-1]], dtype=np.float64),
        high=np.array(high if high is not None else c + spread, dtype=np.float64),
        low=np.array(low if low is not None else c - spread, dtype=np.float64),
        close=c,
        volume=np.array(volume if volume is not None else [100_000.0] * n, dtype=np.float64),
        session=np.array([d.toordinal() for d in days], dtype=np.int64),
        tod=np.full(n, 9 * 60 + 15, dtype=np.int64),
    )


def wave(n: int, start: float, step: float, amplitude: float = 0.6, period: float = 7.0) -> list[float]:
    """A drift with a deterministic wobble, so there are swing highs and lows to find."""
    return [start + step * i + amplitude * math.sin(2 * math.pi * i / period) for i in range(n)]


def candles_from(isin: str, bars: Bars, *, volume_scale: float = 1.0) -> list[Candle]:
    """Daily candles (start at IST midnight, as the feed's daily candles do) from bars."""
    from tbo_platform_kit.config.calendar import IST

    out = []
    for i in range(len(bars)):
        day = date.fromordinal(int(bars.session[i]))
        out.append(
            Candle(
                isin=isin,
                timeframe="1d",
                start_at=datetime.combine(day, time(0, 0), tzinfo=IST).astimezone(UTC),
                open=Decimal(f"{bars.open[i]:.2f}"),
                high=Decimal(f"{bars.high[i]:.2f}"),
                low=Decimal(f"{bars.low[i]:.2f}"),
                close=Decimal(f"{bars.close[i]:.2f}"),
                volume=int(bars.volume[i] * volume_scale),
            )
        )
    return out


def mean_reverting(n: int, centre: float = 100.0, seed: int = 3, sigma: float = 1.0, pull: float = 0.9) -> list[float]:
    """A seeded series that wanders around `centre` without trending (AR(1) around the centre)."""
    rng = np.random.default_rng(seed)
    out, x = [], 0.0
    for _ in range(n):
        x = pull * x + rng.normal(0.0, sigma)
        out.append(centre + x)
    return out


def base_breakout_series(
    *,
    breakout_close: float = 105.0,
    breakout_volume: float = 300_000.0,
    base_amplitude: float = 1.0,
    base_bars: int = 20,
) -> dict[str, list[float]]:
    """A climb from 60 to about 100, a base of `base_bars` sessions around 100, then one breakout session.

    Returned as plain lists so a test can change one value and rebuild the bars.
    """
    climb = wave(160, 60.0, 0.25, amplitude=0.4)
    base = [100.0 + base_amplitude * math.sin(2 * math.pi * i / 6.0) for i in range(base_bars)]
    close = [*climb, *base, breakout_close]
    volume = [100_000.0] * (len(close) - 1) + [breakout_volume]
    high = [c + 1.0 for c in close]
    low = [c - 1.0 for c in close]
    high[-1], low[-1] = breakout_close + 1.0, breakout_close - 2.0
    return {"close": close, "high": high, "low": low, "volume": volume}


def as_bars(series: dict[str, list[float]], **extra: Sequence[float]) -> Bars:
    return bars_from(series["close"], high=series["high"], low=series["low"], volume=series["volume"], **extra)  # type: ignore[arg-type]


def pullback_series(
    *, low_above_ema_pct: float = 0.2, bullish: bool = True, dip: float = 12.0
) -> dict[str, list[float]]:
    """A steady climb, then `dip` points off the high over eight sessions, then a day that touches the 20 EMA and closes
    up. Built so the 20 EMA of the last session is known: the series is generated, the EMA read back, and the last
    bar placed relative to it.
    """
    from tbo_technical_engine import indicators

    climb = [100.0 + 0.8 * i for i in range(280)]
    peak = climb[-1]
    fall = [peak - dip * (k + 1) / 8 for k in range(8)]
    close = [*climb, *fall]
    bars = bars_from([*close, close[-1]])
    ema = float(indicators.get("ema", period=20).batch(bars)["ema_20"][-1])
    last_close = ema + 2.0
    low = ema * (1 + low_above_ema_pct / 100)
    last_open = low + 0.3 if bullish else last_close + 0.5
    high = [c + 1.0 for c in close] + [last_close + 0.3]
    lows = [c - 1.0 for c in close] + [low]
    opens = [close[0], *close[:-1], last_open]
    return {"close": [*close, last_close], "high": high, "low": lows, "open": opens, "volume": [100_000.0] * len(high)}


def week_52_series(
    *, volume: float = 200_000.0, close_above: float = 1.5, last_high_gap: float = 0.5
) -> dict[str, list[float]]:
    """A year of climb with wobble, then a session closing `close_above` over the highest high of the 250 before it."""
    close = wave(279, 100.0, 0.3, amplitude=0.8)
    prior_high = max(c + 1.0 for c in close[-250:])
    last = prior_high + close_above
    close = [*close, last]
    high = [c + 1.0 for c in close]
    low = [c - 1.0 for c in close]
    high[-1], low[-1] = last + last_high_gap, last - 1.0
    return {"close": close, "high": high, "low": low, "volume": [100_000.0] * 279 + [volume]}
