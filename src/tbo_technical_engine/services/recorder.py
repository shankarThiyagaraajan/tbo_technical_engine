"""Writes the indicator values behind each evaluation to `indicator_snapshot` (audit, and the indicators API).

The rows are not part of a candidate's path: the recorder takes a snapshot of the values in memory when a candle is
evaluated and a background loop writes the waiting snapshots in one transaction every half second. (A transaction per
candle, in front of the strategies, was most of the time a 500-stock boundary took: TE7, `docs/adr/0001`.) A crash loses
at most the last half second of audit rows; the candidates themselves are written by `SignalRunner` as before.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tbo_platform_kit.logging import get_logger

from tbo_technical_engine.indicators.format import decimals
from tbo_technical_engine.schemas.candle import Candle
from tbo_technical_engine.schemas.indicators import IndicatorSnapshot
from tbo_technical_engine.services.indicator_engine import BACKEND, IndicatorEngine
from tbo_technical_engine.store import indicators as store

log = get_logger(__name__)
RECORDED = ("5m", "15m")
"""Timeframes the API serves. The 15-minute row is written once per 15-minute candle, not on every 5-minute one."""
FLUSH_SECONDS = 0.5
"""How long a snapshot may wait for the next write."""
MAX_PENDING = 20_000
"""Snapshots kept when the database is unreachable (about 20 boundaries of a 500-stock universe); older ones are
dropped first, with a warning: the audit trail is not worth the worker's memory."""

Key = tuple[str, str, datetime]


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
        self._pending: dict[Key, IndicatorSnapshot] = {}

    async def __call__(self, candle: Candle) -> None:
        """Take the 5-minute and 15-minute snapshots as of the newest candle held; `flush` writes them."""
        now = self.clock()
        for timeframe in RECORDED:
            found = self.engine.values(candle.isin, timeframe)
            if found is None:
                continue
            newest, values = found
            mark = (newest.start_at, newest.revision)
            if self._saved.get((candle.isin, timeframe)) == mark:
                continue
            self._pending[(candle.isin, timeframe, newest.start_at)] = IndicatorSnapshot(
                isin=candle.isin,
                timeframe=timeframe,
                candle_start_at=newest.start_at,
                values=decimals(values),
                backend=BACKEND,
                computed_at=now,
            )
            self._saved[(candle.isin, timeframe)] = mark
        while len(self._pending) > MAX_PENDING:
            del self._pending[next(iter(self._pending))]
            log.warning("indicator_snapshot_dropped", reason="database unreachable for too long")

    @property
    def pending(self) -> int:
        """Snapshots taken and not yet written."""
        return len(self._pending)

    async def flush(self) -> int:
        """Write every waiting snapshot in one transaction; returns how many. On a failure they stay queued for the next
        flush (a snapshot taken meanwhile for the same candle replaces its older copy)."""
        if not self._pending:
            return 0
        batch = self._pending
        self._pending = {}
        try:
            async with self.sessions() as session, session.begin():
                return await store.save_many(session, batch.values())
        except Exception as exc:
            log.error("indicator_snapshot_write_failed", error=type(exc).__name__, waiting=len(batch))
            for key, snapshot in batch.items():
                self._pending.setdefault(key, snapshot)
            return 0

    async def run(self, stop: asyncio.Event, interval: float = FLUSH_SECONDS) -> None:
        """Flush every `interval` seconds until `stop` is set, then once more so a clean stop loses nothing."""
        while not stop.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), interval)
            await self.flush()
