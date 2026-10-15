"""Each strategy's rule on hand-built situations: the exact levels it gives, each filter that blocks it, and the short
side as the mirror image of the long one."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

import pytest

from tbo_technical_engine.schemas.signal import Draft
from tbo_technical_engine.schemas.strategy import Params
from tbo_technical_engine.strategies import REGISTRY
from tbo_technical_engine.strategies.base import Context
from tests.strategies.builders import context, mirror, money, params, prices, reflect, row

FULL = {"pivot_p": 100.0, "pivot_r1": 102.0, "pivot_r2": 103.5, "pivot_r3": 105.0, "pivot_s1": 98.0, "pivot_s2": 96.5}


def run(name: str, ctx: Context, p: Params, side: str = "buy") -> Draft | None:
    return REGISTRY[name].evaluate(ctx, p, side)  # type: ignore[arg-type]


def both(name: str, ctx: Context, p: Params) -> tuple[Draft | None, Draft | None]:
    return run(name, ctx, p, "buy"), run(name, mirror(ctx), p, "sell")


def assert_twins(long: Draft | None, short: Draft | None) -> Draft:
    assert long is not None and short is not None
    assert prices(short) == reflect(long)
    assert abs(short.strength - long.strength) < Decimal("0.01")  # percent-of-price terms are not exactly symmetric
    assert [(c.name, c.is_met) for c in short.components] == [(c.name, c.is_met) for c in long.components]
    return long


# ---- 9/21 EMA crossover ------------------------------------------------------------------------------------------


def ema_ctx(**over: float) -> Context:
    now = {"ema_9": 100.9, "ema_21": 100.8, "vwap": 100.5, "adx_14": 25.0, "atr_14": 1.0}
    now.update(over)
    quiet = {"ema_9": 100.7, "ema_21": 100.75, "vwap": 100.4, "adx_14": 24.0, "atr_14": 1.0}
    rows = [row(i, 100.0, 100.5, 99.8, 100.2, **quiet) for i in range(5)]
    rows.append(row(5, 100.6, 101.6, 100.5, 101.5, **now))
    return context(rows)


def test_ema_crossover_gives_the_stop_and_targets_of_the_plan() -> None:
    long = assert_twins(*both("ema_9_21_crossover", ema_ctx(), params()))
    # swing low 99.8 is 1.7 away, one ATR is 1.0: the nearer stop is 100.50; targets 1.5R and 2.5R
    assert prices(long) == money(
        entry_low="101.50", entry_high="101.60", stop="100.50", target_1="103.00", target_2="104.00"
    )
    assert [c.name for c in long.components] == [
        "ema_9_crossed_21",
        "close_beyond_both_emas",
        "close_with_vwap",
        "adx_at_least_min",
    ]
    assert all(c.is_met for c in long.components)
    assert Decimal("0") < long.strength <= Decimal("1")


def test_ema_crossover_uses_the_swing_low_when_it_is_nearer_than_one_atr() -> None:
    ctx = ema_ctx(atr_14=3.0)  # one ATR is 3.0 away, the 5-bar swing low 1.7
    long = run("ema_9_21_crossover", ctx, params())
    assert long is not None and long.stop == Decimal("99.80")


@pytest.mark.parametrize(
    "over",
    [
        {"ema_9": 100.7},  # no cross: 9 stays under 21... (was under, still under)
        {"vwap": 101.9},  # close under VWAP
        {"adx_14": 19.9},  # no trend
        {"ema_9": 101.6},  # close not above the 9 EMA
        {"adx_14": float("nan")},  # still warming up
    ],
)
def test_ema_crossover_needs_every_condition(over: dict[str, float]) -> None:
    assert run("ema_9_21_crossover", ema_ctx(**over), params()) is None


def test_ema_crossover_is_silent_without_a_fresh_cross() -> None:
    rows = list(ema_ctx().rows)
    rows[-2] = row(4, 100.0, 100.5, 99.8, 100.2, ema_9=100.9, ema_21=100.8, vwap=100.4, adx_14=24.0, atr_14=1.0)
    assert run("ema_9_21_crossover", context(rows), params()) is None


# ---- VWAP breakout -----------------------------------------------------------------------------------------------


def vwap_ctx(*, ratio: float = 2.0, below: int = 3, close: float = 100.8, **over: float) -> Context:
    under = dict(vwap=100.0, atr_14=1.0, vol_ratio_20=1.0, **FULL)
    above = dict(vwap=100.0, atr_14=1.0, vol_ratio_20=ratio, **FULL)
    above.update(over)
    rows = [row(i, 100.3, 100.6, 100.2, 100.4, **under) for i in range(3)]  # closes above VWAP, before the dip
    rows += [row(3 + i, 99.4, 99.6, 98.9, 99.0, **under) for i in range(below)]
    rows.append(row(3 + below, 99.2, 100.9, 99.1, close, **above))
    return context(rows)


def vwap_params(**over: str | int | bool) -> Params:
    return params(
        bars_below=3,
        volume_ratio_min="1.5",
        stop_atr="0.5",
        target_2_r="2",
        target_1_min_r="0.5",
        target_2_min_gap_r="0.5",
        **over,
    )


def test_vwap_breakout_levels() -> None:
    long = assert_twins(*both("vwap_breakout", vwap_ctx(), vwap_params()))
    # stop: the candle low 99.10 or VWAP - 0.5 ATR = 99.50, nearer is 99.50; risk 1.30; T1 the next pivot (R1 102.00);
    # T2 = 2R = 103.40 (further than T1 + 0.5R = 102.65)
    assert prices(long) == money(
        entry_low="100.80", entry_high="100.90", stop="99.50", target_1="102.00", target_2="103.40"
    )


@pytest.mark.parametrize(
    "ctx",
    [
        vwap_ctx(ratio=1.4),  # quiet volume
        vwap_ctx(below=2),  # only 2 bars below (the 3rd back closed above)
        vwap_ctx(close=99.9),  # no breakout
        vwap_ctx(pivot_r1=100.9, pivot_r2=100.95, pivot_r3=100.99),  # no pivot above the entry
        vwap_ctx(pivot_r1=101.0, pivot_r2=103.0),  # next pivot closer than 0.5R (0.2 < 0.65)
    ],
)
def test_vwap_breakout_filters(ctx: Context) -> None:
    assert run("vwap_breakout", ctx, vwap_params()) is None


# ---- 15-minute opening range breakout ----------------------------------------------------------------------------


def orb_params(**over: str | int | bool) -> Params:
    return params(
        volume_ratio_min="1.5",
        max_range_daily_atr="1",
        narrow_range_pct="0.4",
        gap_skip_pct="2",
        target_1_range="1",
        target_2_range="2",
        **over,
    )


def orb_ctx(
    *,
    close: float = 100.4,
    ratio: float = 2.0,
    daily_atr: float = 3.0,
    open_: float = 99.9,
    prev_close: float = 99.5,
    trend: float | None = None,
    **over: float,
) -> Context:
    values = dict(or_high=100.0, or_low=98.0, or_mid=99.0, or_height=2.0, vol_ratio_20=ratio, atr_14=0.8)
    values.update(over)
    first = row(0, open_, 100.2, 99.0, 99.7)
    rows = [first, row(1, 99.7, 100.0, 99.5, 99.9, **values), row(2, 99.9, 100.6, 99.8, close, **values)]
    ctx = context(
        rows,
        daily={"atr_14": daily_atr},
        prev_close=prev_close,
        trend15=None if trend is None else {"supertrend_dir_10_3": trend},
    )
    return ctx


def test_orb_levels() -> None:
    long = assert_twins(*both("orb_15m", orb_ctx(), orb_params()))
    # range 98-100, midpoint stop 99.00, targets one and two range heights (2.0) from the 100.40 entry
    assert prices(long) == money(
        entry_low="100.40", entry_high="100.50", stop="99.00", target_1="102.40", target_2="104.40"
    )


def test_orb_uses_the_far_edge_of_a_narrow_range() -> None:
    ctx = orb_ctx(or_high=100.0, or_low=99.7, or_mid=99.85, or_height=0.3, close=100.3)
    long = run("orb_15m", ctx, orb_params())
    assert long is not None and long.stop == Decimal("99.70")


def test_orb_skips_a_big_gap_unless_the_trend_agrees() -> None:
    gap = {
        "open_": 103.0,
        "prev_close": 100.0,
        "close": 103.4,
        "or_high": 103.2,
        "or_low": 102.6,
        "or_mid": 102.9,
        "or_height": 0.6,
    }
    assert run("orb_15m", orb_ctx(**gap), orb_params()) is None
    assert run("orb_15m", orb_ctx(trend=-1.0, **gap), orb_params()) is None
    assert run("orb_15m", orb_ctx(trend=1.0, **gap), orb_params()) is not None


@pytest.mark.parametrize(
    "ctx",
    [
        orb_ctx(close=99.9),  # inside the range
        orb_ctx(ratio=1.2),  # quiet
        orb_ctx(daily_atr=1.5),  # range wider than 1 daily ATR... 2.0 > 1.5
        orb_ctx(
            or_high=float("nan"), or_low=float("nan"), or_mid=float("nan"), or_height=float("nan")
        ),  # range not complete
    ],
)
def test_orb_filters(ctx: Context) -> None:
    assert run("orb_15m", ctx, orb_params()) is None


def test_orb_does_not_guess_a_filter_it_cannot_check() -> None:
    ctx = orb_ctx()
    assert run("orb_15m", context(list(ctx.rows), daily=None, prev_close=99.5), orb_params()) is None
    assert run("orb_15m", context(list(ctx.rows), daily={"atr_14": 3.0}, prev_close=None), orb_params()) is None


# ---- Pivot + Supertrend ------------------------------------------------------------------------------------------


def pst_ctx(
    *, trend: float = 1.0, close: float = 100.6, st: float = 99.6, st_dir: float = 1.0, r1: float = 101.5
) -> Context:
    levels = {**FULL, "pivot_r1": r1}
    prev = row(0, 99.5, 100.0, 99.4, 99.8, supertrend_10_3=99.4, supertrend_dir_10_3=1.0, atr_14=0.8, **levels)
    now = row(1, 99.9, 100.7, 99.8, close, supertrend_10_3=st, supertrend_dir_10_3=st_dir, atr_14=0.8, **levels)
    return context([prev, now], trend15={"supertrend_dir_10_3": trend})


def test_pivot_supertrend_levels() -> None:
    long = assert_twins(*both("pivot_supertrend", pst_ctx(), params(min_room_pct="0.2")))
    # crossed P (100.00); stop = the 5-minute Supertrend 99.60; T1/T2 = the next two pivots R1 and R2
    assert prices(long) == money(
        entry_low="100.60", entry_high="100.70", stop="99.60", target_1="101.50", target_2="103.50"
    )


@pytest.mark.parametrize(
    "ctx",
    [
        pst_ctx(trend=-1.0),  # 15-minute trend against
        pst_ctx(st_dir=-1.0),  # 5-minute line is above the price: no valid stop
        pst_ctx(r1=100.7),  # within 0.2% of the next resistance (0.1%)
        pst_ctx(close=99.95),  # did not cross P
    ],
)
def test_pivot_supertrend_filters(ctx: Context) -> None:
    assert run("pivot_supertrend", ctx, params(min_room_pct="0.2")) is None


def test_pivot_supertrend_needs_the_15_minute_trend() -> None:
    ctx = pst_ctx()
    assert run("pivot_supertrend", context(list(ctx.rows), trend15=None), params(min_room_pct="0.2")) is None


# ---- VWAP + Pivot ------------------------------------------------------------------------------------------------


def vp_params(**over: str | int | bool) -> Params:
    return params(
        confluence_pct="0.25",
        stop_atr="0.25",
        target_2_r="2",
        target_2_min_gap_r="0.5",
        target_1_min_r="0.5",
        require_index=False,
        **over,
    )


def vp_ctx(
    *,
    close: float = 100.4,
    prev_close: float = 99.9,
    low: float = 99.95,
    pivot: float = 100.1,
    index_side: int | None = None,
) -> Context:
    levels = {**FULL, "pivot_p": pivot, "pivot_r1": 101.2, "pivot_r2": 102.5}
    prev = row(0, 99.8, 100.0, 99.7, prev_close, vwap=100.0, atr_14=0.8, **levels)
    now = row(1, 100.0, 100.5, low, close, vwap=100.0, atr_14=0.8, **levels)
    return context([prev, now], index_side=index_side)


def test_vwap_pivot_levels() -> None:
    long = assert_twins(*both("vwap_pivot", vp_ctx(), vp_params()))
    # zone 100.00-100.10; stop = 100.00 - 0.25 ATR = 99.80; risk 0.60; T1 the next pivot R1 101.20; T2 2R = 101.60
    assert prices(long) == money(
        entry_low="100.40", entry_high="100.50", stop="99.80", target_1="101.20", target_2="101.60"
    )


def test_vwap_pivot_accepts_a_close_above_the_zone_without_a_rejection_wick() -> None:
    ctx = vp_ctx(low=100.2, prev_close=99.9)  # the candle never touched the zone, but the previous close was below it
    long = run("vwap_pivot", ctx, vp_params())
    assert long is not None
    assert [(c.name, c.is_met) for c in long.components if c.name in {"rejection_of_zone", "close_beyond_zone"}] == [
        ("rejection_of_zone", False),
        ("close_beyond_zone", True),
    ]


@pytest.mark.parametrize(
    "ctx",
    [
        vp_ctx(pivot=100.5),  # 0.5% apart: no confluence
        vp_ctx(close=100.05),  # still inside the zone
        vp_ctx(low=100.2, prev_close=100.3),  # above the zone already and never came back: no event
    ],
)
def test_vwap_pivot_filters(ctx: Context) -> None:
    assert run("vwap_pivot", ctx, vp_params()) is None


def test_vwap_pivot_index_filter() -> None:
    required = vp_params()
    required = params(
        confluence_pct="0.25",
        stop_atr="0.25",
        target_2_r="2",
        target_2_min_gap_r="0.5",
        target_1_min_r="0.5",
        require_index=True,
    )
    assert run("vwap_pivot", vp_ctx(), required) is None  # no index data: it does not guess
    assert run("vwap_pivot", vp_ctx(index_side=-1), required) is None
    ok = run("vwap_pivot", vp_ctx(index_side=1), required)
    assert ok is not None and ok.components[-1].name == "index_same_side_of_vwap" and ok.components[-1].is_met
    # not required: an index on the wrong side is recorded but does not block
    assert run("vwap_pivot", vp_ctx(index_side=1), vp_params()) is not None


# ---- every strategy --------------------------------------------------------------------------------------------


def test_no_strategy_fires_on_warm_up_values() -> None:
    blank = context(
        [row(0, 100, 101, 99, 100), row(1, 100, 101, 99, 100)],
        trend15={"supertrend_dir_10_3": 1.0},
        daily={"atr_14": 1.0},
        prev_close=100.0,
    )
    p = params(
        **{
            "bars_below": 3,
            "volume_ratio_min": "1.5",
            "target_2_min_gap_r": "0.5",
            "target_1_min_r": "0.5",
            "min_room_pct": "0.2",
            "confluence_pct": "0.25",
            "require_index": False,
            "max_range_daily_atr": "1",
            "narrow_range_pct": "0.4",
            "gap_skip_pct": "2",
            "target_1_range": "1",
            "target_2_range": "2",
        }
    )
    for strategy in REGISTRY.values():
        for side in ("buy", "sell"):
            assert strategy.evaluate(blank, p, side) is None  # type: ignore[arg-type]


def test_a_draft_never_has_a_stop_on_the_wrong_side_or_targets_out_of_order() -> None:
    long = run("ema_9_21_crossover", ema_ctx(), params())
    assert long is not None
    assert long.stop < long.entry_low <= long.entry_high < long.target_1 < long.target_2
    short = run("ema_9_21_crossover", mirror(ema_ctx()), params(), "sell")
    assert short is not None
    assert short.target_2 < short.target_1 < short.entry_low <= short.entry_high < short.stop


_ = Callable  # (kept for annotations in future parametrised helpers)
