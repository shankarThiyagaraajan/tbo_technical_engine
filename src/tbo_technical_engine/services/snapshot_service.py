"""The daily swing run: the technical snapshot of every stock in the universe, and the swing setups.

Two runs a trading day, both idempotent:

  * `close` (16:00 IST): reads the daily candles up to today's, measures every stock, ranks them against each other,
    stores and announces each snapshot, then looks for setups on the same data and announces the new ones.
  * `morning` (08:00 IST): the same snapshots as of the previous session, because candles can be corrected and
    adjustment factors can arrive overnight. A stock whose numbers did not change is not announced again (the stored
    content hash decides), and no setups are looked for: a setup is found after a close, once.

One stock that cannot be loaded never stops the others, but if more than `max_failed_share` of the universe fails the
run publishes nothing and raises: a rank over a partial universe would be quietly wrong, and the job retries.
Everything is written in one transaction, so a crash leaves nothing half-published.
"""

from __future__ import annotations

import asyncio
import time as clock_time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tbo_platform_kit.calendar import IST, TradingCalendar
from tbo_platform_kit.events import EventPublisher
from tbo_platform_kit.http_client import UpstreamUnavailable
from tbo_platform_kit.logging import get_logger

from tbo_technical_engine import outbox
from tbo_technical_engine.clients.fundamental import FundamentalClient
from tbo_technical_engine.clients.market_feed import MarketFeedClient
from tbo_technical_engine.indicators.bars import Bars
from tbo_technical_engine.schemas.candle import UniverseSnapshot
from tbo_technical_engine.schemas.swing import Setup, SwingParams
from tbo_technical_engine.store import snapshots as snapshot_store
from tbo_technical_engine.store import swing_setups as setup_store
from tbo_technical_engine.swing import analysis, liquidity, rank
from tbo_technical_engine.swing.analysis import Measured
from tbo_technical_engine.utils.ids import setup_id
from tbo_technical_engine.utils.timefmt import z

log = get_logger(__name__)
SNAPSHOT_EVENT = "tbo.technical.technical.snapshot.v1"
SNAPSHOT_SCHEMA = "https://tbo.internal/schemas/technical/technical_snapshot.v1.json"
SETUP_EVENT = "tbo.technical.swing.setup.v1"
SETUP_SCHEMA = "https://tbo.internal/schemas/technical/swing_setup.v1.json"
CLOSE = time(15, 30)

Kind = Literal["close", "morning"]


class IncompleteRun(RuntimeError):
    """Too many stocks could not be loaded to rank the rest; nothing was published."""


@dataclass
class Summary:
    kind: Kind
    as_of: date | None = None
    universe: int = 0
    snapshots_new: int = 0
    snapshots_unchanged: int = 0
    setups_new: int = 0
    too_short: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    skipped: str | None = None
    seconds: float = 0.0


@dataclass(frozen=True)
class Loaded:
    measured: Measured
    bars: Bars
    turnover: float


def midnight(day: date) -> datetime:
    return datetime.combine(day, time(0, 0), tzinfo=IST).astimezone(UTC)


