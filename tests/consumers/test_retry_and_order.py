"""The consumers' retry rule (kit 2.5) and the order guards it needs (remove-lab-mode row D2-5k2).

Kit 1.0 kept an entry pending for as long as the infrastructure failed and counted any `OSError` as such a failure.
Kit 2.5 dead-letters an hour-old entry and counts fewer errors as an outage, unless the consumer says otherwise
(`transient_max_age=None`, `transient=is_outage`), and it keeps no stream order (a newer event can be handled before
an older retried one), so each handler guards its own state. Real Redis; no network beyond it.
"""

from __future__ import annotations

import ast
import errno
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from tbo_platform_kit.events import EventConsumer
from tbo_platform_kit.http_client import UpstreamRefused

from tbo_technical_engine.consumers import handlers
from tbo_technical_engine.consumers.handlers import (
    CALENDAR_STREAM,
    FEED_STREAM,
    calendar_consumer,
    candle_consumer,
    feed_consumer,
    index_consumer,
    universe_consumer,
)
from tbo_technical_engine.consumers.retry import is_outage
from tbo_technical_engine.schemas.candle import FeedStatus, UniverseSnapshot
from tbo_technical_engine.services.index_feed import FeedIndexSource
from tbo_technical_engine.services.readiness import Readiness
from tests.consumers.test_handlers import (
    PROFILE,
    START,
    parts,  # noqa: F401  (the fixture)
    publisher,
    send_candle,
    send_index,
    send_universe,
)
from tests.support import ISIN_A, ISIN_B, FakeFeed, candle, history

SRC = Path(__file__).resolve().parents[2] / "src"
TWO_HOURS = 7200
T1 = "2026-10-15T04:00:00Z"
T2 = "2026-10-15T04:05:00Z"

OUTAGES: list[BaseException] = [
    ConnectionRefusedError("refused"),
    TimeoutError("slow"),
    OSError(errno.EHOSTUNREACH, "No route to host"),
    OSError(errno.ENETUNREACH, "Network is unreachable"),
    OSError("Multiple exceptions: [Errno 111] Connect call failed ('127.0.0.1', 6379)"),
    UpstreamRefused("token_issuer", "401"),
]


@pytest.mark.parametrize("exc", OUTAGES, ids=lambda e: type(e).__name__ + str(getattr(e, "errno", "")))
def test_every_outage_kit_1_0_retried_is_still_an_outage(exc: BaseException) -> None:
    assert is_outage(exc)


@pytest.mark.parametrize("exc", [ValueError("bad event"), KeyError("isin"), RuntimeError("bug")])
def test_a_bug_or_a_bad_event_is_not_an_outage(exc: BaseException) -> None:
    assert not is_outage(exc)


def all_consumers(redis: Any, parts: dict[str, Any]) -> list[EventConsumer]:  # noqa: F811
    source = FeedIndexSource(FakeFeed().client())
    calendar: Any = object()
    return [
        candle_consumer(redis, PROFILE, parts["ingest"], "w1"),
        feed_consumer(redis, PROFILE, parts["readiness"], "w1"),
        universe_consumer(redis, PROFILE, parts["universe"], parts["buffer"], "w1"),
        calendar_consumer(redis, PROFILE, calendar, "w1"),
        index_consumer(redis, PROFILE, source, "w1"),
    ]


async def test_every_consumer_holds_an_outage_for_as_long_as_it_lasts(redis: Any, parts: dict[str, Any]) -> None:  # noqa: F811
    """Not kit 2.5's default (an hour, then a dead letter): kit 1.0's rule, by the owner's decision."""
    consumers = all_consumers(redis, parts)
    assert len(consumers) == 5
    for c in consumers:
        assert c.transient_max_age is None, c.group
        assert c.transient is is_outage, c.group


