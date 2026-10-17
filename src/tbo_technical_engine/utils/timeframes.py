"""Candle timeframes as minutes: the one place that knows `5m` is five minutes."""

from __future__ import annotations

from datetime import timedelta

MINUTES = {"1m": 1, "5m": 5, "15m": 15, "60m": 60}
"""Intraday timeframes the feed publishes as `candle.closed` events. 1d and 1w are history-only."""


def length(timeframe: str) -> timedelta:
    """How long one candle of `timeframe` lasts; KeyError for a timeframe the service does not know."""
    if timeframe == "1d":
        return timedelta(days=1)
    if timeframe == "1w":
        return timedelta(weeks=1)
    return timedelta(minutes=MINUTES[timeframe])
