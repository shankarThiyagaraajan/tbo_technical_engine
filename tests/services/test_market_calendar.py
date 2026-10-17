"""The calendar from the filings extractor: a holiday, a Muhurat evening, a Saturday session, the API down with and
without a cache, and the unset URL. The API is a `MockTransport`; the Redis stream is real (the consumer)."""

from __future__ import annotations

import asyncio
import json
import stat
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from tbo_platform_kit.calendar import CalendarUnknown
from tbo_platform_kit.events import EventPublisher
from tbo_platform_kit.http_client import StaticTokenProvider, SyncServiceClient
from tbo_platform_kit.mode import Mode, profile_for

from tbo_technical_engine.config.settings import Settings
from tbo_technical_engine.consumers.handlers import CALENDAR_STREAM, calendar_consumer
from tbo_technical_engine.services.market_calendar import AUDIENCE, SCOPES, LiveCalendar, build_calendar
from tests.support import sample, spy_signals

NOW = datetime(2026, 10, 17, 3, 0, tzinfo=UTC)
STAMP = "2026-10-17T03:00:00Z"
PROFILE = profile_for(Mode.LAB)
GANDHI = date(2026, 10, 2)  # a Friday, closed
MUHURAT = date(2026, 11, 8)  # a Sunday evening session
SATURDAY = date(2026, 3, 14)  # a Saturday that traded


def row(day: date, kind: str, name: str, windows: list[tuple[str, str, str]] | None = None) -> dict[str, Any]:
    return {
        "calendar_date": day.isoformat(),
        "exchange": "nse",
        "segment": "cash",
        "kind": kind,
        "status": "active",
        "name": name,
        "windows": [{"label": a, "open_time": b, "close_time": c} for a, b, c in windows or []],
    }


ROWS = [
    row(GANDHI, "holiday", "Gandhi Jayanti"),
    row(MUHURAT, "special_session", "Diwali Laxmi Pujan (Muhurat)", [("normal", "18:00", "19:00")]),
    row(SATURDAY, "special_session", "Special live session", [("normal", "09:15", "15:30")]),
]


def api(state: dict[str, Any]) -> httpx.MockTransport:
    """/v1/market-calendar for one year at a time; `state['down']` makes it unreachable, `state['rows']` is its data."""

    def handle(request: httpx.Request) -> httpx.Response:
        if state.get("down"):
            raise httpx.ConnectError("api down")
        year = int(request.url.params["from_date"][:4])
        items = [r for r in state["rows"] if r["calendar_date"].startswith(str(year))]
        return httpx.Response(200, json={"items": items, "next_cursor": None})

    return httpx.MockTransport(handle)


def client(state: dict[str, Any]) -> SyncServiceClient:
    return SyncServiceClient(
        "http://filings",
        audience=AUDIENCE,
        scopes=SCOPES,
        tokens=StaticTokenProvider({AUDIENCE: "t"}),
        transport=api(state),
        max_retries=0,
    )


def live(state: dict[str, Any], tmp_path: Path) -> LiveCalendar:
    return LiveCalendar(client(state), tmp_path / "calendar.json", clock=lambda: NOW)


async def test_a_holiday_a_muhurat_evening_and_a_saturday_session(tmp_path: Path) -> None:
    calendar = live({"rows": ROWS}, tmp_path)
    assert await calendar.refresh()
    assert calendar.source == "service"
    assert not calendar.is_trading_day(GANDHI) and calendar.holiday_name(GANDHI) == "Gandhi Jayanti"
    assert calendar.is_trading_day(MUHURAT)
    evening = calendar.session(MUHURAT)
    assert evening is not None and evening.special == "Diwali Laxmi Pujan (Muhurat)"
    assert calendar.phase(datetime(2026, 11, 8, 13, 0, tzinfo=UTC)) == "normal"  # 18:30 IST
    assert calendar.phase(datetime(2026, 11, 8, 4, 0, tzinfo=UTC)) == "closed"  # 09:30 IST: no morning session
    assert calendar.is_trading_day(SATURDAY)
    assert calendar.phase(datetime(2026, 3, 14, 5, 0, tzinfo=UTC)) == "normal"  # 10:30 IST on a Saturday
    assert calendar.is_trading_day(date(2026, 10, 15)) and not calendar.is_trading_day(date(2026, 10, 17))


