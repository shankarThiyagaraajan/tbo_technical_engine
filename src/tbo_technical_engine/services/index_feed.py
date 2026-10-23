"""The market feed as the index source (ADR-019): index and India VIX candles for the market context.

The feed polls Kite every 5 minutes and announces each closed index candle as `tbo.market_feed.index_candle.closed.v1`;
the `technical.indices` consumer hands each one to `FeedIndexSource.offer`, which keeps the newest revision in memory.
The context service calls `fetch` at each boundary. REST is the fallback: the first context of a day reads today's 5m
and the recent 15m and 1d candles over `GET /v1/index-candles` (a restart mid-session has no events for the earlier
candles), and an index whose events stopped is read again over REST, at most once per 5 minutes.

When neither gives an index its candles, the index is left out of the context (never guessed) and
`technical.index_missing` counts it; the decision engine then scores without the index (its renormalised scorer).
Missing index data does not mark the context degraded: degraded stops every call, and an index is one input of many.

Timing: the feed polls 20 s after a boundary and the context is built 15 s after it, so the index states of the
context at T usually come from the candle that closed at T - 5 minutes. The stale limit below allows for that.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from tbo_platform_kit.config.calendar import IST
from tbo_platform_kit.logging import get_logger
from tbo_signals import Signals

from tbo_technical_engine.clients.market_feed import MarketFeedClient
from tbo_technical_engine.context.index_source import IndexSeries, IndexSnapshot, VixReading
from tbo_technical_engine.schemas.candle import IndexCandle

log = get_logger(__name__)
VIX = "india_vix"
EXPECTED = ("nifty_50", VIX)
"""What is expected while the feed's list of tracked indices is unknown (its API down since the start)."""
OPEN = time(9, 15)
FIVE = timedelta(minutes=5)
STALE = timedelta(minutes=10)
"""An index whose newest 5m candle ended longer than this before the boundary is behind: two candles late, since
one candle of lag is normal (see Timing above)."""
REFILL_EVERY = timedelta(minutes=5)
"""At most one REST read per index in this time while its events are missing, so a down feed costs 30 requests a
boundary, not a storm."""
TRACKED_EVERY = timedelta(hours=1)
HISTORY_15M = timedelta(days=7)
"""15-minute history read at the first context of a day: about 25 candles a session, enough for EMA 21 and
Supertrend(10, 3) to have warmed up by the open."""
HISTORY_1D = timedelta(days=10)
"""Daily history read at the first context of a day: the previous session (CPR, VIX change) even after a long
holiday weekend."""

Held = dict[tuple[str, str], dict[datetime, IndexCandle]]


def session_open(day: date) -> datetime:
    """09:15 IST of `day`."""
    return datetime.combine(day, OPEN, IST)


