"""The per-strategy evidence summary the decision engine attaches to a call (DE5).

Built from one successful backtest at the parameter file's own values (no overrides), so it describes the strategy as it
runs live. It is deliberately flat and small: a call cites it, a person reads it.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from tbo_technical_engine.schemas.backtest import Coverage, Metrics, WalkForward


def metric_dict(m: Metrics) -> dict[str, Any]:
    """The metrics of one slice as a JSON-safe dict (None where a figure cannot be computed)."""
    return {
        "trade_count": m.trade_count,
        "win_rate_pct": format(m.win_rate_pct, "f"),
        "avg_r": format(m.avg_r, "f"),
        "expectancy_r": format(m.expectancy_r, "f"),
        "max_drawdown_r": format(m.max_drawdown_r, "f"),
        "profit_factor": None if m.profit_factor is None else format(m.profit_factor, "f"),
    }


def build_evidence(
    *,
    strategy: str,
    params_version: str,
    backtest_id: str,
    from_date: date,
    to_date: date,
    metrics: Metrics,
    walk_forward: WalkForward,
    coverage: Coverage,
    costs_status: str,
    min_trades: int,
) -> dict[str, Any]:
    """Sample size, win rate, expectancy (after charges), maximum drawdown, the period really tested, and the
    out-of-sample result reported on its own. `low_sample` and `costs_status` are the caveats a reader must see."""
    oos = walk_forward.out_of_sample
    return {
        "strategy": strategy,
        "params_version": params_version,
        "backtest_id": backtest_id,
        "requested_from_date": from_date.isoformat(),
        "requested_to_date": to_date.isoformat(),
        "tested_from_date": None if coverage.first_candle_date is None else coverage.first_candle_date.isoformat(),
        "tested_to_date": None if coverage.last_candle_date is None else coverage.last_candle_date.isoformat(),
        "sessions_tested": coverage.trading_days,
        "stocks_tested": coverage.isins_with_data,
        "all_periods": metric_dict(metrics),
        "out_of_sample": metric_dict(oos),
        "out_of_sample_folds": len(walk_forward.folds),
        "parameter_stability_pct": None
        if walk_forward.stability_pct is None
        else format(walk_forward.stability_pct, "f"),
        "low_sample": metrics.trade_count < min_trades,
        "low_sample_out_of_sample": oos.trade_count < min_trades,
        "costs_status": costs_status,
        "coverage_note": coverage.note,
    }
