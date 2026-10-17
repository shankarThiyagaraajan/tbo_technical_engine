"""Deterministic synthetic market data for the indicator tests: a seeded random walk like 5-minute NSE sessions."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta

import numpy as np

from tbo_technical_engine.indicators.bars import Bar, Bars, session_and_tod
from tests.support import CALENDAR, session_start

BARS_PER_SESSION = 75  # 09:15 to 15:30 in five-minute bars


def sessions(first: date, count: int) -> list[date]:
    days: list[date] = []
    day = first
    while len(days) < count:
        if CALENDAR.is_trading_day(day):
            days.append(day)
        day += timedelta(days=1)
    return days


def random_walk(days: int = 250, seed: int = 7, start: date = date(2025, 10, 1), price: float = 1200.0) -> Bars:
    """`days` sessions of 75 five-minute bars, prices rounded to two decimals like the exchange's."""
    rng = np.random.default_rng(seed)
    rows: list[tuple[int, float, float, float, float, float]] = []
    last = price
    for day in sessions(start, days):
        t0: datetime = session_start(day)
        last *= 1 + rng.normal(0, 0.004)  # overnight gap
        for i in range(BARS_PER_SESSION):
            o = last
            c = o * (1 + rng.normal(0, 0.0015))
            h = max(o, c) * (1 + abs(rng.normal(0, 0.0007)))
            low = min(o, c) * (1 - abs(rng.normal(0, 0.0007)))
            v = int(rng.integers(5_000, 200_000)) * (3 if i < 6 or i > 70 else 1)
            ts = int((t0 + timedelta(minutes=5 * i)).astimezone(UTC).timestamp())
            rows.append((ts, round(o, 2), round(h, 2), round(low, 2), round(c, 2), float(v)))
            last = round(c, 2)
    bars = []
    for epoch, open_, high, low_, close, volume in rows:
        s, tod = session_and_tod(datetime.fromtimestamp(epoch, UTC))
        bars.append(Bar(epoch, open_, high, low_, close, volume, s, tod))
    return Bars.from_bars(bars)


def from_ohlcv(
    rows: Sequence[tuple[float, float, float, float, float]], per_session: int = 75, first: date = date(2026, 3, 2)
) -> Bars:
    """Bars from explicit (open, high, low, close, volume) rows, `per_session` bars to a session starting 09:15 IST."""
    days = sessions(first, max(1, -(-len(rows) // per_session)))
    bars = []
    for i, (o, h, low, c, v) in enumerate(rows):
        day = days[i // per_session]
        start = session_start(day) + timedelta(minutes=5 * (i % per_session))
        s, tod = session_and_tod(start)
        bars.append(Bar(int(start.timestamp()), o, h, low, c, v, s, tod))
    return Bars.from_bars(bars)
