"""Keeps the engine warm: starts the warm-up, repeats it for stocks that failed, and redoes it after an outage.

One `tick` is one decision; the signal worker calls it every few seconds. Nothing here evaluates a strategy.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import date, datetime, time

from tbo_platform_kit.calendar import TradingCalendar
from tbo_platform_kit.config.calendar import IST
from tbo_platform_kit.http_client import UpstreamUnavailable
from tbo_platform_kit.logging import get_logger

from tbo_technical_engine.clients.market_feed import MarketFeedClient
from tbo_technical_engine.services.candle_buffer import CandleBuffer
from tbo_technical_engine.services.readiness import Readiness
from tbo_technical_engine.services.universe import UniverseHolder
from tbo_technical_engine.services.warmup import WarmupSpec, is_warm, warm_up
from tbo_technical_engine.store.research_cache import ResearchCache

log = get_logger(__name__)
SESSION_WARMUP_AT = time(9, 0)
"""09:00 IST: each trading day starts with a fresh warm-up, so candles the feed lost overnight are repaired."""


class WarmupSupervisor:
    """Keeps the buffer filled: asks for a fresh warm-up at the start of each session, reads the feed's status at start,
    and warms any stock that is not yet warm."""

    def __init__(
        self,
        client: MarketFeedClient,
        buffer: CandleBuffer,
        readiness: Readiness,
        universe: UniverseHolder,
        spec: WarmupSpec,
        calendar: TradingCalendar,
        clock: Callable[[], datetime],
        cache: ResearchCache | None = None,
        prime: Callable[[str], None] | None = None,
    ) -> None:
        self.client = client
        self.buffer = buffer
        self.readiness = readiness
        self.universe = universe
        self.spec = spec
        self.calendar = calendar
        self.clock = clock
        self.cache = cache
        self.prime = prime
        """Called for each stock after a warm-up, to build its indicator state before the first candle needs it."""
        self._session_day: date | None = None

    async def tick(self) -> None:
        """One supervision step; safe to call every few seconds. Does nothing outside trading days or without a
        universe."""
        now = self.clock()
        local = now.astimezone(IST)
        if not self.calendar.is_trading_day(local.date()) or not self.universe.isins:
            return
        if local.time() >= SESSION_WARMUP_AT and self._session_day != local.date():
            self.readiness.request_warmup()
            self._session_day = local.date()
        if self.readiness.feed_state == "connecting":
            try:
                self.readiness.on_feed_status(await self.client.feed_status())
            except UpstreamUnavailable as exc:
                log.warning("feed_status_unavailable", error=str(exc)[:200])
                return
        if not self.readiness.feed_healthy:
            return  # nothing trustworthy to warm from; the next healthy status brings us back
        everyone = sorted(self.universe.isins)
        targets = (
            everyone if self.readiness.needs_warmup else [i for i in everyone if not is_warm(self.buffer, i, self.spec)]
        )
        if not targets:
            return
        generation = self.readiness.generation
        await warm_up(self.client, self.buffer, targets, now, self.calendar, self.spec, cache=self.cache)
        await self._prime(targets)
        if self.readiness.needs_warmup and not self.readiness.mark_warm(generation):
            log.warning("warmup_discarded_feed_went_unhealthy")

    async def _prime(self, isins: list[str]) -> None:
        """Build each warmed stock's indicator state, one stock at a time with a yield between, so the event loop (the
        candle consumers) keeps running while a few hundred stocks are primed."""
        if self.prime is None:
            return
        for isin in isins:
            if is_warm(self.buffer, isin, self.spec):
                self.prime(isin)
            await asyncio.sleep(0)