async def test_the_api_down_uses_the_cache_and_without_one_the_calendar_is_unknown(tmp_path: Path) -> None:
    state: dict[str, Any] = {"rows": ROWS}
    first = live(state, tmp_path)
    await first.refresh()
    path = tmp_path / "calendar.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    state["down"] = True
    restarted = live(state, tmp_path)
    assert await restarted.refresh()
    assert restarted.source == "cache" and not restarted.is_trading_day(GANDHI)
    path.unlink()
    nothing = live(state, tmp_path)
    assert not await nothing.refresh()
    assert nothing.source == "none"
    with pytest.raises(CalendarUnknown):
        nothing.is_trading_day(GANDHI)


async def test_a_failed_refresh_never_replaces_a_working_calendar_by_an_empty_one(tmp_path: Path) -> None:
    state: dict[str, Any] = {"rows": ROWS}
    calendar = live(state, tmp_path)
    await calendar.refresh()
    (tmp_path / "calendar.json").unlink()
    state["down"] = True
    assert not await calendar.refresh()
    assert calendar.source == "service" and not calendar.is_trading_day(GANDHI)


async def test_a_refresh_replaces_the_data_in_place_for_every_holder(tmp_path: Path) -> None:
    state: dict[str, Any] = {"rows": []}
    calendar = live(state, tmp_path)
    await calendar.refresh()
    holder = calendar  # the supervisor and the services hold this same object
    assert holder.is_trading_day(GANDHI)
    state["rows"] = ROWS
    await calendar.refresh()
    assert not holder.is_trading_day(GANDHI)


async def test_an_unset_url_keeps_the_empty_calendar_and_says_so_once(tmp_path: Path) -> None:
    signals, _ = spy_signals()
    calendar = build_calendar(Settings(), signals=signals)
    assert not calendar.configured and not await calendar.refresh()
    stop = asyncio.Event()
    await asyncio.wait_for(calendar.run(stop), 1)  # returns at once: nothing to refresh
    assert sample(signals, "tbo_technical_worker_last_error_seconds", worker="calendar") is not None
    assert sample(signals, "tbo_technical_worker_last_handled_seconds", worker="calendar") is None
    with pytest.raises(CalendarUnknown):
        calendar.is_trading_day(GANDHI)


def test_a_set_url_wires_the_client_and_the_cache_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TBO_TECHNICAL_FILINGS_URL", "http://filings:8000")
    monkeypatch.setenv("TBO_TECHNICAL_CALENDAR_CACHE_FILE", str(tmp_path / "c.json"))
    calendar = build_calendar(Settings(), tokens=StaticTokenProvider({AUDIENCE: "t"}))
    assert calendar.configured and calendar.cache_path == tmp_path / "c.json"
    assert calendar.client is not None and calendar.client.audience == AUDIENCE
    assert calendar.client.scopes == SCOPES


async def test_the_updated_event_rebuilds_the_calendar(redis: Any, tmp_path: Path) -> None:
    state: dict[str, Any] = {"rows": []}
    calendar = live(state, tmp_path)
    await calendar.refresh()
    consumer = calendar_consumer(redis, PROFILE, calendar, "w1")
    await consumer.ensure_group()
    state["rows"] = ROWS
    pub = EventPublisher(redis, PROFILE, source="tbo_filings_extractor_service")
    event = pub.build(
        "tbo.filings.market_calendar.updated.v1",
        subject="market",
        data={**ROWS[0], "source": "nse_api", "source_ref": "x", "fetched_at": STAMP, "changed_at": STAMP},
        dataschema="filings/market_calendar_day.v1.json",
    )
    await pub.publish(CALENDAR_STREAM, event)
    await consumer.run_once(block_ms=10)
    assert not calendar.is_trading_day(GANDHI)
    assert json.loads((tmp_path / "calendar.json").read_text())["years"]
