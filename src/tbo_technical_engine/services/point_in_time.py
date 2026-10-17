"""The point-in-time universe and gate for a backtest: for each weekday, who was tradable and what the gate allowed.

Backtests read the fundamental engine's past gates (`GET /v1/gate?date=`), which never change once a day's run is done,
so the same request gives the same rules next month. A weekday with no gate (a holiday, or a run that never finished)
has no rules and therefore no trades.
"""

from __future__ import annotations

from datetime import date, timedelta

from tbo_technical_engine.backtest.engine import DayRules
from tbo_technical_engine.clients.fundamental import FundamentalClient


def weekdays(start: date, end: date) -> list[date]:
    """Monday-to-Friday dates from start to end inclusive."""
    days = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


async def load_rules(funds: FundamentalClient, start: date, end: date) -> DayRules:
    """Day-by-day gate rules from the fundamental engine, so a replay sees what the live gate saw."""
    by_day: dict[date, dict[str, str]] = {}
    for day in weekdays(start, end):
        statuses = await funds.gate(day)
        if statuses is not None:
            by_day[day] = statuses
    return DayRules(by_day)