class SnapshotService:
    def __init__(
        self,
        feed: MarketFeedClient,
        funds: FundamentalClient,
        sessions: async_sessionmaker[AsyncSession],
        publisher: EventPublisher,
        calendar: TradingCalendar,
        params: SwingParams,
        clock: Callable[[], datetime],
    ) -> None:
        self.feed = feed
        self.funds = funds
        self.sessions = sessions
        self.publisher = publisher
        self.calendar = calendar
        self.params = params
        self.clock = clock

    async def universe(self, today: date) -> UniverseSnapshot | None:
        """Today's universe, or the previous session's when today's is not published yet (08:00 is before 08:20)."""
        found = await self.funds.universe(today)
        if found is None:
            found = await self.funds.universe(self.calendar.previous_trading_day(today))
        return found

    async def _bars(self, isin: str, start: date, as_of: date, *, adjusted: bool) -> Bars:
        end = midnight(as_of + timedelta(days=1))
        candles = [c async for c in self.feed.candles(isin, "1d", midnight(start), end, adjusted=adjusted)]
        return Bars.from_candles(candles)

    async def _load(self, isin: str, as_of: date, gate: asyncio.Semaphore, summary: Summary) -> Loaded | None:
        p = self.params
        try:
            async with gate:
                adjusted = await self._bars(isin, as_of - timedelta(days=p.history_days), as_of, adjusted=True)
                raw = await self._bars(isin, as_of - timedelta(days=p.turnover_lookback_days), as_of, adjusted=False)
        except UpstreamUnavailable as exc:
            log.warning("snapshot_history_unavailable", isin=isin, error=str(exc)[:200])
            summary.failed.append(isin)
            return None
        if len(adjusted) == 0 or len(raw) == 0 or int(adjusted.session[-1]) != as_of.toordinal():
            summary.stale.append(isin)  # no candle for the session: suspended, or the feed is late. No snapshot.
            return None
        measured = analysis.measure(isin, adjusted, p)
        if measured is None:
            summary.too_short.append(isin)
            return None
        return Loaded(measured, adjusted, liquidity.turnover(raw.close, raw.volume, p.liquidity.turnover_days))

    async def run(self, kind: Kind, today: date) -> Summary:
        started = clock_time.monotonic()
        summary = Summary(kind)
        try:
            as_of = await self._session_of(kind, today)
        except Exception as exc:  # the kit's holiday file is empty until the owner fills it
            log.warning("calendar_unavailable", error=type(exc).__name__)
            summary.skipped = "calendar_unavailable"
            return summary
        if as_of is None:
            summary.skipped = "not_a_trading_day"
            return summary
        summary.as_of = as_of
        universe = await self.universe(today)
        if universe is None:
            summary.skipped = "no_universe"
            log.warning("snapshot_no_universe", today=today.isoformat())
            return summary
        summary.universe = len(universe.isins)

        gate = asyncio.Semaphore(self.params.fetch_concurrency)
        results = await asyncio.gather(*(self._load(i, as_of, gate, summary) for i in sorted(universe.isins)))
        loaded = [r for r in results if r is not None]
        if len(summary.failed) > self.params.max_failed_share * len(universe.isins):
            raise IncompleteRun(f"{len(summary.failed)} of {len(universe.isins)} stocks could not be loaded")

        ranks = self._rank(loaded)
        now = self.clock()
        snapshots = [
            analysis.snapshot(r.measured, ranks[r.measured.isin], r.turnover, z(now))
            for r in loaded
            if r.measured.isin in ranks
        ]
        setups = self._setups(loaded, as_of, now) if kind == "close" else []

        async with self.sessions() as session, session.begin():
            for snap in snapshots:
                if await snapshot_store.upsert(session, snap, now):
                    summary.snapshots_new += 1
                    await self._announce(
                        session, SNAPSHOT_EVENT, SNAPSHOT_SCHEMA, snap.isin, snap.model_dump(mode="json"), now
                    )
                else:
                    summary.snapshots_unchanged += 1
            for setup in setups:
                if await setup_store.save(session, setup):
                    summary.setups_new += 1
                    await self._announce(
                        session, SETUP_EVENT, SETUP_SCHEMA, setup.isin, setup_store.to_dict(setup), now
                    )
        await outbox.flush_after_commit(self.sessions, self.publisher)
        summary.seconds = round(clock_time.monotonic() - started, 1)
        log.info(
            "swing_run_done",
            kind=kind,
            as_of=as_of.isoformat(),
            universe=summary.universe,
            snapshots_new=summary.snapshots_new,
            snapshots_unchanged=summary.snapshots_unchanged,
            setups_new=summary.setups_new,
            too_short=len(summary.too_short),
            stale=len(summary.stale),
            failed=len(summary.failed),
            seconds=summary.seconds,
        )
        return summary

    async def _session_of(self, kind: Kind, today: date) -> date | None:
        """The session the run is as of: today's for the close run, the previous one for the morning refresh."""
        if not self.calendar.is_trading_day(today):
            return None
        return today if kind == "close" else self.calendar.previous_trading_day(today)

    def _rank(self, loaded: list[Loaded]) -> dict[str, int]:
        """Relative strength percentile. The plan ranks the 20- and 60-day return against Nifty and the sector index;
        every stock is measured against the same Nifty, so subtracting it cannot change the order and the rank of the
        plain returns is the Nifty-relative rank. The sector index is not available yet (no index feed) and is left
        out; when an index source exists the rank can subtract it per stock."""
        r20 = rank.percentile_ranks({r.measured.isin: r.measured.return_20d for r in loaded})
        r60 = rank.percentile_ranks({r.measured.isin: r.measured.return_60d for r in loaded})
        w = self.params.relative_strength
        return rank.blend(r20, r60, w.weight_20d, w.weight_60d)

    def _setups(self, loaded: list[Loaded], as_of: date, now: datetime) -> list[Setup]:
        try:
            valid_until = as_of
            for _ in range(self.params.setups.valid_days):
                valid_until = self.calendar.next_trading_day(valid_until)
        except Exception as exc:  # holiday file does not cover the date
            log.warning("setup_validity_unknown", error=type(exc).__name__)
            return []
        found_at = datetime.combine(as_of, CLOSE, tzinfo=IST).astimezone(UTC)
        out: list[Setup] = []
        for r in loaded:
            draft = analysis.find_setup(r.measured, r.bars, self.params)
            if draft is None:
                continue
            out.append(
                Setup(
                    setup_id=setup_id(r.measured.isin, draft.setup_type, as_of, found_at),
                    isin=r.measured.isin,
                    setup_date=as_of,
                    setup_type=draft.setup_type,
                    side="buy",
                    entry=draft.entry,
                    stop=draft.stop,
                    target_1=draft.target_1,
                    target_2=draft.target_2,
                    valid_until_date=valid_until,
                    params_version=self.params.params_version,
                    created_at=now,
                )
            )
        return out

    async def _announce(
        self, session: AsyncSession, event_type: str, schema: str, subject: str, data: dict[str, object], now: datetime
    ) -> None:
        await outbox.add(
            session,
            self.publisher.build(event_type, subject=subject, data=data, dataschema=schema, time=now),
        )
