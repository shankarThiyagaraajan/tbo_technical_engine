"""The review sample for TE6's exit criterion: 30 swing setups (ten of each type) found on fixture data.

There is no recorded daily history yet, so the sample is made of shaped fixtures, with the shape varied across ten
cases per type (base tightness, breakout size, volume, pullback depth, how close the low came to the EMA). Each case
goes through the real detector; `check` then re-derives the rule's numbers with plain Python loops (no NumPy, no
indicator code) and lists anything that disagrees with what the detector said. `scripts/swing_review_sheet.py` writes
the cases as a table for a person to look at; `tests/swing/test_review_sample.py` fails if any case does not check out.
"""

from __future__ import annotations

from dataclasses import dataclass

from tbo_technical_engine import indicators
from tbo_technical_engine.indicators.bars import Bars
from tbo_technical_engine.swing import analysis, setups
from tests.swing.data import (
    PARAMS,
    as_bars,
    bars_from,
    base_breakout_series,
    pullback_series,
    week_52_series,
)

SP = PARAMS.setups
TICK = 0.05


@dataclass(frozen=True)
class Case:
    number: int
    label: str
    bars: Bars
    expected: str
    atr: float
    draft: setups.Draft | None


def _case(number: int, label: str, bars: Bars, expected: str) -> Case:
    measured = analysis.measure("INE000000001", bars, PARAMS)
    draft = None if measured is None else analysis.find_setup(measured, bars, PARAMS)
    return Case(number, label, bars, expected, 0.0 if measured is None else measured.atr, draft)


def build_sample() -> list[Case]:
    cases: list[Case] = []
    for k in range(10):
        amplitude = 0.6 + 0.25 * k  # a base that gets looser
        close = 102.0 + 0.4 * k  # a breakout that gets bigger
        volume = 160_000.0 + 30_000.0 * k
        series = base_breakout_series(
            base_amplitude=amplitude, breakout_close=close + amplitude, breakout_volume=volume
        )
        cases.append(
            _case(
                len(cases) + 1, f"base amp {amplitude:.2f}, vol x{volume / 1e5:.1f}", as_bars(series), "base_breakout"
            )
        )
    for k in range(10):
        dip, gap = 11.0 + 1.5 * k, 0.04 * k  # a deeper pullback each time, the low a little further above the EMA
        s = pullback_series(dip=dip, low_above_ema_pct=gap)
        bars = bars_from(s["close"], high=s["high"], low=s["low"], open_=s["open"], volume=s["volume"])
        cases.append(_case(len(cases) + 1, f"dip {dip:.1f}, low {gap:.2f}% over EMA", bars, "ema_pullback"))
    for k in range(10):
        above, volume = 0.4 + 0.35 * k, 125_000.0 + 25_000.0 * k
        cases.append(
            _case(
                len(cases) + 1,
                f"close {above:.2f} over the old high, vol x{volume / 1e5:.2f}",
                as_bars(week_52_series(close_above=above, volume=volume)),
                "week_52_high_breakout",
            )
        )
    return cases


def check(case: Case) -> list[str]:
    """Everything the detector said that plain arithmetic on the raw bars does not support. Empty when it checks out."""
    d = case.draft
    if d is None:
        return ["no setup found"]
    problems: list[str] = []
    if d.setup_type != case.expected:
        problems.append(f"found {d.setup_type}, expected {case.expected}")
    problems += _check_prices(case, d)
    if d.setup_type == "base_breakout":
        problems += _check_base(case.bars, d)
    elif d.setup_type == "week_52_high_breakout":
        problems += _check_week_52(case.bars, d)
    else:
        problems += _check_pullback(case.bars, d)
    return problems