class FeedIndexSource:
    """Index and VIX candles from the market feed's events, with its REST API as the fallback."""

    def __init__(self, feed: MarketFeedClient, signals: Signals | None = None) -> None:
        self.feed = feed
        self.signals = signals
        self.tracked: tuple[str, ...] = ()
        """The feed's tracked indices (India VIX among them), from `GET /v1/indices`, refreshed hourly."""
        self._tracked_at: datetime | None = None
        self._held: Held = {}
        self._warm: dict[str, date] = {}
        """The day each index's history was last read over REST."""
        self._tried: dict[str, datetime] = {}

    def offer(self, candle: IndexCandle) -> bool:
        """Keep a candle from the event stream unless a copy with the same or a higher revision is held. True when it
        changed what is held."""
        held = self._held.setdefault((candle.index_code, candle.timeframe), {})
        old = held.get(candle.start_at)
        if old is not None and old.revision >= candle.revision:
            return False
        held[candle.start_at] = candle
        return True

    def reset(self) -> None:
        """Read every index's history over REST again at the next context (the consumer group was lost, so events
        published meanwhile never arrive)."""
        self._warm.clear()
        self._tried.clear()

    async def fetch(self, now: datetime) -> IndexSnapshot:
        """The index states' inputs and the VIX as of boundary `now`. Never raises: a missing index is left out."""
        day = now.astimezone(IST).date()
        await self._refresh_tracked(now)
        expected = self.tracked or tuple(sorted({code for code, _ in self._held} | set(EXPECTED)))
        missing = 0
        for code in expected:
            if self._warm.get(code) != day:
                await self._read(code, now, warm=True)
            elif self._behind(code, now):
                await self._read(code, now, warm=False)
            if self._behind(code, now):
                missing += 1
        self._prune(day)
        if self.signals is not None:
            self.signals.emit("technical.index_missing", missing)
        indices = [s for s in (self._series(code, day) for code in expected if code != VIX) if s.candles_5m]
        return IndexSnapshot(indices=indices, vix=self._vix(day))

    async def _refresh_tracked(self, now: datetime) -> None:
        """The tracked list, at most hourly; a failure keeps the list held (or none, then `EXPECTED` is used)."""
        if self._tracked_at is not None and now - self._tracked_at < TRACKED_EVERY:
            return
        self._tracked_at = now
        try:
            found = await self.feed.indices()
        except Exception as exc:  # any failure of the feed's API: keep what is known
            log.warning("index_list_unavailable", error=type(exc).__name__)
            return
        self.tracked = tuple(sorted(i.index_code for i in found if i.is_tracked))

    def _behind(self, code: str, now: datetime) -> bool:
        """True when today's candles of `code` are two or more 5-minute candles late at boundary `now`."""
        day = now.astimezone(IST).date()
        start = session_open(day)
        if now - start < STALE:
            return False  # nothing is due yet
        today = [s for s in self._held.get((code, "5m"), {}) if s >= start]
        return not today or max(today) + FIVE < now - STALE

    async def _read(self, code: str, now: datetime, *, warm: bool) -> None:
        """Read `code`'s candles over REST: today's 5m and 15m, and with `warm` the 15m and 1d history too. At most
        once per `REFILL_EVERY` per index; a failure keeps what is held."""
        last = self._tried.get(code)
        if last is not None and timedelta(0) <= now - last < REFILL_EVERY:
            return
        self._tried[code] = now
        day = now.astimezone(IST).date()
        today = session_open(day)
        reads = [("5m", today), ("15m", now - HISTORY_15M if warm else today)]
        if warm:
            reads.append(("1d", now - HISTORY_1D))
        try:
            for timeframe, start in reads:
                async for candle in self.feed.index_candles(code, timeframe, start, now):
                    self.offer(candle)
        except Exception as exc:  # the feed down or a bad page: the stream may still bring the candles
            log.warning("index_candles_unavailable", index_code=code, error=type(exc).__name__)
            if self.signals is not None:
                self.signals.emit("technical.index_reads", 1, result="failed")
            return
        if warm:
            self._warm[code] = day
        if self.signals is not None:
            self.signals.emit("technical.index_reads", 1, result="ok")

    def _sorted(self, code: str, timeframe: str) -> list[IndexCandle]:
        held = self._held.get((code, timeframe), {})
        return [held[s] for s in sorted(held)]

    def _series(self, code: str, day: date) -> IndexSeries:
        """One index's candles for the context: today's 5m, the recent 15m, the previous session's 1d."""
        start = session_open(day)
        today = [c for c in self._sorted(code, "5m") if c.start_at >= start]
        before = [c for c in self._sorted(code, "1d") if c.start_at.astimezone(IST).date() < day]
        return IndexSeries(
            code=code,
            candles_5m=[c.as_candle() for c in today],
            candles_15m=[c.as_candle() for c in self._sorted(code, "15m")],
            previous_day=before[-1].as_candle() if before else None,
            vwap=today[-1].session_vwap if today else None,
        )

    def _vix(self, day: date) -> VixReading | None:
        """India VIX: the newest 5m close of today against the previous session's close; None without either."""
        series = self._series(VIX, day)
        if not series.candles_5m or series.previous_day is None:
            return None
        return VixReading(float(series.candles_5m[-1].close), float(series.previous_day.close))

    def _prune(self, day: date) -> None:
        """Drop what no context needs any more: 5m before today, 15m and 1d older than their history windows."""
        start = session_open(day)
        keep = {"5m": start, "15m": start - HISTORY_15M, "1d": start - HISTORY_1D - timedelta(days=1)}
        for (_, timeframe), held in self._held.items():
            for s in [s for s in held if s < keep[timeframe]]:
                del held[s]
