"""The primary index's VWAP side in a backtest (TE5 with `require_index`, ADR-019)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from tbo_technical_engine.backtest.index_side import PRIMARY_INDEX, index_sides
from tbo_technical_engine.backtest.study import run_study
from tbo_technical_engine.backtest.summary import render
from tbo_technical_engine.schemas.backtest import ParamOverride
from tests.backtest.builders import COSTS, SETTINGS
from tests.backtest.data import FIRST, HISTORY, LAST, filled_cache
from tests.strategies.session import CONFIG
from tests.support import ISIN_A, candle

OPEN = datetime(2026, 10, 15, 3, 45, tzinfo=UTC)  # 09:15 IST


def test_the_side_is_the_newest_closed_candles_against_the_time_weighted_vwap() -> None:
    # Typical prices 100, 103, 97 (candle() spans close +- 1): the VWAP is their running mean, 100, 101.5, 100.
    bars = [
        candle(PRIMARY_INDEX, "5m", OPEN + timedelta(minutes=5 * i), c, volume=0)
        for i, c in enumerate(["100.00", "103.00", "97.00"])
    ]
    side = index_sides(bars)
    assert side is not None
    assert side(OPEN) is None  # nothing has closed at 09:15
    assert side(OPEN + timedelta(minutes=5)) == -1  # 100 is not above 100
    assert side(OPEN + timedelta(minutes=10)) == 1  # 103 above 101.5
    assert side(OPEN + timedelta(minutes=12)) == 1  # still the 09:20 candle: a later one has not closed
    assert side(OPEN + timedelta(minutes=15)) == -1  # 97 below 100
    assert side(OPEN + timedelta(days=1)) is None  # yesterday's side never carries into a new session


def test_each_session_starts_its_own_vwap() -> None:
    day1 = [candle(PRIMARY_INDEX, "5m", OPEN, "200.00", volume=0)]
    day2 = [
        candle(PRIMARY_INDEX, "5m", OPEN + timedelta(days=1, minutes=5 * i), c, volume=0)
        for i, c in enumerate(["100.00", "101.00"])
    ]
    side = index_sides(day1 + day2)
    assert side is not None
    assert side(OPEN + timedelta(days=1, minutes=10)) == 1  # 101 above (100 + 101) / 2; day 1's 200 is not in it


def test_no_index_candles_means_no_index_data() -> None:
    assert index_sides([]) is None


def pivot(tmp_path: Path, *, require: bool, with_index: bool):  # type: ignore[no-untyped-def]
    cache = filled_cache(tmp_path)
    side = None
    if with_index:
        # The stock's own candles stand in for the index: a real side series, all the data the fixture has.
        cache.indices.write(PRIMARY_INDEX, "5m", [c.model_copy(update={"isin": PRIMARY_INDEX}) for c in HISTORY.five])
        side = index_sides(cache.indices.candles(PRIMARY_INDEX, "5m"))
    overrides = [ParamOverride(name="require_index", value="true" if require else "false")]
    return run_study(
        CONFIG, "vwap_pivot", overrides, None, [ISIN_A], cache.stock, {ISIN_A: cache.session_dates(ISIN_A, "5m")},
        FIRST, LAST, SETTINGS, COSTS, None, monotonic=lambda: 0.0, deadline=float("inf"), index_side=side,
    )  # fmt: skip


def test_require_index_without_index_candles_takes_no_trade_and_the_report_says_so(tmp_path: Path) -> None:
    study = pivot(tmp_path, require=True, with_index=False)
    assert study.counts["candidates"] == 0 and not study.index_filter
    text = render({"vwap_pivot": study}, start=FIRST, end=LAST, settings=SETTINGS, costs=COSTS)
    assert "**Index filter off**" in text[: text.index("## Summary")]


def test_with_index_candles_the_filter_runs_as_live(tmp_path: Path) -> None:
    free = pivot(tmp_path / "a", require=False, with_index=False)
    filtered = pivot(tmp_path / "b", require=True, with_index=True)
    assert filtered.index_filter
    assert 0 < filtered.counts["candidates"] <= free.counts["candidates"]
    text = render({"vwap_pivot": filtered}, start=FIRST, end=LAST, settings=SETTINGS, costs=COSTS)
    assert "Index filter on" in text and "Index filter off" not in text
