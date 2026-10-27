# tbo_technical_engine progress

Last updated 2026-10-27. Newest first. Plan: `tbo_plans/tbo_technical_engine/PLAN.md` in the project's shared folder.
The full history (every phase, decision and review fix, with its reasoning) is in
[progress/archive/PROGRESS-2026-10-27.md](progress/archive/PROGRESS-2026-10-27.md). Other docs: [runbooks.md](runbooks.md),
[monitoring.md](monitoring.md), [adr/](adr/), [te5-contract-proposal.md](te5-contract-proposal.md).

## Now

| | |
|---|---|
| Version | v0.2.2 (latest release tag) |
| Main | `1b51722` (2026-10-25) |
| Open PRs | #36 Dependabot compose group (2 updates), not ours; no branch of ours is open |
| Waiting on Bala | trading costs, recorded candles for the backtest, the `require_index` decision (see open items) |
| Redesign pause | Bala is redesigning the old design (2026-10-23). Nothing new is started here; this file only records where things stand |

## What is built (all merged to main)

1. **TE0 foundation.** Template service, outbox, Celery, research queue guarded by the mode blackout.
2. **TE1 market feed integration.** Consumers for `candle.closed`, `feed.status`, `universe.published`; history client; Parquet research cache; warm-up at start, 09:00 IST and after an outage; evaluation suspended while the feed is unhealthy.
3. **TE2 indicators.** EMA, SMA, ATR, ADX, RSI, Supertrend, VWAP, pivots, CPR, opening range, relative strength; batch and streaming forms agree; match TA-Lib to 1e-6.
4. **TE3 strategies.** The five intraday strategies (`ema_9_21_crossover`, `vwap_breakout`, `orb_15m`, `pivot_supertrend`, `vwap_pivot`), long and short, parameters in `config/strategies.yaml`; entry windows and cooldowns; candidates stored.
5. **TE4 market context.** Breadth, data quality, CPR day class, index states and VIX change as `market.context.v1` every 5 minutes, 09:15 to 15:30 IST; `GET /v1/context`. Index data from the feed's index candles (ADR-019).
6. **TE5 backtesting.** `POST /v1/backtests`, results and trades; replays through the live evaluator, simulated fills and exits, costs, walk-forward, evidence summary.
7. **TE6 swing analysis.** Daily snapshot (trend, levels, volatility, liquidity, rank, setups) on the `technical.swing` queue, Beat 16:00 and 08:00 IST.
8. **TE7 and TE8.** Numba profiling on a synthetic market (replay 8.5x, sweep 35x faster); per-boundary latency record, session report, failure drills.
9. **Signals, trading calendar (R5), review fixes 2026-10-18 and 2026-10-20, SonarQube strict fixes.** See recent changes.

## Recent changes (newest first)

1. **2026-10-25** PR #35: filelock 4.2.4, a lock-only fix for six advisories in 4.2.0.
2. **2026-10-23** SonarQube strict fixes (PR #33, #34 release request, macro): bind host as a setting, backtest and live functions split with identical digests (`scripts/identity_check.py --digests`: 950 candidates, 285 trades), virtualenv 21.14.13, unused code removed. SonarQube passed. `S7503` on three consumer handlers is left for SonarQube to accept; S9073 and S5778 deferred by Bala on 2026-10-21.
3. **2026-10-20/21** Index and VIX context from the market feed (ADR-019, #27). Review fixes 2026-10-20 (PR #28): candle memory about 400 bytes (was 1,570), tools moved into the image (`apps/`), special sessions handled, one price scale, Redis timeouts.
4. **2026-10-19** Review fixes 2026-10-18 (area 9): outbox sweep from the platform template, candidate write retried, swing run retries when more than 20 % of the universe is stale, split-adjusted daily candles, point-in-time gate in the backtest report. tbo_signals 0.6.0.

## Open items

1. **Trading costs (TE5):** `config/costs.yaml` holds Zerodha's published intraday schedule as a placeholder (`costs.status: placeholder`). Bala to confirm the broker's figures and set `status: confirmed`.
2. **TE5 exit criterion:** no recorded candles yet (the research cache fills at warm-up once the feed runs). Run `python -m tbo_technical_engine.apps.backtest_report --from <date> --to <date>` when 12 months exist.
3. **TE5 contract change to decide:** serve the evidence summary and the extra result fields (`docs/te5-contract-proposal.md`).
4. **Index data (ADR-019) built, not seen live.** Run the feed's 12-month index backfill, fill the research cache with `apps.fill_research_cache --indices nifty_50 --isins none`, re-run the `vwap_pivot` study with `require_index: true`, then Bala decides whether the live file sets it (a new `params_version`). TE6's sector-relative rank waits for this.
5. **Recorded sessions needed:** gate 3 (chart review of 10 sessions), the TE4 exit criterion, TE6 snapshots for 500 stocks by 16:30 on the real feed, and a manual review of 30 setups on real history (fixture sample `docs/swing-review-sample.md`). Replay tests use synthetic sessions.
6. **Platform side:** `deploy/services/tbo_technical_engine.yml` env lines (market feed and fundamental URLs, Redis ACL read access to `tbo.market_feed.*` and `tbo.fundamental.universe`); the grant to `filings:read` for the calendar; a persistent path for the calendar cache (`/app/data` is a tmpfs).
7. **Not built:** the nightly live-versus-backtest comparison (finding 9.18). With `TBO_TECHNICAL_FILINGS_URL` unset the calendar stays empty and one warning is logged.

## Key decisions (one line each; reasoning in the archive)

1. Index source: events first (`technical.indices` consumer), REST fallback once a day and when an index is two or more 5m candles late.
2. The calendar is rebuilt at start, hourly and on `market_calendar.updated`; a failed rebuild never replaces a working calendar by an empty one; with none, callers treat it as "not ready" and a market day is never assumed.
3. Backtest evidence is stored only from gated runs without overrides, so a narrow run cannot replace the broad one.
4. A stock whose first 5-minute open is more than 25 % from the previous close is not evaluated (corporate action guard).
5. Synthetic market data is only for benchmarks, drills and identity checks, never for results that claim anything about strategies.

## Pins and checks

1. `tbo-contracts` and `tbo-platform-kit` are git sources (not audited by pip-audit); contract vendored with `scripts/vendor_contract.sh`.
2. `make check` needs PostgreSQL and Redis. CI is manual-only. Releases through `releases/requests/`.
