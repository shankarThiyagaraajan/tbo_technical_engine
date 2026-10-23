"""The trading calendar from the filings extractor, kept fresh in place.

`LiveCalendar` is a `TradingCalendar` whose data is replaced by `refresh()`, so every holder of the object (supervisor,
context service, snapshot service, latency gauges) sees a new calendar without being rebuilt. It is refreshed at start,
every hour and when the extractor announces a change (`tbo.filings.market_calendar.updated.v1`).

Without `TBO_TECHNICAL_FILINGS_URL` it stays the empty calendar the kit gives (`CalendarUnknown` on every question: not
ready, never a guessed market day) and says so once. With the URL but neither API nor cache the answer is the same, and
a failed refresh never replaces a working calendar by an empty one.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from tbo_platform_kit.calendar import TradingCalendar, now_utc
from tbo_platform_kit.http_client import IssuerTokenProvider, SyncServiceClient, TokenProvider
from tbo_platform_kit.logging import get_logger
from tbo_signals import Signals

from tbo_technical_engine.config.settings import Settings

log = get_logger(__name__)

AUDIENCE = "tbo_filings_extractor_service"
SCOPES = ("filings:read",)
REFRESH_SECONDS = 3600
"""One rebuild an hour, besides the one the event triggers: the event can be lost, the hour is the bound."""


class LiveCalendar(TradingCalendar):
    """A `TradingCalendar` that can be rebuilt from the API while others hold it."""

    def __init__(
        self,
        client: SyncServiceClient | None = None,
        cache_path: Path | None = None,
        clock: Callable[[], datetime] = now_utc,
    ) -> None:
        super().__init__({})
        self.clock = clock
        self.client = client
        self.cache_path = cache_path
        self.signals: Signals | None = None
        self._lock = asyncio.Lock()

    @property
    def configured(self) -> bool:
        """True when there is an API to ask; without one the calendar stays empty."""
        return self.client is not None and self.cache_path is not None

    async def refresh(self) -> bool:
        """Fetch the calendar (in a thread: the kit client is blocking) and swap it in. True when a calendar with data
        was installed. An answer with no data at all (API down, no cache) never replaces what is already known."""
        if self.client is None or self.cache_path is None:
            return False
        async with self._lock:
            client, path = self.client, self.cache_path
            fresh = await asyncio.to_thread(self._build, client, path)
            if fresh.source == "none" and self.source != "none":
                log.warning("calendar_refresh_empty_kept_previous")
                self._report(False)
                return False
            self._install(fresh)
            self._report(fresh.source != "none")
            return fresh.source != "none"

    def _build(self, client: SyncServiceClient, path: Path) -> TradingCalendar:
        return TradingCalendar.from_service(
            client, cache_path=path, exchange=self.exchange, segment=self.segment, now=self.clock()
        )

    def _install(self, fresh: TradingCalendar) -> None:
        """One synchronous step on the event loop, so no reader sees half of the old data and half of the new."""
        self.holidays = fresh.holidays
        self.special = fresh.special
        self.timing = fresh.timing
        self.source = fresh.source
        self.fetched_at = fresh.fetched_at

    def _report(self, ok: bool) -> None:
        if self.signals is None:
            return
        self.signals.activity("technical.worker", ok, worker="calendar")
        age = self.cache_age_seconds()
        if age is not None:
            self.signals.emit("technical.calendar_age", age)

    async def run(self, stop: asyncio.Event) -> None:
        """Refresh at start and then every hour until `stop`. Not configured: say so once and end."""
        if not self.configured:
            log.warning("calendar_not_configured", setting="TBO_TECHNICAL_FILINGS_URL")
            if self.signals is not None:
                self.signals.activity("technical.worker", False, worker="calendar")
            return
        while not stop.is_set():
            try:
                await self.refresh()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # one bad refresh must not end the loop
                log.error("calendar_refresh_failed", error=type(exc).__name__)
                self._report(False)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), REFRESH_SECONDS)


def build_calendar(
    settings: Settings, tokens: TokenProvider | None = None, signals: Signals | None = None
) -> LiveCalendar:
    """The process's calendar: wired to the filings extractor when `filings_url` is set, empty (with a warning on
    `run`) when it is not. `tokens` defaults to the token issuer from the settings."""
    calendar = LiveCalendar()
    calendar.signals = signals
    if settings.filings_url is not None:
        calendar.client = SyncServiceClient(
            str(settings.filings_url),
            audience=AUDIENCE,
            scopes=SCOPES,
            tokens=tokens or IssuerTokenProvider.from_settings(settings),
        )
        calendar.cache_path = settings.calendar_cache_file
    return calendar
