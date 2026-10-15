"""Liquidity metrics for the fundamental gate: the 20-day average traded value and an estimated spread.

The traded value is `close * volume` per session, from UNADJUSTED candles: an adjusted close times a raw volume
would understate it after a split, and the gate would wrongly call a busy stock illiquid.

The spread is the Corwin-Schultz (2012) high-low estimator, because daily candles have no quotes. For each pair of
consecutive sessions (high H, low L):

    beta  = ln(H1/L1)^2 + ln(H2/L2)^2          gamma = ln(max(H1,H2) / min(L1,L2))^2
    k     = 3 - 2*sqrt(2)
    alpha = (sqrt(2*beta) - sqrt(beta)) / k - sqrt(gamma / k)
    S     = 2 * (e^alpha - 1) / (1 + e^alpha)            (the pair's estimate, as a fraction of the price)

A negative estimate (noise) counts as 0, and the result is the mean over the last `days` pairs, in percent. It is an
estimate, and it reads high on a stock with large intraday moves; the gate treats it as a coarse tier input.
"""

from __future__ import annotations

import math

import numpy as np

from tbo_technical_engine.indicators.bars import F64

K = 3.0 - 2.0 * math.sqrt(2.0)


def turnover(close: F64, volume: F64, days: int) -> float:
    """Mean of close * volume over the last `days` sessions (fewer if the stock has fewer)."""
    n = min(days, len(close))
    return float(np.mean(close[-n:] * volume[-n:]))


def pair_spreads(high: F64, low: F64) -> F64:
    """Corwin-Schultz estimate for every consecutive pair; nan where a price is not positive."""
    h1, h2, l1, l2 = high[:-1], high[1:], low[:-1], low[1:]
    out = np.full(len(h1), np.nan)
    ok = (h1 > 0) & (h2 > 0) & (l1 > 0) & (l2 > 0) & (h1 >= l1) & (h2 >= l2)
    if not ok.any():
        return out
    beta = np.log(h1[ok] / l1[ok]) ** 2 + np.log(h2[ok] / l2[ok]) ** 2
    gamma = np.log(np.maximum(h1[ok], h2[ok]) / np.minimum(l1[ok], l2[ok])) ** 2
    alpha = (np.sqrt(2.0 * beta) - np.sqrt(beta)) / K - np.sqrt(gamma / K)
    e = np.exp(alpha)
    out[ok] = 2.0 * (e - 1.0) / (1.0 + e)
    return out


def spread_pct(high: F64, low: F64, days: int, min_pairs: int) -> float | None:
    """The mean estimate over the last `days` pairs, in percent; None when fewer than `min_pairs` are usable."""
    pairs = pair_spreads(high, low)[-days:]
    usable = pairs[np.isfinite(pairs)]
    if len(usable) < min_pairs:
        return None
    return float(np.mean(np.maximum(usable, 0.0)) * 100.0)
