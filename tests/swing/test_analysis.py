"""measure -> snapshot: the record is the contract's, the numbers are the hand-checked ones, and 500 stocks are fast."""

from __future__ import annotations

import time

import pytest
import yaml
from pydantic import ValidationError
from tbo_contracts import validate

from tbo_technical_engine.schemas.swing import SwingParams
from tbo_technical_engine.services.swing_loader import load_swing
from tbo_technical_engine.swing import analysis, rank
from tests.swing.data import DAY, PARAMS, ROOT, bars_from, mean_reverting, wave

ISIN = "INE002A01018"


def test_decimal_rounds_half_up_the_way_a_person_would() -> None:
    assert analysis.decimal(6.405) == "6.41"
    assert analysis.decimal(-6.405) == "-6.41"
    assert analysis.decimal(0.0) == "0.00"
    assert analysis.decimal(0.0304, 3) == "0.030"
    assert analysis.decimal(1e9) == "1000000000.00"


def test_the_snapshot_is_a_valid_contract_record() -> None:
    measured = analysis.measure(ISIN, bars_from(wave(300, 100.0, 0.5)), PARAMS)
    assert measured is not None
    snapshot = analysis.snapshot(measured, 84, 8.42e9, "2026-10-15T10:30:00Z")
    validate("technical/technical_snapshot.v1.json", snapshot.model_dump(mode="json"))
    assert snapshot.as_of_date == DAY.isoformat() and snapshot.trend_state == "up" and snapshot.rs_percentile == 84


def test_returns_are_close_over_close_n_sessions_back() -> None:
    close = [100.0] * 300
    close[-21], close[-61], close[-1] = 80.0, 50.0, 100.0
    measured = analysis.measure(ISIN, bars_from(close), PARAMS)
    assert measured is not None
    assert round(measured.return_20d, 6) == 25.0  # 100 / 80 - 1
    assert round(measured.return_60d, 6) == 100.0  # 100 / 50 - 1


def test_a_stock_with_too_few_sessions_has_no_snapshot() -> None:
    assert analysis.measure(ISIN, bars_from(wave(PARAMS.min_bars - 1, 100.0, 0.5)), PARAMS) is None
    assert analysis.measure(ISIN, bars_from(wave(PARAMS.min_bars, 100.0, 0.5)), PARAMS) is not None


def test_the_same_bars_give_the_same_snapshot() -> None:
    bars = bars_from(mean_reverting(300, seed=9))
    first, second = analysis.measure(ISIN, bars, PARAMS), analysis.measure(ISIN, bars, PARAMS)
    assert first is not None and second is not None
    one = analysis.snapshot(first, 50, 1e8, "2026-10-15T10:30:00Z")
    two = analysis.snapshot(second, 50, 1e8, "2026-10-15T10:30:00Z")
    assert one == two


def test_500_stocks_are_measured_and_ranked_in_seconds() -> None:
    """Exit criterion: snapshots for 500 stocks by 16:30. Computing is a few seconds; the rest of the half hour is the
    fetch, which the service test bounds by concurrency."""
    started = time.monotonic()
    measured = [
        m
        for i in range(500)
        if (m := analysis.measure(f"INE{i:09d}", bars_from(mean_reverting(300, seed=i, centre=50.0 + i)), PARAMS))
    ]
    ranks = rank.blend(
        rank.percentile_ranks({m.isin: m.return_20d for m in measured}),
        rank.percentile_ranks({m.isin: m.return_60d for m in measured}),
        0.5,
        0.5,
    )
    elapsed = time.monotonic() - started
    assert len(measured) == 500 and len(ranks) == 500
    assert elapsed < 60, f"500 stocks took {elapsed:.1f}s"


def test_the_shipped_config_loads_and_a_typo_stops_the_load() -> None:
    assert load_swing(ROOT / "config" / "swing.yaml").params_version
    document = yaml.safe_load((ROOT / "config" / "swing.yaml").read_text())
    document["trend"]["fats"] = 20  # a misspelled key
    with pytest.raises(ValidationError):
        SwingParams.model_validate(document)


def test_a_pullback_ema_that_the_trend_does_not_compute_is_refused() -> None:
    document = PARAMS.model_dump()
    document["setups"]["ema_pullback"]["ema"] = [20, 100]
    with pytest.raises(ValidationError, match="not trend.fast or trend.mid"):
        SwingParams.model_validate(document)


def test_the_image_carries_the_config_directory() -> None:
    """Lesson 12: swing.yaml must be in the image or the daily job starts without its rules."""
    assert "COPY config config" in (ROOT / "Dockerfile").read_text()
