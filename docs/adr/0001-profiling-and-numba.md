# ADR 0001: profiling, speed-ups and Numba (TE7)

Status: accepted, 2026-10-16. Plan item: TE7 in `tbo_plans/tbo_technical_engine/PLAN.md`.

## What was measured, and on what

There are no recorded Kite sessions yet, so every number below comes from a **synthetic market**: seeded random walks
with gaps, trend days and volume bursts, built as one-minute arrays and aggregated to 5-minute, 15-minute and daily
candles by the market feed's own rule (`src/tbo_technical_engine/synthetic/`). It can also write the feed's tick-Parquet
format (`write_ticks`), so the feed's replay source can serve the same sessions. Timings of the code are real; what the
candles *look like* is not, so trade counts and hit rates say nothing about the strategies.

Machine: Intel Xeon @ 2.1 GHz, 4 cores, 15 GB, Python 3.12.3, NumPy 2.2, Numba 0.68.0, one process, one core. The mini
PC is not measured here.

Reproduce: `uv run python -m scripts.benchmark replay|sweep|live ...` (`--help`), `uv run python -m scripts.identity_check`.

## Where the time went

`cProfile` of a replayed stock (before): almost all of it was two things.

1. **Per-candle work that scaled with the history.** Today's context was found by converting every candle in the buffer
   to IST (`astimezone`) to filter by date, and a 15-minute or daily candle arriving made the indicator engine rebuild its
   whole suite from the buffer.
2. **Doing the same work once per parameter set.** A walk-forward study replays each stock for every point of the strategy's
   grid, and the indicators and contexts do not depend on the parameters.

On the live path a third thing showed up once the first two were gone: the audit write (`indicator_snapshot`) was one
database transaction per candle, in front of the strategies, and the first candle of each stock paid a full indicator
build.

## What changed (all behaviour-preserving)

| Change | Where |
|---|---|
| Today's context found by a backward scan with a date comparison, no per-candle timezone conversion | `services/evaluator.py` |
| Sorted start-time lists and zero-copy views over the candle buffer (bisect, no list copies) | `services/candle_buffer.py` |
| Indicator engine catches up over skipped candles instead of rebuilding (the evaluator reads only inside entry windows, so there are gaps every evening and morning); rebuilds only when an older candle is corrected | `services/indicator_engine.py` |
| Indicator state built at warm-up (`prime`), so the first live candle of a stock pays no build | `services/indicator_engine.py`, `services/supervisor.py` |
| Replay split into `build_tape` (contexts, independent of the parameters) and `replay_tape` (strategies); `run_grid` builds one tape per stock and replays it for every parameter set; the time-budget check is re-based on the first stock | `backtest/replay.py`, `backtest/engine.py`, `backtest/study.py` |
| Audit snapshots queued in memory and written every 0.5 s in one transaction (a crash loses at most 0.5 s of audit rows; candidates are written as before) | `services/recorder.py`, `store/indicators.py` |
| `finite()` as a plain loop (about a million calls in a one-year backtest) | `strategies/base.py` |

## Results

All figures are from the synthetic market on the machine above.

**Replay, one parameter set** (10 stocks x 60 sessions): 55.4 s before, 6.5 s after (92 ms to 10.8 ms per stock-session,
8.5x). The same 2171 candidates.

**Full sweep** (2 stocks x 60 sessions, all five strategies with their grids, 33 parameter sets in all, walk-forward and
the vectorbt statistics): 331 s before, 9.4 s after (35x). The trade count of every strategy is identical (69, 24, 43, 86,
139).

**Live path, 500 stocks, 12 consecutive boundaries** (`candle closed` to the end of evaluation; the candles of a boundary
arrive back to back, so a stock's latency includes the stocks queued before it):

| Variant | p50 | p95 | max |
|---|---|---|---|
| Audit rows written in the candle's path, no priming (the shipped TE6 behaviour with the replay fixes) | 1.20 s | 5.93 s | 14.5 s |
| ... with priming at warm-up | 1.04 s | 2.05 s | 2.82 s |
| Priming and the background audit writer (shipped) | 0.37 s | **0.88 s** | 1.45 s |

(Before any change, an earlier run on the same machine gave p95 9.9 s; that code is no longer in the tree, so it is quoted,
not reproducible.)

**Projection, not a measurement.** A year of 500 stocks is 125,000 stock-sessions; at 9.4 s for 120 stock-sessions with
33 parameter sets that is about 2.7 hours on one core of this machine (was about 96 hours). The sweep job is split per
strategy and capped below the Celery visibility timeout (lesson 14), and a study that would not fit its budget falls back to
the fixed parameters and says so.

## Numba: not compiled, on purpose

The plan says to compile only loops that dominate. After the changes above nothing in this service is a numeric loop that
dominates a profile: the remaining time is spread over pydantic models, Decimal rounding, indicator updates per candle, and
database and Redis calls. Compiling an indicator update would save microseconds on a path that is now bound by those calls,
and would add a compile-on-first-call cost and a second code path to keep identical. So no `@njit` was added.

Numba is still in the process, because vectorbt uses it for the return statistics. It is locked to 0.68.0 with NumPy 2.2 in
`uv.lock` (upgrade vectorbt, NumPy, Numba and Polars together, as `pyproject.toml` says).

What the plan asks to prove, "identical results with the JIT on and off", is built and passes, so it keeps being true if a
kernel is compiled later: `uv run python -m scripts.identity_check` runs the same synthetic sessions in two fresh
interpreters, one with `NUMBA_DISABLE_JIT=1`, and compares SHA-256 digests of the candidates, of every indicator series
(exact `repr` of each float) and of one whole study including the vectorbt statistics.
`tests/test_scripts.py::test_the_jit_on_and_off_give_identical_digests_on_twenty_stock_sessions` runs it on 20 stock-sessions.
The plan's "20 recorded sessions" are 20 *synthetic* sessions until real recordings exist.

## Other findings

- The kit's `EventConsumer` validates every event against its JSON Schema, about 6 ms each; at 500 stocks that is 3 s of
  consumer time per boundary on top of the figures above (`candle closed` events only reach the consumer after the feed
  published them, and the benchmark calls the handler directly). **This is outside this repository and unmeasured on a real
  stream; it is the first thing to look at if the p95 on real data is over 2 s.**
- Writing tick files with Polars stores strings as Arrow `large_string`, so the feed's reader needs a cast to its own schema;
  checked once against the feed's recorder schema with PyArrow (same columns, same order, every row read back).

## Exit criteria

| Criterion | State |
|---|---|
| Candle close to candidate p95 under 2 s at the planned universe size | **Met on the synthetic market (0.88 s at 500 stocks). Pending on real data**: it excludes the feed's time to build and publish the candle, the network, the stream read and the schema validation above. TE8 measures it in the worker on the worker's own clock, so a real session answers it. |
| A full walk-forward sweep completes outside market hours on the mini PC | **Pending.** About 2.7 hours projected on one core of this machine for a year of 500 stocks (extrapolated). Needs the mini PC and a 12-month cache of real candles. |
| Identical results with the JIT on and off on 20 recorded sessions | Done on 20 synthetic sessions; repeat on recordings. |
| Each decision recorded with before/after timings | This document. |
