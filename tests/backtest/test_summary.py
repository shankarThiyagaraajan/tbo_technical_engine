"""The report text: the caveats are there, and out-of-sample is a row of its own."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

from tbo_technical_engine.backtest.study import run_study
from tbo_technical_engine.backtest.summary import render
from tests.backtest.builders import COSTS, ROOT, SETTINGS
from tests.backtest.data import FIRST, LAST, filled_cache
from tests.strategies.session import CONFIG
from tests.support import ISIN_A


def study(tmp_path: Path):  # type: ignore[no-untyped-def]
    cache = filled_cache(tmp_path)
    return run_study(
        CONFIG, "vwap_pivot", [], None, [ISIN_A], cache.stock, {ISIN_A: cache.session_dates(ISIN_A, "5m")},
        FIRST, LAST, SETTINGS, COSTS, None, monotonic=lambda: 0.0, deadline=float("inf"),
    )  # fmt: skip


def test_the_report_leads_with_the_caveats_and_shows_out_of_sample_apart(tmp_path: Path) -> None:
    text = render({"vwap_pivot": study(tmp_path)}, start=FIRST, end=LAST, settings=SETTINGS, costs=COSTS, banner="TEST")
    head = text[: text.index("## Summary")]
    assert "> **TEST**" in head and "Costs are a placeholder" in head and "low sample" in head
    assert "1 of 1 stocks had candles" in head
    assert "vwap_pivot - **out of sample**" in text and "vwap_pivot - whole period" in text


def test_the_report_script_writes_a_file_from_the_cache(tmp_path: Path) -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        backtest_report = importlib.import_module("backtest_report")

        filled_cache(tmp_path / "cache")
        out = tmp_path / "report.md"
        code = backtest_report.main(
            ["--from", FIRST.isoformat(), "--to", LAST.isoformat(), "--cache", str(tmp_path / "cache"),
             "--strategies", "orb_15m", "--out", str(out)]
        )  # fmt: skip
    finally:
        sys.path.remove(str(ROOT / "scripts"))
    assert code == 0 and "## orb_15m" in out.read_text()