@pytest.mark.parametrize("exc", OUTAGES, ids=lambda e: type(e).__name__ + str(getattr(e, "errno", "")))
async def test_an_outage_failure_older_than_an_hour_stays_pending_and_is_never_dead_lettered(
    redis: Any,
    parts: dict[str, Any],  # noqa: F811
    exc: BaseException,
) -> None:
    c = feed_consumer(redis, PROFILE, parts["readiness"], "w1")
    c.claim_idle_ms, c.max_deliveries = 0, 2
    c.clock = lambda: time.time() + TWO_HOURS  # the entry is two hours old
    calls: list[int] = []

    async def failing(event: dict[str, Any], session: Any) -> None:
        calls.append(1)
        raise exc

    c.handler = failing
    await send_status_at(redis, "healthy", T1)
    for _ in range(4):  # twice the delivery limit
        await c.run_once(block_ms=10)
    assert len(calls) == 4
    assert (await redis.xpending(c.stream, c.group))["pending"] == 1
    assert await redis.xlen(c.dead_letter) == 0


async def test_a_bad_event_still_goes_to_the_dead_letters_after_the_delivery_limit(
    redis: Any,
    parts: dict[str, Any],  # noqa: F811
) -> None:
    c = feed_consumer(redis, PROFILE, parts["readiness"], "w1")
    c.claim_idle_ms, c.max_deliveries = 0, 2

    async def failing(event: dict[str, Any], session: Any) -> None:
        raise ValueError("bad event")

    c.handler = failing
    await send_status_at(redis, "healthy", T1)
    for _ in range(3):
        await c.run_once(block_ms=10)
    assert await redis.xlen(c.dead_letter) == 1


def test_only_the_handlers_module_builds_a_consumer() -> None:
    """Every consumer is built in `consumers/handlers.py`, so none can miss the rule above (an AST check: a docstring
    or an import for an annotation does not count, a call does)."""
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        if path.name == "handlers.py" and path.parent.name == "consumers":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", getattr(node.func, "attr", ""))
                if name in {"EventConsumer", "RecreatingConsumer"}:
                    offenders.append(f"{path.relative_to(SRC)}:{node.lineno}")
    assert offenders == []
    assert handlers.RecreatingConsumer.__mro__[1] is EventConsumer


async def send_status_at(redis: Any, state: str, changed_at: str) -> None:
    pub = publisher(redis, "tbo_market_feed_service")
    data = {
        "state": state,
        "subscribed_count": 100,
        "last_tick_at": None,
        "reason": None if state == "healthy" else "test",
        "changed_at": changed_at,
    }
    event = pub.build(
        "tbo.market_feed.feed.status.v1", subject="market", data=data, dataschema="market_feed/feed_status.v1.json"
    )
    await pub.publish(FEED_STREAM, event)


# Order: kit 2.5 keeps none, so a newer state must survive an older event handled after it.


async def test_an_older_healthy_status_handled_late_does_not_make_a_stale_feed_healthy(
    redis: Any,
    parts: dict[str, Any],  # noqa: F811
) -> None:
    consumer = feed_consumer(redis, PROFILE, parts["readiness"], "w1")
    await send_status_at(redis, "stale", T2)
    await send_status_at(redis, "healthy", T1)  # older, handled after
    await consumer.run_once(block_ms=10)
    assert parts["readiness"].feed_state == "stale"
    assert not parts["readiness"].feed_healthy


async def test_an_older_unhealthy_status_handled_late_does_not_overwrite_a_healthy_feed_but_still_asks_for_a_warmup(
    redis: Any,
    parts: dict[str, Any],  # noqa: F811
) -> None:
    """In order the two events end as feed healthy with a warm-up due; out of order they end the same way."""
    readiness: Readiness = parts["readiness"]
    consumer = feed_consumer(redis, PROFILE, readiness, "w1")
    await send_status_at(redis, "healthy", T2)
    await consumer.run_once(block_ms=10)
    readiness.mark_warm(readiness.generation)
    assert readiness.active
    await send_status_at(redis, "down", T1)
    await consumer.run_once(block_ms=10)
    assert readiness.feed_state == "healthy"
    assert readiness.needs_warmup and not readiness.active  # a hole may have opened at T1: warm up again


def test_a_status_with_the_same_time_is_applied_again() -> None:
    readiness = Readiness()
    at = datetime(2026, 10, 15, 4, 0, tzinfo=UTC)
    readiness.on_feed_status(FeedStatus(state="healthy", changed_at=at))
    readiness.on_feed_status(FeedStatus(state="healthy", changed_at=at))  # a redelivery
    assert readiness.feed_healthy
    readiness.on_feed_status(FeedStatus(state="stale", changed_at=at))  # cannot be ordered: the last one wins
    assert readiness.feed_state == "stale"


