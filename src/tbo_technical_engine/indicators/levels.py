"""Levels taken from the previous session (classic and Camarilla pivots, CPR) and from the opening range.

Previous-session high, low and close are taken from the bars themselves (the session's highest high, lowest low and
last close), so the same function serves every timeframe and needs no daily candles. They are valid from the first
bar of the second session in the series. A session missing from the series is simply skipped: the "previous"
session is the previous one present.

Formulas (H, L, C of the previous session; P = (H + L + C) / 3; range = H - L):

    classic   R1 = 2P - L   S1 = 2P - H   R2 = P + range   S2 = P - range   R3 = H + 2(P - L)   S3 = L - 2(H - P)
    Camarilla R1..R4 = C + range * 1.1 / (12, 6, 4, 2)    S1..S4 = C - range * 1.1 / (12, 6, 4, 2)
    CPR       BC = (H + L) / 2   TC = 2P - BC   width % = |TC - BC| / P * 100     (top and bottom are max/min of TC, BC)

The opening range is the high and low of the first `minutes` of the session. It is nan until the bar that completes
the window has closed and then fixed for the rest of the session, so a strategy can never see a range that is still
forming (the plan's look-ahead risk). A session whose first bar is not the 09:15 bar has no opening range.
"""

from __future__ import annotations

import math
from collections.abc import Callable

import numpy as np

from tbo_technical_engine.indicators.bars import F64, SESSION_OPEN_TOD, Bar, Bars
from tbo_technical_engine.indicators.base import NAN, Indicator, Stream, full

Hlc = tuple[float, float, float]


def classic_pivots(h: float, low: float, c: float) -> dict[str, float]:
    """Classic floor-trader pivots from the previous session's high, low and close."""
    p = (h + low + c) / 3.0
    rng = h - low
    return {
        "pivot_p": p,
        "pivot_r1": 2 * p - low,
        "pivot_r2": p + rng,
        "pivot_r3": h + 2 * (p - low),
        "pivot_s1": 2 * p - h,
        "pivot_s2": p - rng,
        "pivot_s3": low - 2 * (h - p),
    }


def camarilla_pivots(h: float, low: float, c: float) -> dict[str, float]:
    """Camarilla levels R1-R4 / S1-S4: the close plus or minus 1.1 x range / (12, 6, 4, 2). R3/S3 are the usual reversal
    levels, R4/S4 the breakout levels."""
    rng = h - low
    out: dict[str, float] = {}
    for i, divisor in enumerate((12.0, 6.0, 4.0, 2.0), start=1):
        out[f"cam_r{i}"] = c + rng * 1.1 / divisor
        out[f"cam_s{i}"] = c - rng * 1.1 / divisor
    return out


def cpr(h: float, low: float, c: float) -> dict[str, float]:
    """The central pivot range: pivot, bottom and top central pivots and the range's width in percent of the pivot (a
    narrow CPR hints at a trend day)."""
    p = (h + low + c) / 3.0
    bc = (h + low) / 2.0
    tc = 2 * p - bc
    return {
        "cpr_pivot": p,
        "cpr_bc": bc,
        "cpr_tc": tc,
        "cpr_top": max(tc, bc),
        "cpr_bottom": min(tc, bc),
        "cpr_width_pct": abs(tc - bc) / p * 100.0 if p else NAN,
    }


def previous_session_hlc(bars: Bars) -> tuple[F64, F64, F64]:
    """For every bar, the high, low and close of the previous session (nan on the first session)."""
    n = len(bars)
    ph, pl, pc = full(n), full(n), full(n)
    if n == 0:
        return ph, pl, pc
    starts = np.flatnonzero(np.r_[True, bars.session[1:] != bars.session[:-1]])
    ends = np.r_[starts[1:], n]
    for idx in range(1, len(starts)):
        a, b = starts[idx - 1], ends[idx - 1]
        h, low, c = float(bars.high[a:b].max()), float(bars.low[a:b].min()), float(bars.close[b - 1])
        ph[starts[idx] : ends[idx]], pl[starts[idx] : ends[idx]], pc[starts[idx] : ends[idx]] = h, low, c
    return ph, pl, pc


class _SessionLevels(Indicator):
    """Base of the previous-session level indicators: subclasses name the outputs (`names`) and the formula (`kind`);
    the batch and live forms are shared."""

    kind: Callable[[float, float, float], dict[str, float]]
    names: tuple[str, ...]

    @property
    def outputs(self) -> tuple[str, ...]:
        return self.names

    @property
    def lookback(self) -> int:
        return 0

    def batch(self, bars: Bars) -> dict[str, F64]:
        ph, pl, pc = previous_session_hlc(bars)
        out = {name: full(len(bars)) for name in self.names}
        cache: dict[tuple[float, float, float], dict[str, float]] = {}
        for i in range(len(bars)):
            if math.isnan(ph[i]):
                continue
            key = (float(ph[i]), float(pl[i]), float(pc[i]))
            if key not in cache:
                cache[key] = type(self).kind(*key)
            for name, value in cache[key].items():
                out[name][i] = value
        return out

    def stream(self) -> Stream:
        return _LevelsStream(self)


