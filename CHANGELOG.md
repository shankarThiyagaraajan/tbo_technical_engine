# Changelog

## Unreleased

- TE6: swing analysis and the daily technical snapshot (`technical.snapshot.v1`, `swing.setup.v1`, `GET /v1/snapshots`, `/v1/snapshots/{isin}`, `/v1/swing-setups`), migration 0005, `config/swing.yaml`.
- Generated from the tbo_platform service template.
- TE5: backtesting. `POST /v1/backtests` and results (replacing the stubs), a replay through the live strategy code, the Indian
  intraday cost model (placeholder figures), walk-forward with out-of-sample apart, vectorbt statistics, the per-strategy evidence
  summary, caps and a separate research queue, `scripts/backtest_report.py`, migration 0006.
