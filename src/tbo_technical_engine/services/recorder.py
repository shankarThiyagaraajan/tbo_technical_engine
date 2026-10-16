"""Writes the indicator values behind each evaluation to `indicator_snapshot` (audit, and the indicators API)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tbo_technical_engine.indicators.format import decimals
from tbo_technical_engine.schemas.candle import Candle
from tbo_technical_engine.schemas.indicators import IndicatorSnapshot
from tbo_technical_engine.services.indicator_engine import BACKEND, IndicatorEngine
from tbo_technical_engine.store import indicators as store

RECORDED = ("5m", "15m")
"""Timeframes the API serves. The 15-minute row is written once per 15-minute candle, not on every 5-minute one."""


class IndicatorRecorder:
    """Saves a snapshot of the indicator values after each evaluated candle. Each (stock, timeframe, candle, revision)
    is written once; a redelivered candle is skipped."""

    def __init__(
        self, engine: IndicatorEngine, sessions: async_sessionmaker[AsyncSession], clock: Callable[[], datetime]
    ) -> None:
        self.engine = engine
        self.sessions = sessions
        self.clock = clock
        self._saved: dict[tuple[str, str], tuple[datetime, int]] = {}

    async def __call__(self, candle: Candle) -> None:
        """Record the 5-minute and 15-minute snapshots as of the newest candle held, in one transaction."""
        now = self.clock()
        async with self.sessions() as session, session.begin():
            for timeframe in RECORDED:
                found = self.engine.values(candle.isin, timeframe)
                if found is None:
                    continue
                newest, values = found
                mark = (newest.start_at, newest.revision)
                if self._saved.get((candle.isin, timeframe)) == mark:
                    continue
                await store.save(
                    session,
                    IndicatorSnapshot(
                        isin=candle.isin,
                        timeframe=timeframe,
                        candle_start_at=newest.start_at,
                        values=decimals(values),
                        backend=BACKEND,
                        computed_at=now,
                    ),
                )
                self._saved[(candle.isin, timeframe)] = mark