class _LevelsStream:
    """Live form: tracks the running session's high, low and close; recomputes the levels when a session starts."""

    def __init__(self, spec: _SessionLevels) -> None:
        self.spec = spec
        self.session = -1
        self.cur_h = self.cur_l = self.cur_c = NAN
        self.levels: dict[str, float] = {name: NAN for name in spec.names}

    def update(self, bar: Bar) -> dict[str, float]:
        if bar.session != self.session:
            if self.session != -1:
                self.levels = type(self.spec).kind(self.cur_h, self.cur_l, self.cur_c)
            self.session = bar.session
            self.cur_h, self.cur_l = bar.high, bar.low
        else:
            self.cur_h, self.cur_l = max(self.cur_h, bar.high), min(self.cur_l, bar.low)
        self.cur_c = bar.close
        return dict(self.levels)


class Pivots(_SessionLevels):
    """Classic pivots of the previous session."""

    kind = staticmethod(classic_pivots)
    names = ("pivot_p", "pivot_r1", "pivot_r2", "pivot_r3", "pivot_s1", "pivot_s2", "pivot_s3")


class Camarilla(_SessionLevels):
    """Camarilla pivots of the previous session."""

    kind = staticmethod(camarilla_pivots)
    names = tuple(f"cam_{side}{i}" for side in ("r", "s") for i in (1, 2, 3, 4))


class Cpr(_SessionLevels):
    """CPR of the previous session."""

    kind = staticmethod(cpr)
    names = ("cpr_pivot", "cpr_bc", "cpr_tc", "cpr_top", "cpr_bottom", "cpr_width_pct")


class OpeningRange(Indicator):
    """High, low, middle and height of the first `minutes` of the session; nan until the window has fully closed."""

    def __init__(self, minutes: int = 15, timeframe_minutes: int = 5) -> None:
        if minutes <= 0 or minutes % timeframe_minutes:
            raise ValueError("the range must be a whole number of bars")
        self.minutes = minutes
        self.tf = timeframe_minutes
        self.high, self.low, self.mid, self.height = "or_high", "or_low", "or_mid", "or_height"

    @property
    def outputs(self) -> tuple[str, ...]:
        return (self.high, self.low, self.mid, self.height)

    @property
    def lookback(self) -> int:
        return self.minutes // self.tf

    def _values(self, hi: float, lo: float) -> dict[str, float]:
        return {self.high: hi, self.low: lo, self.mid: (hi + lo) / 2.0, self.height: hi - lo}

    def batch(self, bars: Bars) -> dict[str, F64]:
        n = len(bars)
        out = {name: full(n) for name in self.outputs}
        if n == 0:
            return out
        starts = np.flatnonzero(np.r_[True, bars.session[1:] != bars.session[:-1]])
        ends = np.r_[starts[1:], n]
        end_tod = SESSION_OPEN_TOD + self.minutes
        for a, b in zip(starts, ends, strict=True):
            if int(bars.tod[a]) != SESSION_OPEN_TOD:
                continue
            tod = bars.tod[a:b]
            in_range = tod < end_tod
            done = np.flatnonzero(tod + self.tf >= end_tod)
            if not in_range.any() or done.size == 0:
                continue
            first_done = a + int(done[0])
            window = slice(a, a + int(in_range.sum()))
            hi, lo = float(bars.high[window].max()), float(bars.low[window].min())
            for name, value in self._values(hi, lo).items():
                out[name][first_done:b] = value
        return out

    def stream(self) -> Stream:
        return _OrStream(self)


class _OrStream:
    """Live form: accumulates the window's high and low, publishing them once the window's last bar has closed."""

    def __init__(self, spec: OpeningRange) -> None:
        self.spec = spec
        self.session = -1
        self.valid = False
        self.hi = self.lo = NAN
        self.done = False

    def update(self, bar: Bar) -> dict[str, float]:
        s = self.spec
        if bar.session != self.session:
            self.session = bar.session
            self.valid = bar.tod == SESSION_OPEN_TOD
            self.hi, self.lo, self.done = NAN, NAN, False
        end_tod = SESSION_OPEN_TOD + s.minutes
        if self.valid and not self.done:
            if bar.tod < end_tod:
                self.hi = bar.high if math.isnan(self.hi) else max(self.hi, bar.high)
                self.lo = bar.low if math.isnan(self.lo) else min(self.lo, bar.low)
            if bar.tod + s.tf >= end_tod:
                self.done = True
        if self.valid and self.done:
            return s._values(self.hi, self.lo)
        return {name: NAN for name in s.outputs}
