"""The market context every 5 minutes.

At each 5-minute boundary of a trading session (09:15, 09:20, ... 15:30 IST) shortly after it, the service builds the
context from the candle buffer (breadth over the universe), the index source (index states, VIX, CPR class) and the
readiness (data quality), stores it and announces it through the outbox. One row per boundary: a second process or a
restart inside the same boundary finds the row and publishes nothing. While the feed is unhealthy the context is still
published, with `data_quality` degraded, because the decision engine stops issuing calls on exactly that signal.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tbo_platform_kit.calendar import TradingCalendar
from tbo_platform_kit.config.calendar import IST
from tbo_platform_kit.events import EventPublisher
from tbo_platform_kit.logging import get_logger
from tbo_signals import Signals

from tbo_technical_engine import outbox
from tbo_technical_engine.context import compute
from tbo_technical_engine.context.index_source import IndexSnapshot, IndexSource, NoIndexSource
from tbo_technical_engine.schemas.candle import Candle
from tbo_technical_engine.schemas.context import MarketContext
from tbo_technical_engine.services.candle_buffer import CandleBuffer
from tbo_technical_engine.services.latency import regular_window
from tbo_technical_engine.services.readiness import Readiness
from tbo_technical_engine.services.universe import UniverseHolder
from tbo_technical_engine.store import context as store
from tbo_technical_engine.utils.timefmt import z

log = get_logger(__name__)
CONTEXT_EVENT = "tbo.technical.market.context.v1"
CONTEXT_SCHEMA = "https://tbo.internal/schemas/technical/market_context.v1.json"
GRACE = timedelta(seconds=15)
"""How long after a boundary the context is built, so the candle that closed at the boundary has arrived."""
PRIMARY_INDEX = "nifty_50"


def boundary_of(now: datetime) -> datetime:
    """The latest 5-minute boundary at or before `now`, in IST."""
    local = now.astimezone(IST)
    return local.replace(minute=local.minute - local.minute % 5, second=0, microsecond=0)


class ContextService:
    """Builds the market context at each 5-minute boundary of the session and publishes it once per boundary (stored
    first, then announced through the outbox)."""

    def __init__(
        self,
        buffer: CandleBuffer,
        universe: UniverseHolder,
        readiness: Readiness,
        sessions: async_sessionmaker[AsyncSession],
        publisher: EventPublisher,
        calendar: TradingCalendar,
        clock: Callable[[], datetime],
        source: IndexSource | None = None,
        narrow_pct: float = compute.NARROW_PCT,
        wide_pct: float = compute.WIDE_PCT,
    ) -> None:
        self.buffer = buffer
        self.universe = universe
        self.readiness = readiness
        self.sessions = sessions
        self.publisher = publisher
        self.calendar = calendar
        self.clock = clock
        self.signals: Signals | None = None
        """Counts published events (`technical.signal_events`)."""
        self.source: IndexSource = source or NoIndexSource()
        self.narrow_pct = narrow_pct
        self.wide_pct = wide_pct
        self.latest: tuple[datetime, MarketContext] | None = None
        self._done: datetime | None = None
        self._unavailable: datetime | None = None
        """The boundary at which "calendar unavailable" was last logged: once per boundary, not every tick."""
        self._day_class: tuple[date, Literal["narrow", "normal", "wide"] | None] | None = None

    async def build(self, at: datetime) -> MarketContext:
        """The context as of boundary `at` (IST)."""
        day = at.astimezone(IST).date()
        degraded = not self.readiness.active
        try:
            found = await self.source.fetch(at)
        except Exception as exc:
            # A configured source that fails means index data is missing, which is a data-quality problem.
            log.warning("index_source_failed", error=type(exc).__name__)
            found, degraded = IndexSnapshot(), True
        states = [s for s in (compute.index_state(i, day) for i in found.indices) if s is not None]
        primary = next(
            (i for i in found.indices if i.code == PRIMARY_INDEX), found.indices[0] if found.indices else None
        )
        return MarketContext(
            context_at=z(at),
            trade_date=day.isoformat(),
            index_states=states,
            vix=compute.vix_state(found.vix),
            breadth=compute.breadth(self.buffer, self.universe.isins, day),
            cpr_class=self._cpr_class(day, primary.previous_day if primary else None),
            data_quality="degraded" if degraded else "ok",
        )

    def _cpr_class(self, day: date, previous: Candle | None) -> Literal["narrow", "normal", "wide"] | None:
        """Set once per day (at 09:15, from the previous session) and kept for the rest of the session."""
        if self._day_class is not None and self._day_class[0] == day and self._day_class[1] is not None:
            return self._day_class[1]
        klass = compute.cpr_class(previous, self.narrow_pct, self.wide_pct)
        self._day_class = (day, klass)
        return klass

    async def tick(self) -> bool:
        """Publish the context of the current boundary if it is due and not yet done. True when one was published."""
        now = self.clock()
        at = boundary_of(now)
        if self._done == at or now - at < GRACE:
            return False
        try:
            window = regular_window(self.calendar, at.date())
        except Exception as exc:  # no calendar yet (the filings extractor has not answered)
            if self._unavailable != at:
                log.warning("calendar_unavailable", error=type(exc).__name__)
                self._unavailable = at
            return False
        # The calendar's session (a delayed open moves it; a special session has none, review 9.22).
        if window is None or not (window[0] <= at <= window[1]):
            return False
        context = await self.build(at)
        async with self.sessions() as session, session.begin():
            fresh = await store.save(session, at, context, now)
            if fresh:
                event = self.publisher.build(
                    CONTEXT_EVENT,
                    subject="market",
                    data=context.model_dump(mode="json"),
                    dataschema=CONTEXT_SCHEMA,
                    time=now,
                )
                await outbox.add(session, event)
        self._done = at
        self.latest = (at, context)
        if fresh:
            await outbox.flush_after_commit(self.sessions, self.publisher, self.signals)
            log.info("market_context_published", context_at=context.context_at, quality=context.data_quality)
        return fresh

    def index_side(self, candle_start: datetime) -> int | None:
        """+1 / -1 when the primary index was above / below its VWAP in the context at the time of the entry candle
        (the one of the boundary the candle started at, or the one it closes at), None when there is none."""
        if self.latest is None:
            return None
        at, context = self.latest
        if not (candle_start - timedelta(minutes=5) < at <= candle_start + timedelta(minutes=5)):
            return None
        states = {s.index_code: s for s in context.index_states}
        state = states.get(PRIMARY_INDEX) or next(iter(states.values()), None)
        if state is None or state.vwap is None:
            return None
        return 1 if state.is_above_vwap else -1
