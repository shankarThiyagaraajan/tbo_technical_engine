# TE5: what the contract does not carry yet

`POST /v1/backtests`, `GET /v1/backtests/{id}` and `/trades` follow the contract in `tbo_contracts` v1.2.0 exactly. The engine
also computes things the contract has no place for. They are stored and unserved until `tbo_contracts` takes a change (contract
first, then `scripts/vendor_contract.sh`). Nothing here has been changed in `tbo_contracts`.

## 1. Strategy evidence (needed by DE5)

The decision engine attaches a strategy's evidence to each call. The data is in table `strategy_evidence` (one row per strategy and
`params_version`, replaced each time a backtest at the file's own parameters succeeds). Proposed operation:

`GET /v1/strategies/{strategy}/evidence`, scope `technical:read`, consumer `tbo_decision_engine`. Query `params_version`
(default: the current one). 404 until a backtest has produced one. Body (what `backtest/evidence.py` builds):

```json
{
  "strategy": "vwap_breakout", "params_version": "2026.10.1", "backtest_id": "01J...",
  "requested_from_date": "2025-10-01", "requested_to_date": "2026-09-30",
  "tested_from_date": "2025-10-01", "tested_to_date": "2026-09-30", "sessions_tested": 249, "stocks_tested": 412,
  "all_periods":   {"trade_count": 0, "win_rate_pct": "0", "avg_r": "0", "expectancy_r": "0", "max_drawdown_r": "0", "profit_factor": null},
  "out_of_sample": {"trade_count": 0, "win_rate_pct": "0", "avg_r": "0", "expectancy_r": "0", "max_drawdown_r": "0", "profit_factor": null},
  "out_of_sample_folds": 0, "parameter_stability_pct": null,
  "low_sample": true, "low_sample_out_of_sample": true,
  "costs_status": "placeholder", "coverage_note": "..."
}
```

`avg_r` is before charges and `expectancy_r` after (the contract's `Backtest.metrics` has both without saying which; say so there too).
`tested_*` are the dates the candles really cover, which can be shorter than the request: a reader must see that.

## 2. Extra fields on `Backtest` (open response, additive)

`result` (object) with `coverage`, `costs` (`schedule_version`, `status`, `source`), `walk_forward` (folds, `out_of_sample`,
`in_sample`, `stability_pct`, `parameter_choices`), `statistics` (total return %, Sharpe, max drawdown %), `counts` (candidates,
gated out, never filled, skipped because a position was open), `grid_skipped`. The stored JSON is in `backtest.result`.

## 3. Request

`BacktestCreate` has no switch for the point-in-time gate (it applies when `isins` is empty and not otherwise) and none for the
parameter grid (walked when there are no `params_overrides` and the strategy has a grid in `config/backtest.yaml`). Say so in the
operation's description, or add the switches.