async def test_an_older_universe_handled_late_changes_nothing(redis: Any, parts: dict[str, Any]) -> None:  # noqa: F811
    consumer = universe_consumer(redis, PROFILE, parts["universe"], parts["buffer"], "w1")
    await send_universe(redis, 2, ISIN_A)
    await send_universe(redis, 1, ISIN_A, ISIN_B)  # older, handled after
    await send_universe(redis, 2, ISIN_B)  # the same version again: the first one stays
    await consumer.run_once(block_ms=10)
    snapshot: UniverseSnapshot | None = parts["universe"].snapshot
    assert snapshot is not None and snapshot.universe_version == 2
    assert parts["universe"].isins == {ISIN_A}


async def test_an_older_index_revision_handled_late_changes_nothing(redis: Any) -> None:
    source = FeedIndexSource(FakeFeed().client())
    consumer = index_consumer(redis, PROFILE, source, "w1")
    await consumer.ensure_group()
    await send_index(redis, "24015.00", revision=2)
    await send_index(redis, "24010.00", revision=1)  # older, handled after
    await consumer.run_once(block_ms=10)
    held = source._sorted("nifty_50", "5m")
    assert [(c.close.to_eng_string(), c.revision) for c in held] == [("24015.00", 2)]


async def test_an_older_candle_revision_handled_late_is_neither_stored_nor_evaluated(
    redis: Any,
    parts: dict[str, Any],  # noqa: F811
) -> None:
    parts["universe"].offer(UniverseSnapshot(trade_date="2026-10-15", universe_version=1, isins=(ISIN_A,)))
    parts["buffer"].add_many(c for tf in history(ISIN_A, date(2026, 10, 15)).values() for c in tf)
    parts["readiness"].on_feed_status(FeedStatus(state="healthy", changed_at=START))
    parts["readiness"].mark_warm(parts["readiness"].generation)
    consumer = candle_consumer(redis, PROFILE, parts["ingest"], "w1")
    await consumer.ensure_group()
    await send_candle(redis, candle(ISIN_A, "5m", START, "101.00", revision=2))
    await send_candle(redis, candle(ISIN_A, "5m", START, "100.00", revision=1))  # older, handled after
    await consumer.run_once(block_ms=10)
    held = [c for c in parts["buffer"].series(ISIN_A, "5m") if c.start_at == START]
    assert [(str(c.close), c.revision) for c in held] == [("101.00", 2)]
    assert len(parts["seen"]) == 1


async def test_the_calendar_handler_keeps_no_state_so_order_cannot_matter(redis: Any) -> None:
    """`market_calendar.updated` only triggers a rebuild from the filings extractor; two events in any order give two
    rebuilds and the same calendar."""
    refreshed: list[int] = []

    class Calendar:
        async def refresh(self) -> None:
            refreshed.append(1)

    calendar: Any = Calendar()
    consumer = calendar_consumer(redis, PROFILE, calendar, "w1")
    await consumer.ensure_group()
    pub = publisher(redis, "tbo_filings_extractor_service")
    row = {
        "calendar_date": "2026-11-08",
        "exchange": "nse",
        "segment": "cash",
        "kind": "special_session",
        "status": "active",
        "name": "Diwali Laxmi Pujan (Muhurat)",
        "windows": [{"label": "normal", "open_time": "18:00", "close_time": "19:00"}],
        "source": "nse_circulars",
        "source_ref": "NSE/CMTR/00000",
    }
    for stamp in ("2026-10-15T04:05:00Z", "2026-10-15T04:00:00Z"):  # newer first, then older
        event = pub.build(
            "tbo.filings.market_calendar.updated.v1",
            subject="market",
            data={**row, "fetched_at": stamp, "changed_at": stamp},
            dataschema="filings/market_calendar_day.v1.json",
        )
        await pub.publish(CALENDAR_STREAM, event)
    await consumer.run_once(block_ms=10)
    assert refreshed == [1, 1]
