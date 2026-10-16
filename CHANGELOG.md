# Changelog

## Unreleased

- TE7: profiling and speed-ups (a candle buffer with sorted starts and views, an indicator engine that catches up over skipped candles and is primed at warm-up, a replay split into tape and strategies with one tape per stock for a whole parameter grid, audit snapshots written in the background), a synthetic market (`synthetic/`, feed-format tick files), `scripts/benchmark.py`, `scripts/identity_check.py` (Numba on/off), ADR 0001. Numba is not compiled: nothing dominates.
- TE6: swing analysis and the daily technical snapshot (`technical.snapshot.v1`, `swing.setup.v1`, `GET /v1/snapshots`, `/v1/snapshots/{isin}`, `/v1/swing-setups`), migration 0005, `config/swing.yaml`.
- Generated from the tbo_platform service template.
- TE5: backtesting. `POST /v1/backtests` and results (replacing the stubs), a replay through the live strategy code, the Indian
  intraday cost model (placeholder figures), walk-forward with out-of-sample apart, vectorbt statistics, the per-strategy evidence
  summary, caps and a separate research queue, `scripts/backtest_report.py`, migration 0006.
