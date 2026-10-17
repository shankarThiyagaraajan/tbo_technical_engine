"""Backtest results as plain JSON (what is stored in `backtest.metrics` and `backtest.result`)."""

from __future__ import annotations

from datetime import date
from typing import Any

from tbo_costs import ScheduleBook

from tbo_technical_engine.backtest.evidence import metric_dict
from tbo_technical_engine.backtest.study import Study
from tbo_technical_engine.schemas.backtest import BacktestSettings, Coverage, Fold, Metrics, WalkForward


def costs_summary(book: ScheduleBook) -> dict[str, Any]:
    """Which charge schedule priced the run, and how far to trust it (`digest` names the exact figures)."""
    entry = book.entries[-1]
    return {
        "schedule_version": entry.schedule_version,
        "status": book.status,
        "source": entry.source,
        "digest": book.digest(),
    }


def day(d: date | None) -> str | None:
    """A date as ISO text, None passes through."""
    return None if d is None else d.isoformat()


def contract_metrics(m: Metrics) -> dict[str, Any]:
    """The `metrics` object of the contract's Backtest: exactly its six fields."""
    return metric_dict(m)


def full_metrics(m: Metrics) -> dict[str, Any]:
    """The contract fields plus the totals, for the stored result."""
    return {
        **metric_dict(m),
        "total_r": format(m.total_r, "f"),
        "total_costs": format(m.total_costs, "f"),
        "gross_pnl": format(m.gross_pnl, "f"),
    }


def coverage_dict(c: Coverage) -> dict[str, Any]:
    """Coverage (sessions asked, found, skipped) as a JSON-safe dict."""
    return {
        "requested_isins": c.requested_isins,
        "isins_with_data": c.isins_with_data,
        "skipped_no_data": c.skipped_no_data,
        "first_candle_date": day(c.first_candle_date),
        "last_candle_date": day(c.last_candle_date),
        "trading_days": c.trading_days,
        "gate_applied": c.gate_applied,
        "note": c.note,
    }


def fold_dict(f: Fold) -> dict[str, Any]:
    """One walk-forward fold as a JSON-safe dict."""
    return {
        "train_from": day(f.train_from),
        "train_to": day(f.train_to),
        "test_from": day(f.test_from),
        "test_to": day(f.test_to),
        "chosen": f.chosen,
        "stable": f.stable,
        "train": metric_dict(f.train),
        "test": metric_dict(f.test),
    }


def walk_forward_dict(w: WalkForward) -> dict[str, Any]:
    """The walk-forward result (folds, pooled out-of-sample, stability) as a JSON-safe dict."""
    return {
        "folds": [fold_dict(f) for f in w.folds],
        "out_of_sample": full_metrics(w.out_of_sample),
        "in_sample": full_metrics(w.in_sample),
        "stability_pct": None if w.stability_pct is None else format(w.stability_pct, "f"),
        "parameter_choices": w.parameter_choices,
    }


def study_result(study: Study, settings: BacktestSettings, costs: ScheduleBook) -> dict[str, Any]:
    """Everything a run stores beside the contract's metrics and the trades."""
    return {
        "coverage": coverage_dict(study.coverage),
        "costs": costs_summary(costs),
        "settings": settings.model_dump(mode="json", exclude={"grids"}),
        "grid": study.grid,
        "grid_skipped": study.grid_skipped,
        "totals": full_metrics(study.metrics),
        "walk_forward": walk_forward_dict(study.walk_forward),
        "statistics": study.statistics,
        "counts": study.counts,
    }
