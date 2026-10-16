"""Return statistics from vectorbt's returns accessor.

Trade results are in R everywhere else; this turns the daily rupee profit into a return on a reference capital
(`reporting.reference_capital`, a reporting convention that never changes a trade) so Sharpe and drawdown % are
computed by a library everyone knows instead of by hand. Days without a trade count as a zero return: leaving them
out would make the Sharpe look better than it is.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import date
from decimal import Decimal

import pandas as pd
import vectorbt as vbt  # noqa: F401  (importing registers the `.vbt` accessor on pandas objects)
from tbo_platform_kit.calendar import IST

from tbo_technical_engine.schemas.backtest import Trade

TRADING_DAYS_PER_YEAR = 252


def equity_stats(
    trades: Sequence[Trade], sessions: Sequence[date], reference_capital: Decimal
) -> dict[str, float | None]:
    """`total_return_pct`, `sharpe` (annualised on 252 sessions) and `max_drawdown_pct` (a positive number; vectorbt's
    own sign is negative) of the daily returns."""
    if not sessions:
        return {"total_return_pct": None, "sharpe": None, "max_drawdown_pct": None}
    profit: dict[date, float] = dict.fromkeys(sessions, 0.0)
    for t in trades:
        day = t.exit_at.astimezone(IST).date()
        if day in profit:
            profit[day] += float(t.gross_pnl - t.costs)
    series = pd.Series(profit).sort_index() / float(reference_capital)
    series.index = pd.DatetimeIndex(series.index)
    year = f"{TRADING_DAYS_PER_YEAR} days"
    returns = series.vbt.returns(freq="1D", year_freq=year)  # type: ignore[attr-defined]
    sharpe = float(returns.sharpe_ratio())
    return {
        "total_return_pct": round(float(returns.total()) * 100, 4),
        # No volatility at all (a single trade, or none) makes Sharpe infinite or NaN: that is "not defined", and JSON
        # has no infinity.
        "sharpe": round(sharpe, 4) if math.isfinite(sharpe) else None,
        "max_drawdown_pct": abs(round(float(returns.max_drawdown()) * 100, 4)),
    }