def _check_prices(case: Case, d: setups.Draft) -> list[str]:
    """Order, risk band, tick grid and R multiples of the four prices."""
    problems: list[str] = []
    entry, stop, t1, t2 = (float(x) for x in (d.entry, d.stop, d.target_1, d.target_2))
    risk = entry - stop
    if not stop < entry < t1 < t2:
        problems.append("prices are not in order stop < entry < T1 < T2")
    if not SP.risk_atr_min * case.atr <= risk <= SP.risk_atr_max * case.atr + 2 * TICK:
        problems.append(f"risk {risk:.2f} is {risk / case.atr:.2f} ATR, outside {SP.risk_atr_min}-{SP.risk_atr_max}")
    for value in (entry, stop, t1, t2):
        if abs(value / TICK - round(value / TICK)) > 1e-6:
            problems.append(f"{value} is off the 0.05 tick")
    rule = {
        "base_breakout": SP.base_breakout,
        "ema_pullback": SP.ema_pullback,
        "week_52_high_breakout": SP.week_52_high_breakout,
    }[d.setup_type]
    rule_r = (rule.target_1_r, rule.target_2_r)  # type: ignore[attr-defined]
    for got, r, name in ((t1, rule_r[0], "T1"), (t2, rule_r[1], "T2")):
        # The stop rounds down (risk grows by under a tick, r times that in the target) and the target rounds down.
        if abs((got - entry) - r * risk) > (r + 1.01) * TICK:
            problems.append(f"{name} is {(got - entry) / risk:.3f}R, rule says {r}R")
    return problems


def _check_base(bars: Bars, d: setups.Draft) -> list[str]:
    """Base breakout rules recomputed with plain max/min/mean."""
    problems: list[str] = []
    high = [float(x) for x in bars.high]
    low = [float(x) for x in bars.low]
    close = [float(x) for x in bars.close]
    volume = [float(x) for x in bars.volume]
    n = len(close)
    base = slice(n - 1 - SP.base_breakout.base_bars, n - 1)
    base_high, base_low = max(high[base]), min(low[base])
    mean_volume = sum(volume[base]) / len(volume[base])
    if abs(d.facts["base_high"] - base_high) > 1e-9 or abs(d.facts["base_low"] - base_low) > 1e-9:
        problems.append("base high/low differ from a plain max/min over the 20 sessions before")
    if not close[-1] > base_high:
        problems.append("close is not above the base high")
    if (base_high - base_low) / base_high * 100 > SP.base_breakout.max_depth_pct:
        problems.append("base is deeper than the limit")
    if volume[-1] / mean_volume < SP.base_breakout.volume_ratio_min:
        problems.append("volume is under the minimum multiple")
    if close[-2] > max(high[n - 2 - SP.base_breakout.base_bars : n - 2]):
        problems.append("yesterday had already closed above the base")
    return problems


def _check_week_52(bars: Bars, d: setups.Draft) -> list[str]:
    """52-week high breakout rules recomputed with plain max/mean."""
    problems: list[str] = []
    high = [float(x) for x in bars.high]
    close = [float(x) for x in bars.close]
    volume = [float(x) for x in bars.volume]
    n = len(close)
    prior = max(high[max(0, n - 1 - SP.week_52_high_breakout.lookback) : n - 1])
    if abs(d.facts["prior_high"] - prior) > 1e-9 or not close[-1] > prior:
        problems.append("close is not above the highest high of the 250 sessions before")
    if volume[-1] / (sum(volume[-21:-1]) / 20) < SP.week_52_high_breakout.volume_ratio_min:
        problems.append("volume is under the minimum multiple")
    return problems


def _check_pullback(bars: Bars, d: setups.Draft) -> list[str]:
    """EMA pullback rules recomputed with a plain EMA."""
    problems: list[str] = []
    high = [float(x) for x in bars.high]
    low = [float(x) for x in bars.low]
    close = [float(x) for x in bars.close]
    n = len(close)
    ema = _plain_ema(close, int(d.facts["ema_period"]))
    if not (ema * (1 - SP.ema_pullback.touch_pct / 100) <= low[-1] <= ema * (1 + SP.ema_pullback.touch_pct / 100)):
        problems.append("the low is not within the band around the EMA")
    if close[-1] < ema or not close[-1] > float(bars.open[-1]):
        problems.append("the day did not close up and above the EMA")
    if (max(high[n - 1 - SP.ema_pullback.pullback_bars : n - 1]) - close[-1]) / close[-1] * 100 < 2.0 - 1e-9:
        problems.append("the pullback is shallower than the minimum")
    if not float(d.entry) > high[-1]:
        problems.append("entry is not above the day's high")
    return problems


def _plain_ema(close: list[float], period: int) -> float:
    """The EMA as TA-Lib defines it (seeded with the SMA of the first `period` closes), by a plain loop."""
    value = sum(close[:period]) / period
    k = 2.0 / (period + 1)
    for x in close[period:]:
        value += k * (x - value)
    return value


def library_ema(close: Bars, period: int) -> float:
    return float(indicators.get("ema", period=period).batch(close)[f"ema_{period}"][-1])
