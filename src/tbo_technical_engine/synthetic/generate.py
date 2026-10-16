"""One stock's sessions as one-minute arrays, then the 5-minute, 15-minute and daily candles aggregated from them.

The aggregation is the market feed's own rule (a 5-minute candle is the open of its first minute, the extremes of all
five, the close of the last, the volumes added), so a candle here is what the feed would have published for the same
minutes. Every random draw comes from `numpy.random.default_rng([seed, stock_index])`, so a stock's path does not
depend on how many other stocks are generated.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import numpy as np
from numpy.typing import NDArray
from tbo_platform_kit.calendar import IST

from tbo_technical_engine.config import synthetic as cfg
from tbo_technical_engine.schemas.candle import Candle, Timeframe
from tbo_technical_engine.schemas.synthetic import SyntheticSpec, isin_of

F = NDArray[np.float64]


@dataclass(frozen=True)
class Minutes:
    """One session of one stock, one value per minute."""

    day: date
    open: F
    high: F
    low: F
    close: F
    volume: NDArray[np.int64]


def weekdays_ending(end: date, count: int) -> list[date]:
    """The `count` weekdays up to and including `end`, oldest first (a weekday calendar: no holidays)."""
    days: list[date] = []
    day = end
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    return days[::-1]


def session_open_utc(day: date) -> datetime:
    """09:15 IST of `day` as an aware UTC datetime."""
    return datetime.combine(day, time(9, 15), tzinfo=IST).astimezone(UTC)


def _round_tick(prices: F) -> F:
    return np.asarray(np.round(prices / cfg.PRICE_TICK) * cfg.PRICE_TICK, dtype=np.float64)


def _volume_shape() -> F:
    """Busy at the open and the close, quiet at lunch: a U over the session, mean 1."""
    x = np.linspace(-1.0, 1.0, cfg.SESSION_MINUTES)
    shape = 0.55 + 1.6 * x**2
    return np.asarray(shape / shape.mean(), dtype=np.float64)


def stock_sessions(spec: SyntheticSpec, index: int) -> list[Minutes]:
    """All sessions of stock `index`, oldest first."""
    rng = np.random.default_rng([spec.seed, index])
    shape = _volume_shape()
    scale = float(rng.lognormal(0.0, 0.6))
    price = spec.start_price * float(rng.lognormal(0.0, 0.5))
    out: list[Minutes] = []
    for day in weekdays_ending(spec.end, spec.sessions):
        gap_sigma = cfg.GAP_SIGMA * (3.0 if rng.random() < cfg.BIG_GAP_CHANCE else 1.0)
        open_price = max(price * (1 + rng.normal(0.0, gap_sigma)), 2.0)
        drift = 0.0
        if rng.random() < cfg.TREND_DAY_CHANCE:
            drift = cfg.TREND_DRIFT * (1 if rng.random() < 0.5 else -1)
        burst = rng.random(cfg.SESSION_MINUTES) < cfg.BURST_CHANCE
        sigma = cfg.MINUTE_SIGMA * np.where(burst, cfg.BURST_SIGMA, 1.0)
        # A burst keeps the direction of the day's drift half of the time: that is what makes a breakout hold.
        sign = np.where(rng.random(cfg.SESSION_MINUTES) < 0.5, 1.0, -1.0)
        moves = rng.normal(0.0, 1.0, cfg.SESSION_MINUTES) * sigma + drift + burst * sign * sigma * 0.5
        close = _round_tick(np.maximum(open_price * np.exp(np.cumsum(moves)), cfg.PRICE_TICK))
        opens = np.concatenate(([_round_tick(np.array([open_price]))[0]], close[:-1]))
        wiggle = np.abs(rng.normal(0.0, cfg.MINUTE_SIGMA * 0.6, cfg.SESSION_MINUTES)) * close
        high = _round_tick(np.maximum(opens, close) + wiggle)
        low = np.maximum(_round_tick(np.minimum(opens, close) - wiggle), cfg.PRICE_TICK)
        noise = rng.lognormal(0.0, 0.5, cfg.SESSION_MINUTES)
        volume = np.maximum(
            (cfg.BASE_VOLUME * scale * shape * noise * np.where(burst, cfg.BURST_VOLUME, 1.0)).astype(np.int64), 1
        )
        out.append(Minutes(day, opens, high, low, close, volume))
        price = float(close[-1])
    return out


def _dec(value: float) -> Decimal:
    return Decimal(f"{value:.2f}")


def _aggregate(isin: str, timeframe: Timeframe, session: Minutes, width: int) -> list[Candle]:
    """Candles of `width` minutes from the minute arrays (the session length is a multiple of 5 and of 15)."""
    n = cfg.SESSION_MINUTES // width
    o = session.open.reshape(n, width)
    h = session.high.reshape(n, width)
    lo = session.low.reshape(n, width)
    c = session.close.reshape(n, width)
    v = session.volume.reshape(n, width)
    start = session_open_utc(session.day)
    return [
        Candle(
            isin=isin,
            timeframe=timeframe,
            start_at=start + timedelta(minutes=width * i),
            open=_dec(float(o[i, 0])),
            high=_dec(float(h[i].max())),
            low=_dec(float(lo[i].min())),
            close=_dec(float(c[i, -1])),
            volume=int(v[i].sum()),
        )
        for i in range(n)
    ]


def minute_candles(isin: str, session: Minutes) -> list[Candle]:
    """The session's one-minute candles."""
    return _aggregate(isin, "1m", session, 1)


def five_minute(isin: str, session: Minutes) -> list[Candle]:
    """The session's 75 five-minute candles."""
    return _aggregate(isin, "5m", session, 5)


def fifteen_minute(isin: str, session: Minutes) -> list[Candle]:
    """The session's 25 fifteen-minute candles."""
    return _aggregate(isin, "15m", session, 15)


def daily(isin: str, session: Minutes) -> Candle:
    """The session as one daily candle, starting at midnight IST (as the feed's daily candles do)."""
    return Candle(
        isin=isin,
        timeframe="1d",
        start_at=datetime.combine(session.day, time(0, 0), tzinfo=IST).astimezone(UTC),
        open=_dec(float(session.open[0])),
        high=_dec(float(session.high.max())),
        low=_dec(float(session.low.min())),
        close=_dec(float(session.close[-1])),
        volume=int(session.volume.sum()),
    )


@dataclass(frozen=True)
class StockCandles:
    """Everything a replay or the live path reads for one stock."""

    isin: str
    five: list[Candle]
    fifteen: list[Candle]
    daily: list[Candle]


def stock_candles(spec: SyntheticSpec, index: int) -> StockCandles:
    """A stock's 5-minute, 15-minute and daily candles for every session of `spec`."""
    isin = isin_of(index)
    sessions = stock_sessions(spec, index)
    five: list[Candle] = []
    fifteen: list[Candle] = []
    days: list[Candle] = []
    for s in sessions:
        five += five_minute(isin, s)
        fifteen += fifteen_minute(isin, s)
        days.append(daily(isin, s))
    return StockCandles(isin, five, fifteen, days)
