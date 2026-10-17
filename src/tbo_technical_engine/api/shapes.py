"""Records as the API writes them."""

from __future__ import annotations

from typing import Any

from tbo_technical_engine.schemas.signal import SignalCandidate
from tbo_technical_engine.utils.timefmt import z


def candidate_dict(c: SignalCandidate) -> dict[str, Any]:
    """A candidate as the API returns it: `model_dump` writes datetimes as `+00:00`, the contract wants `Z`
    (`2026-10-15T04:05:00Z`), so the two time fields are re-rendered."""
    data = c.model_dump(mode="json")
    data["candle_start_at"] = z(c.candle_start_at)
    data["created_at"] = z(c.created_at)
    return data


def backtest_dict(row: Any) -> dict[str, Any]:
    """A `backtest` row as the contract's Backtest. `metrics` has the six contract fields; the rest of a run (coverage,
    costs status, walk-forward) is stored with it but not served until the contract has a place for it."""
    return {
        "backtest_id": row.backtest_id,
        "status": row.status,
        "strategy": row.strategy,
        "params_version": row.params_version,
        "params_overrides": row.params_overrides,
        "from_date": row.from_date.isoformat(),
        "to_date": row.to_date.isoformat(),
        "isins": row.isins,
        "metrics": row.metrics,
        "code_version": row.code_version,
        "backend": row.backend,
        "error": row.error,
        "created_at": z(row.created_at),
        "started_at": None if row.started_at is None else z(row.started_at),
        "finished_at": None if row.finished_at is None else z(row.finished_at),
    }


def backtest_trade_dict(row: Any) -> dict[str, Any]:
    """A stored trade as the contract's Trade object, money as decimal strings."""
    return {
        "backtest_id": row.backtest_id,
        "trade_no": row.trade_no,
        "isin": row.isin,
        "side": row.side,
        "entry_at": z(row.entry_at),
        "exit_at": z(row.exit_at),
        "entry_price": format(row.entry_price, "f"),
        "exit_price": format(row.exit_price, "f"),
        "exit_reason": row.exit_reason,
        "r_multiple": format(row.r_multiple, "f"),
    }
