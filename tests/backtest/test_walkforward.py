"""Walk-forward folds, the choice on the train window, out-of-sample kept apart, and parameter stability."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from tbo_technical_engine.backtest.walkforward import combos, walk_forward, windows
from tbo_technical_engine.schemas.backtest import Trade, WalkForwardSettings
from tests.backtest.builders import SETTINGS, at

D = Decimal
SESSIONS = [date(2026, 1, 5) + timedelta(days=i) for i in range(40)]  # 40 "sessions" (weekends are irrelevant here)
WF = WalkForwardSettings(train_days=10, test_days=5, min_trades_train=2, stability_band=D("0.8"))
A: tuple[tuple[str, str], ...] = (("adx_min", "15"),)
B: tuple[tuple[str, str], ...] = (("adx_min", "20"),)
C: tuple[tuple[str, str], ...] = (("adx_min", "25"),)


def t(day: date, r: str, n: int = 0) -> Trade:
    return Trade(
        "INE002A01018",
        "buy",
        f"{day}-{n}",
        at(5, day),
        at(8, day),
        D(100),
        D(101),
        "stop",
        10,
        D(0),
        D(0),
        D(r),
        D(r),
        False,
    )


def every_day(r: str, days: list[date] = SESSIONS) -> list[Trade]:
    """Two trades on each of `days`, each with result `r`."""
    return [t(d, r, n) for d in days for n in (0, 1)]


def test_windows_move_by_the_test_length_and_never_overlap_in_test() -> None:
    w = windows(SESSIONS, WF)
    assert len(w) == 6  # (40 - 10) / 5 folds
    assert w[0][0][0] == SESSIONS[0] and w[0][1][0] == SESSIONS[10]
    tests = [d for _, test in w for d in test]
    assert len(tests) == len(set(tests))


def test_too_little_data_makes_no_folds() -> None:
    assert windows(SESSIONS[:12], WF) == []


def test_combinations_include_the_base_even_if_the_grid_lacks_it() -> None:
    assert combos({"adx_min": ["15", "25"]}, {"adx_min": "20"}) == [
        (("adx_min", "15"),),
        (("adx_min", "25"),),
        (("adx_min", "20"),),
    ]
    assert combos({}, {}) == [()]


def test_a_fold_picks_the_best_train_set_and_is_judged_on_the_test_window_alone() -> None:
    # A is good everywhere in time; B is bad everywhere; so every fold must choose A and report A's test result.
    results = {A: every_day("1"), B: every_day("-1"), C: every_day("0.5")}
    out = walk_forward(results, B, SESSIONS, WF)
    assert len(out.folds) == 6 and all(f.chosen == {"adx_min": "15"} for f in out.folds)
    assert out.parameter_choices == {"adx_min": {"15": 6}}
    assert out.out_of_sample.trade_count == 6 * 5 * 2  # 6 test windows x 5 days x 2 trades
    assert out.out_of_sample.expectancy_r == D("1.0000")
    assert out.in_sample.trade_count == 6 * 10 * 2


def test_out_of_sample_is_only_the_test_windows_even_when_the_train_windows_look_great() -> None:
    # A makes +1R on the first ten days and -1R afterwards; B makes nothing. Fold 0 trains on those ten days, picks A,
    # and A then loses in its test window. The pooled in-sample number looks good; the out-of-sample one must not.
    results = {A: [t(d, "1" if d in SESSIONS[:10] else "-1") for d in SESSIONS], B: every_day("0")}
    out = walk_forward(results, B, SESSIONS, WF)
    assert out.folds[0].chosen == {"adx_min": "15"}
    assert out.folds[0].train.expectancy_r > 0 > out.folds[0].test.expectancy_r
    assert out.in_sample.expectancy_r > 0 > out.out_of_sample.expectancy_r


def test_stability_is_the_share_of_folds_whose_neighbours_were_nearly_as_good() -> None:
    near = {A: every_day("1"), B: every_day("0.9"), C: every_day("0.95")}  # neighbours within 80% of the best
    assert walk_forward(near, B, SESSIONS, WF).stability_pct == D("100.00")
    spiky = {A: every_day("1"), B: every_day("0.1"), C: every_day("0.1")}  # one lucky set: not stable
    assert walk_forward(spiky, B, SESSIONS, WF).stability_pct == D("0.00")


def test_a_set_with_too_few_trades_cannot_be_chosen() -> None:
    results = {A: [t(SESSIONS[0], "5")], B: every_day("0.2")}  # A has one lucky trade
    out = walk_forward(results, B, SESSIONS, WF)
    assert all(f.chosen == {"adx_min": "20"} for f in out.folds)


def test_without_a_grid_there_is_no_stability_to_report() -> None:
    out = walk_forward({(): every_day("1")}, (), SESSIONS, WF)
    assert out.stability_pct is None and all(f.stable is None and f.chosen == {} for f in out.folds)
    assert out.out_of_sample.trade_count > 0


def test_the_shipped_settings_load() -> None:
    assert SETTINGS.walk_forward.train_days > SETTINGS.walk_forward.test_days > 0
