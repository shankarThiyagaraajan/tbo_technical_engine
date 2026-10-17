"""The 30-case review sample: ten of each setup type, every one found, and every number re-derived by plain loops."""

from __future__ import annotations

import pytest

from tests.swing.review_sample import _plain_ema, build_sample, check, library_ema

SAMPLE = build_sample()


def test_the_sample_has_thirty_cases_ten_of_each_type() -> None:
    assert len(SAMPLE) == 30
    for kind in ("base_breakout", "ema_pullback", "week_52_high_breakout"):
        assert sum(c.expected == kind for c in SAMPLE) == 10


@pytest.mark.parametrize("case", SAMPLE, ids=lambda c: f"{c.number:02d}-{c.expected}")
def test_each_case_checks_out(case: object) -> None:
    assert check(case) == []  # type: ignore[arg-type]


def test_the_plain_loop_ema_equals_the_library_one() -> None:
    case = next(c for c in SAMPLE if c.expected == "ema_pullback")
    plain = _plain_ema([float(x) for x in case.bars.close], 20)
    assert abs(plain - library_ema(case.bars, 20)) < 1e-9
