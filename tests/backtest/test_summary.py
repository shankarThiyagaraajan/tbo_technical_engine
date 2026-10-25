"""The report text: the caveats are there, and out-of-sample is a row of its own."""

from __future__ import annotations

import dataclasses
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from tbo_technical_engine.apps import backtest_report
from tbo_technical_engine.backtest.engine import DayRules
from tbo_technical_engine.backtest.study import StudyData, run_study
from tbo_technical_engine.backtest.summary import render
from tbo_technical_engine.schemas.backtest import Fold
from tests.backtest.builders import COSTS, SETTINGS
from tests.backtest.data import FIRST, LAST, filled_cache
from tests.strategies.session import CONFIG
from tests.support import ISIN_A


def study(tmp_path: Path):  # type: ignore[no-untyped-def]
    cache = filled_cache(tmp_path)
    return run_study(
        CONFIG,
        "vwap_pivot",
        [],
        None,
        StudyData([ISIN_A], cache.stock, {ISIN_A: cache.session_dates(ISIN_A, "5m")}, FIRST, LAST),
        SETTINGS,
        COSTS,
        None,
        monotonic=lambda: 0.0,
        deadline=float("inf"),
    )


def test_the_report_leads_with_the_caveats_and_shows_out_of_sample_apart(tmp_path: Path) -> None:
    text = render({"vwap_pivot": study(tmp_path)}, start=FIRST, end=LAST, settings=SETTINGS, costs=COSTS, banner="TEST")
    head = text[: text.index("## Summary")]
    assert "> **TEST**" in head and "Costs are a placeholder" in head and "low sample" in head
    assert "1 of 1 stocks had candles" in head
    assert "vwap_pivot - **out of sample**" in text and "vwap_pivot - whole period" in text


def test_the_report_script_writes_a_file_from_the_cache(tmp_path: Path) -> None:
    filled_cache(tmp_path / "cache")
    out = tmp_path / "report.md"
    code = backtest_report.main(
        ["--from", FIRST.isoformat(), "--to", LAST.isoformat(), "--cache", str(tmp_path / "cache"),
         "--strategies", "orb_15m", "--out", str(out)]
    )  # fmt: skip
    assert code == 0 and "## orb_15m" in out.read_text()
    assert "NOT GATED" in out.read_text()  # 9.6: no fundamental URL, so the report says it is not the go-live evidence


def _script(tmp_path: Path) -> Any:
    filled_cache(tmp_path / "cache")
    return backtest_report


def _args(tmp_path: Path, *extra: str) -> list[str]:
    return ["--from", FIRST.isoformat(), "--to", LAST.isoformat(), "--cache", str(tmp_path / "cache"),
            "--strategies", "orb_15m", "--out", str(tmp_path / "r.md"), *extra]  # fmt: skip


def test_asking_for_the_gate_without_a_fundamental_url_is_an_error(tmp_path: Path) -> None:
    assert _script(tmp_path).main(_args(tmp_path, "--gate")) == 2


def test_with_the_gate_the_report_has_no_warning_and_only_gated_days_trade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """9.6: with the point-in-time gate applied (here: nothing allowed any day) there are no trades and no banner."""
    module = _script(tmp_path)
    monkeypatch.setenv("TBO_TECHNICAL_FUNDAMENTAL_URL", "http://fundamental.invalid")

    async def rules(*_: Any) -> DayRules:
        return DayRules({})

    monkeypatch.setattr(module, "gate_rules", rules)
    assert module.main(_args(tmp_path)) == 0
    text = (tmp_path / "r.md").read_text()
    assert "NOT GATED" not in text


def test_the_report_shows_each_fold_its_stability_and_the_chosen_values(tmp_path: Path) -> None:
    base = study(tmp_path)
    m = base.metrics
    folds = [
        Fold(FIRST, FIRST, LAST, LAST, {"a": "1", "b": "2"}, m, m, True),
        Fold(FIRST, FIRST, LAST, LAST, {}, m, m, None),
        Fold(FIRST, FIRST, LAST, LAST, {"a": "3"}, m, m, False),
    ]
    walked = dataclasses.replace(
        base.walk_forward, folds=folds, stability_pct=Decimal("66.7"), parameter_choices={"a": {"1": 1, "3": 1}}
    )
    text = render(
        {"x": dataclasses.replace(base, walk_forward=walked, grid_skipped="too slow")},
        start=FIRST,
        end=LAST,
        settings=SETTINGS,
        costs=COSTS,
    )
    assert "Grid skipped: too slow." in text
    assert "Parameter stability: 66.7% of folds" in text
    assert f"| {LAST} to {LAST} | a=1, b=2 |" in text
    assert "| fixed |" in text
    rows = [line for line in text.splitlines() if line.startswith(f"| {LAST} to {LAST} |")]
    assert [row.rsplit("|", 2)[-2].strip() for row in rows] == ["yes", "-", "no"]
    assert "Chosen values across folds: a: 1 x1, 3 x1" in text
