"""The operating tools shipped in the image (review 9.21): the session report and the research cache fill, run as
`python -m tbo_technical_engine.apps.<tool>`, wired here to fakes (no database, feed or token issuer)."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from tbo_technical_engine.apps import fill_research_cache, session_report
from tbo_technical_engine.schemas.candle import UniverseSnapshot
from tbo_technical_engine.schemas.latency import SessionReport
from tbo_technical_engine.services.cache_fill import FillResult
from tests.support import CALENDAR


class Calendar:
    """The kit calendar with the `refresh` the tool calls."""

    def __init__(self) -> None:
        self.refreshed = False

    def __getattr__(self, name: str) -> Any:
        return getattr(CALENDAR, name)

    async def refresh(self) -> bool:
        self.refreshed = True
        return True


def clean(day: date) -> SessionReport:
    return SessionReport(
        trade_date=day, boundaries_expected=75, boundaries_recorded=75, boundaries_missing=0, suspended=0,
        missed_evaluations=0, slow_boundaries=0, worst_p95_seconds=0.4, clean=True,
    )  # fmt: skip


@pytest.mark.parametrize(("days", "code"), [(10, 0), (3, 1)])
def test_the_session_report_exits_0_only_with_ten_clean_sessions(
    days: int, code: int, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TBO_TECHNICAL_DATABASE_URL", "postgresql+asyncpg://u@db.invalid/x")
    calendar = Calendar()
    seen: list[list[date]] = []

    async def build(_settings: Any, wanted: list[date], _calendar: Any) -> list[SessionReport]:
        seen.append(wanted)
        return [clean(d) for d in wanted]

    monkeypatch.setattr(session_report, "build_calendar", lambda _s: calendar)
    monkeypatch.setattr(session_report, "build", build)
    assert session_report.main(["--days", str(days), "--date", "2026-10-16"]) == code
    assert calendar.refreshed
    assert len(seen[0]) == days
    assert seen[0][-1] == date(2026, 10, 16)
    assert date(2026, 10, 2) not in seen[0]  # a holiday
    assert f"consecutive clean sessions: {days} of 10" in capsys.readouterr().out


class Http:
    closed = 0

    def __init__(self, *_: Any, **__: Any) -> None:
        pass

    async def aclose(self) -> None:
        Http.closed += 1


class Funds:
    def __init__(self, _http: Any) -> None:
        pass

    async def universe(self, day: date) -> UniverseSnapshot | None:
        return UniverseSnapshot(trade_date=day.isoformat(), universe_version=1, isins=("INE2", "INE1"))


def test_the_fill_takes_the_universe_and_the_indices_and_reports_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TBO_TECHNICAL_MARKET_FEED_URL", "http://feed.invalid")
    monkeypatch.setenv("TBO_TECHNICAL_FUNDAMENTAL_URL", "http://fundamental.invalid")
    monkeypatch.setenv("TBO_TECHNICAL_RESEARCH_CACHE_DIR", str(tmp_path))
    calls: dict[str, Any] = {}

    async def fill(_feed: Any, _cache: Any, isins: list[str], *args: Any) -> FillResult:
        calls["isins"], calls["timeframes"] = isins, args[-1]
        return FillResult(candles=10, failed=["INE2"])

    async def fill_indices(_feed: Any, _cache: Any, codes: list[str], *_: Any) -> FillResult:
        calls["indices"] = codes
        return FillResult(candles=5)

    monkeypatch.setattr("tbo_platform_kit.http_client.IssuerTokenProvider.from_settings", lambda _s: None)
    monkeypatch.setattr(fill_research_cache, "ServiceClient", Http)
    monkeypatch.setattr("tbo_technical_engine.clients.fundamental.FundamentalClient", Funds)
    monkeypatch.setattr(fill_research_cache, "fill_cache", fill)
    monkeypatch.setattr(fill_research_cache, "fill_index_cache", fill_indices)
    monkeypatch.setattr(Http, "closed", 0)
    code = fill_research_cache.main(["--from", "2026-09-01", "--to", "2026-09-30", "--indices", "nifty_50"])
    assert code == 1  # a stock failed
    assert calls == {"isins": ["INE1", "INE2"], "timeframes": ["5m", "15m", "1d"], "indices": ["nifty_50"]}
    out = capsys.readouterr()
    assert "2 stocks, 15 candles, 1 failed" in out.out
    assert "failed: INE2" in out.err
    assert Http.closed == 2


def test_the_fill_with_no_stocks_fills_only_the_indices(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TBO_TECHNICAL_MARKET_FEED_URL", "http://feed.invalid")
    monkeypatch.setenv("TBO_TECHNICAL_RESEARCH_CACHE_DIR", str(tmp_path))

    async def never(*_: Any) -> FillResult:
        raise AssertionError("no stock fill expected")

    async def fill_indices(*_: Any) -> FillResult:
        return FillResult(candles=5)

    monkeypatch.setattr("tbo_platform_kit.http_client.IssuerTokenProvider.from_settings", lambda _s: None)
    monkeypatch.setattr(fill_research_cache, "ServiceClient", Http)
    monkeypatch.setattr(fill_research_cache, "fill_cache", never)
    monkeypatch.setattr(fill_research_cache, "fill_index_cache", fill_indices)
    args = ["--from", "2026-09-01", "--to", "2026-09-30", "--indices", "nifty_50", "--isins", "none"]
    assert fill_research_cache.main(args) == 0
