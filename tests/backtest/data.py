"""A research cache filled from the synthetic session history, and a runner over it."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tbo_technical_engine.backtest.engine import DayRules
from tbo_technical_engine.backtest.replay import day_of
from tbo_technical_engine.schemas.backtest import BacktestSettings
from tbo_technical_engine.services.backtests import BacktestRunner
from tbo_technical_engine.store.research_cache import ResearchCache
from tests.backtest.builders import COSTS, SETTINGS
from tests.strategies.session import CONFIG, History
from tests.support import ISIN_A

HISTORY = History(days=24, seed=12)
FIRST = day_of(HISTORY.sessions[12][0])
LAST = day_of(HISTORY.sessions[19][0])
NOW = datetime(2026, 10, 16, 20, 0, tzinfo=UTC)


def filled_cache(root: Path, isins: tuple[str, ...] = (ISIN_A,)) -> ResearchCache:
    cache = ResearchCache(root)
    for isin in isins:
        history = HISTORY if isin == ISIN_A else History(days=24, seed=12, isin=isin)
        cache.write(isin, "5m", history.five)
        cache.write(isin, "15m", history.fifteen)
        cache.write(isin, "1d", history.daily)
    return cache


def runner(
    sessions: Any,
    cache: ResearchCache,
    *,
    settings: BacktestSettings = SETTINGS,
    rules: Callable[..., Any] | None = None,
    monotonic: Callable[[], float] | None = None,
) -> BacktestRunner:
    kwargs: dict[str, Any] = {} if monotonic is None else {"monotonic": monotonic}
    return BacktestRunner(sessions, CONFIG, settings, COSTS, cache, rules, clock=lambda: NOW, **kwargs)


def rules_for(isin: str = ISIN_A, status: str = "both") -> Callable[..., Any]:
    async def load(start: Any, end: Any) -> DayRules:
        return DayRules({day_of(c): {isin: status} for c in HISTORY.five if start <= day_of(c) <= end})

    return load
