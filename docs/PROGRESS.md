# tbo_technical_engine progress

Plan: `tbo_plans/tbo_technical_engine/PLAN.md` in the project's shared folder. What is built, what is not, decisions.

## Status

| Phase | State |
|---|---|
| TE0 Repository foundation | Built (branch `claude/technical-te0-te4-1mrk0g`). Generated from the tbo_platform service template at `bfaaabf`; the contract is vendored (`v1.2.0`); all 12 operations are protected stubs answering 501; Celery with the outbox sweep; research queue guarded by the kit's mode blackout; signal worker skeleton. Checked locally only (CI did not run). |
| TE1 Market feed integration | Built, tested against a fake feed over `httpx.MockTransport` and a real Redis (no real feed or Kite). `candle.closed`, `feed.status` and `universe.published` consumers; typed history client with cursor paging; Parquet research cache (write-through at warm-up); warm-up at start, 09:00 IST and after an outage; evaluation suspended while the feed is unhealthy. |
| TE2 Indicator library | Built. EMA/SMA, ATR, ADX, RSI, Supertrend, session VWAP and bands, classic/Camarilla pivots, CPR, opening range, volume average, relative strength; batch and streaming forms agree over a year of data; EMA/ATR/ADX/RSI match TA-Lib to 1e-6 (TA-Lib is a test-only dependency). Live `IndicatorEngine` (rebuilds on any non-append change), `indicator_snapshot` table (14-day retention, pruned 02:30 IST), `GET /v1/indicators/{isin}`. |
| TE3 Intraday strategy engine | Built. The five strategies (`ema_9_21_crossover`, `vwap_breakout`, `orb_15m`, `pivot_supertrend`, `vwap_pivot`), long and short, as pure rules over a context; parameters and `params_version` in `config/strategies.yaml`; entry windows (09:30-14:30 IST, ORB to 11:30) and per-strategy cooldowns (30 min; ORB once a session); cooldown checkpoint in Redis; candidates stored in `signal_candidate` and announced as `signal.candidate.v1` through the outbox; `GET /v1/signals`, `/v1/signals/{id}`, `/v1/strategies`. A replayed or restarted session gives identical candidates (tested). Not done: gate 3 (chart review of 10 recorded sessions) needs real recorded sessions. |
| TE4 Market context | Built. Breadth over the universe (advancing/declining/unchanged against the previous close), data quality from the feed's health, CPR day class, index states (VWAP side, 15-minute trend) and the VIX change, published as `market.context.v1` every 5 minutes from 09:15 to 15:30 IST (once per boundary, stored in `market_context`); `GET /v1/context` and `/v1/context/history`. **Index data (ADR-019, 2026-10-20):** `services/index_feed.FeedIndexSource` reads the market feed's `index_candle.closed` events (consumer group `technical.indices`) with `GET /v1/index-candles` as the fallback, so index states, VIX and CPR class are filled once the feed's index poll runs; an index with no candles is left out and counted by `technical.index_missing`. Not yet seen on a real session (needs the feed's poll in paper). Exit criterion (5 sessions on time, matched by hand) needs live sessions. |
| TE6 Swing analysis and daily snapshot | Built (branch `claude/technical-te6-9kz86w`), tested on fixtures and a fake feed with a real PostgreSQL and Redis; no real feed or recorded history yet. `swing/` (pure): trend state and score, relative-strength percentile, support and resistance zones, ATR % and its one-year percentile, volatility regime, Bollinger squeeze, 20-day traded value and a Corwin-Schultz spread estimate, and three long-only setup detectors (base breakout, pullback to the 20 or 50 EMA, 52-week high breakout). `SnapshotService` runs at 16:00 IST (snapshots + setups) and 08:00 IST (snapshot refresh) as Celery tasks on the new `technical.swing` queue; announces `technical.snapshot.v1` and `swing.setup.v1` through the outbox; `GET /v1/snapshots`, `/v1/snapshots/{isin}`, `/v1/swing-setups`. Parameters in `config/swing.yaml`. 500 stocks are measured and ranked in about two seconds (the daily-candle fetch is the rest of the half hour). Exit criterion 2 (30 setups reviewed) is prepared as `docs/swing-review-sample.md` on fixtures; the review on recorded data waits for recorded sessions. |
| TE5 Backtesting and research | Built (branch `claude/technical-te5-0p6q89`), checked locally only (CI did not run). `POST /v1/backtests`, `GET /v1/backtests/{id}` and `/trades` replace the stubs (scope `technical:research`, `Idempotency-Key`); the job runs on the `technical.research` queue and replays each stock through the live `StrategyEvaluator`, `IndicatorEngine` and `CandleBuffer` (a test proves its candidates equal the live worker's). Fills, exits, slippage and the Indian intraday cost model (`config/costs.yaml`) turn candidates into trades; metrics are in R, after charges. Walk-forward with a parameter grid (`config/backtest.yaml`), out-of-sample reported apart, parameter stability, vectorbt return statistics, per-strategy evidence summary in `strategy_evidence`, caps on stocks, days, stock-days and concurrent jobs, a job time budget, and a Beat sweep that re-sends lost jobs. `python -m tbo_technical_engine.apps.backtest_report` writes the Markdown report. **Not done:** the exit criterion (a 12-month report on all five strategies) needs recorded candles and none exist yet, and the costs are a **placeholder** until the owner confirms them (see Decisions and Open items). |
| TE7 Profiling and Numba | Built on branch `claude/te7-te8-9h7qqj` (ADR `docs/adr/0001-profiling-and-numba.md`). Profiled on a **synthetic market** (`synthetic/`, `scripts/benchmark.py`): replay 8.5x faster, a full sweep 35x faster (331 s to 9.4 s on 2 stocks x 60 sessions, identical trades), live path at 500 stocks p95 0.88 s (was 5.9 s with the same code before priming and the background audit writer). Numba assessed and **not compiled** (no numeric loop dominates); `scripts/identity_check.py` proves JIT on/off identity (20 synthetic sessions in a test). **Pending on real data:** p95 under 2 s on a real session, the walk-forward sweep timed on the mini PC, identity on recorded sessions. |
| Trading calendar adoption (R5) | Built (merged; `make check` clean locally, CI did not run). `services/market_calendar.py`, the `technical.calendar` consumer, the `calendar` worker label and `technical.calendar_age` signal, kit pinned at `4e92fc0`, tbo-contracts at `39085e3` (1.10.0). Opt-in: with `TBO_TECHNICAL_FILINGS_URL` unset the calendar stays empty (as the empty holiday file was) and one warning is logged. **Pending in tbo_platform:** the grant, the URL and cache lines. |
| TE8 Hardening | Built on branch `claude/te8-9h7qqj` (on top of TE7). A per-boundary evaluation record (`evaluation_boundary`, migration 0007: evaluated, stale, cold, inactive, failed, absent, p50/p95/max latency, candle close to end of evaluation on the worker's clock), Prometheus gauges set by the API process (the worker has no port), `python -m tbo_technical_engine.apps.session_report` (the ten-clean-sessions check), failure drills as tests on a real PostgreSQL and Redis (`tests/drills/`: feed outage, Redis restart with and without its data, worker crash), runbooks and monitoring notes (`docs/runbooks.md`, `docs/monitoring.md`). Found and fixed on the way: the kit's consumer retries a missing group for ever after a Redis restart without data (`consumers/handlers.py` now recreates it and asks for a warm-up). Alert rules, their tests and dashboard panels are a companion `tbo_platform` PR. **Pending on real data: ten consecutive clean sessions and the real p95; nothing here proves either on synthetic data.** |
| Signals pilot (stage 5) | Merged (PR #11, #12, #14; `make check` clean locally, CI did not run). `config/signals.yaml` declares 11 signals (the six board stages, worker and swing activity, three events); `services/platform_signals.py` builds `Signals` per process; the signal worker, the Celery worker and the API call it; the manifest is served at `GET /v1/signal-manifest` (scope `technical:read`). `tbo_signals` 0.5.0 pinned by tbo_platform_kit commit `aca4999`, `tbo-contracts` 1.16.0 by commit `a3317a2` (untagged, vendored `contract/` the same), kit pinned at `4e92fc0`. **Pending on the laptop:** checking the numbers against `redis-cli` and `psql`; a scrape port for the worker (see Decisions). |
| Code review 2026-10-18 (numbers 9.x) | Fixed (9.1 ported from platform 2.2; the 9.14 contract wording is in contracts 1.16.0). See "Review fixes" under Decisions. |
| Code review 2026-10-20 (9.20 to 9.26) | Fixed (PR #28 merged, f96d928; `make check` clean locally, CI did not run): 9.20 memory, 9.21 tools in the image, 9.22 session hours, 9.23 one price scale, 9.25 Redis timeouts, 9.26 grace, universe backoff, prune index. See "Review fixes (2026-10-20)". |
| SonarQube strict fixes (2026-10-23, FIX-BRIEFS 2.10) | On branch `claude/project-thread-n7y5rv` (PR open; `make check` clean locally, CI did not run). See "SonarQube strict fixes (2026-10-23)". |

## Decisions

- **Index source (ADR-019, 2026-10-20).** `services/index_feed.FeedIndexSource` is the `IndexSource` of the signal worker. Events
  first: the `technical.indices` consumer (start at `$`) offers every `index_candle.closed` to the source, newest revision wins. REST is
  the fallback: the first context of a day reads each tracked index's 5m of today, 15m of 7 days and 1d of 10 days over
  `GET /v1/index-candles` (a restart mid-session has no events for the earlier candles), and an index two or more 5m candles late is read
  again, at most once per 5 minutes. The tracked list comes from `GET /v1/indices` hourly (unknown: nifty_50 and India VIX are expected).
  India VIX is the VIX reading (newest 5m close against the previous 1d close), never an index state. The index VWAP is the feed's
  `session_vwap` (an index has no volume, so the volume-weighted indicator would be NaN). A missing index is left out and counted
  (`technical.index_missing`); it does **not** mark the context degraded, because degraded stops every call and the decision engine
  scores without the index instead. Timing: the feed polls 20 s after a boundary and the context is built 15 s after it, so the context
  at T normally carries the index candle that closed at T - 5 minutes; accepted rather than delaying the context (the decision engine's
  latency budget). A lost consumer group asks for the REST history again.
- **TE5 index filter (review 2.4, 2.6).** A backtest rebuilds nifty_50's VWAP side from the research cache's index candles
  (`<cache>/indices/5m/nifty_50.parquet`, filled by `python -m tbo_technical_engine.apps.fill_research_cache --indices nifty_50`), using the side of the newest
  index candle that closed by the entry candle's start (`backtest/index_side.py`; live may lag one candle more). Without those candles a
  study runs as live without index data, and the Markdown report says **Index filter off** in its header. `config/strategies.yaml`
  keeps `require_index: false` until the TE5 re-run with `require_index: true` is seen; switching it is a new `params_version`.
- **Signals (tbo_signals).** `GET /v1/signals` is this service's trade-candidate list, so the library's default manifest path is taken: the
  manifest is served at `GET /v1/signal-manifest` instead (`services/platform_signals.MANIFEST_PATH`); the platform registry needs this
  path for `technical`. The route is in the contract since tbo_contracts 1.7.0, so
  `tests/test_platform.py` has no allowance. The signal worker and the Celery worker have no HTTP port, so their metrics are not scraped: set
  `TBO_TECHNICAL_SIGNALS_METRICS_PORT` on the worker (and give Prometheus that target) to expose them; events at level `full` reach the
  `<mode>.signals.v1` stream without it. Level: `TBO_TECHNICAL_SIGNALS` (off in lab, metrics in paper and live). No alerts are declared:
  the workers sleep outside the session, so "stale" needs a session-hours rule.

- Pins: tbo_contracts commit `39085e3` (1.10.0, not tagged yet; the vendored `contract/` is at the same commit and now carries the manifest operation
  change) and tbo_platform commit `bfaaabf` (no platform tag yet). The kit's own sources still pin tbo_contracts v1.1.0, so
  `[tool.uv] override-dependencies` forces the commit; drop it when the kit moves. `tbo_signals` is pinned by tbo_platform_kit commit `061dd40`.
- Operations whose phase is not built answer 501 `not_implemented`; Schemathesis tolerates 501 until the last stub goes.
- Process entry points live in the package (`tbo_technical_engine.apps.*`) so the image ships them; the plan's top-level `apps/` is this.
- The old empty `tbot_technical_engine` repository is not used.

- **Warm-up lives in the signal worker, not in Beat.** Indicator state is in that process, so a Beat task could not warm it. The
  worker's supervisor warms at start, at 09:00 IST each trading day, after any unhealthy `feed.status`, and retries stocks that
  failed. Beat keeps the outbox sweep only.
- Readiness (`services/readiness.py`): a candidate needs a healthy feed *and* a warm-up that finished after the last unhealthy
  status. A warm-up that overlapped an outage is discarded (`generation`).
- A closed candle older than 120 s when it arrives is stored but not evaluated (a backlog after a restart is not a signal).
- 15-minute inputs for the 5-minute entry are read with `CandleBuffer.before(cutoff=entry candle start)`, so whether the
  15-minute candle closing at the same instant has arrived cannot change a result (live, replay and backtest agree).
- The candle stream group starts at `$` (history comes from REST); feed status and universe groups start at `0`.

- **Strategies are pure rules over a context** (`strategies/base.py`): the last 8 five-minute rows with their indicator values, the 15-minute
  indicators as of the entry candle's start, the daily ATR, today's candles and the previous close. They keep no state, so a restart, a
  replay and a backtest agree; the only state is the cooldown (`StrategyEvaluator.fired`, checkpointed in Redis, restored per stock).
- **A candidate's ID is derived** from (stock, strategy, side, candle), as a valid ULID whose time part is the candle close. A redelivered
  candle gives the same ID and the database refuses the copy, so no duplicate event is queued. The cooldown moves only after the write.
- Prices are rounded to the 0.05 tick the safe way: a buy's entry zone widens, its stop moves away and its targets nearer; sells mirror.
  A draft whose stop, zone and targets are not in order after rounding is dropped.
- Choices where the plan is silent (all in `config/strategies.yaml`, tunable only through the backtest): the entry zone is the close plus
  0.1 ATR on the trade side; VWAP breakout and the EMA crossover take the *nearer* of their two stops; ORB targets are measured from the
  entry, not the range edge; T2 of the pivot-aimed strategies is at least 0.5 R beyond T1; a pivot closer than 0.5 R is no target.
- A filter that cannot be checked blocks the signal (ORB without a daily ATR or previous close; Pivot + Supertrend without the 15-minute
  trend). "Prefer the first retest within 3 bars" (VWAP breakout) is not a condition.
- **`vwap_pivot`'s "index on the same side of its VWAP" is off** (`require_index: false`): the feed has no index data (see open items). When
  an index source exists the context carries `index_side` and the component is recorded; switching the parameter on makes it required.
- `strategy_params` (listed in the plan) is not a table: the parameter file is the source, its version is stored on every candidate, and
  the history is git's.

- **Market context** is built in the signal worker's supervise loop (15 s tick): at a boundary plus 15 s it builds, stores (one row per
  `context_at`, so two processes or a restart publish once) and queues the event. A feed that is not healthy still gets a context, with
  `data_quality: degraded`; so does a configured index source that fails. Breadth counts only stocks with a candle today and a previous session.
- **`IndexSource` is the seam** (`context/index_source.py`): index candles (5m, 15m, previous day) and the VIX reading. `NoIndexSource` is
  the default; the pure functions in `context/compute.py` are tested on hand numbers. Index trend 15m = Supertrend(10,3) and EMA 9/21 agree
  beyond a 0.05% band, else sideways. CPR class from the previous day's CPR width % of the primary index (nifty_50, else the first):
  narrow below 0.25, wide above 0.5 (settings `cpr_narrow_pct`, `cpr_wide_pct`; starting values, no source for them in the plan).
  The class is fixed at 09:15 and kept for the day. The strategy filter `vwap_pivot.require_index` reads the same index state.

- **TE5: what a backtest assumes** (all in `config/backtest.yaml`, all stored with each result; the plan is silent on them).
  The signal is known at its candle's close; a limit order at the far edge of the entry zone lives one candle (fills at the open if
  the open is already better, else at the limit if reached, else no trade); slippage 2 bps against the trader on every fill; target 1
  sells half, then the stop on the rest moves to the entry; open trades leave at the open of the 15:15 candle; a candle touching both
  stop and target counts as the stop (adverse first, also on the fill candle); a fill beyond the stop is no trade; one trade at a time
  per stock and strategy; 100,000 rupees a trade. Rounded numbers: Decimal, prices to 0.01.
- **TE5: `avg_r` is before charges, `expectancy_r` after.** The contract has both and does not say; the pair shows the cost drag.
  The decision engine should judge a strategy by `expectancy_r`.
- **TE5: replay and walk-forward.** A parameter set is replayed once over the whole range and its trades are cut into windows by entry
  date, which equals running each window apart (the strategies are pure; cooldown and open positions never survive a session). Daily
  candles join the replay buffer when their day has ended (as the 09:00 warm-up does); a test compares the replay with the live worker.
  Folds: 60 sessions of train, 20 of test, moving by 20; a fold picks the grid point with the best train expectancy (at least 10
  trades, ties to the file's values); out-of-sample is the pooled test windows only. `stable` = every one-parameter neighbour reached
  80% of the best train expectancy. Sessions are the dates that have candles, not a holiday calendar (the kit's holiday file is empty).
- **TE5: point-in-time rules** come from `GET /v1/gate?date=` for each weekday when no ISINs are listed (the stock must be in that
  day's gate; `block` / `long_only` / `short_only` filter the side after the cooldown, as in live). With ISINs listed there is no gate.
  A job with no ISINs and no `TBO_TECHNICAL_FUNDAMENTAL_URL` fails with that reason.
- **TE5: vectorbt** is used for the return statistics (Sharpe, drawdown %) on a reference capital, not for the fill loop: exits depend
  on the path (target 1, then a stop at entry), which `from_signals` cannot express. `plotly<6` is pinned because vectorbt 0.28 fails
  to import with plotly 6. The image grows (pandas, numba, scipy, matplotlib come with it).
- **TE5: limits.** `max_runtime_seconds` is 3300 because the Redis broker redelivers an unacknowledged task after 3600 s; a longer
  job would run twice. Other parameter sets run only if the first run's duration times their number fits the budget, else the result
  says `grid_skipped`. Replay costs about 0.07 s per stock-session (measured on synthetic data), so one year of 500 stocks is hours:
  that is TE7's job. Profile: `StrategyEvaluator.context` re-filters the whole 1,000-candle series with `astimezone` on every candle
  and takes about 60% of the replay time.
- **TE5: served and stored.** The contract's `Backtest` carries six metrics. Coverage, costs status, walk-forward folds, statistics and
  the evidence summary are stored (`backtest.result`, `strategy_evidence`) but not served: that needs a `tbo_contracts` change, proposed
  in `docs/te5-contract-proposal.md`. A re-run of a finished job is a no-op; a running job is re-claimable only after 56 minutes.

## Review fixes (code review 2026-10-18, numbers 9.x)

- **9.2** `backtest/simulate.py`: an entry filled at the LIMIT (the candle opened beyond it and came back) credits no target on that candle and takes no gap exit; only the stop is tested. A fill at the open is judged as before. The shared vector `tests/backtest/vectors/fills.json` is the kit's `test-vectors/fills.json` (finding 3.1); keep the two equal. Backtest R on strong breakout candles is lower than before.
- **9.3** `SignalRunner` retries the candidate write (`WRITE_RETRY_DELAYS`, 0.25, 1, 2 s) itself: the candle is already in the buffer, so a redelivered event is dropped as a duplicate and the old "retry by redelivery" never existed. After the retries the boundary is lost and shows as FAILED in `evaluation_boundary` and in the `evaluator_failed` log.
- **9.4** The swing run raises `IncompleteRun` (so Beat retries at +10 min, 3 times) when more than `max_stale_share` (20 %, `config/swing.yaml`) of the universe has no candle for the session. `store.snapshots.serving` skips a session holding under 80 % of the fullest recent session, so a partial day cannot hide a complete one. A run skipped for `no_universe` or `calendar_unavailable` now reports `technical.swing_snapshot` as failed. **Slack alert for it is a rule in tbo_platform (rule 13), not done here.**
- **9.5** Daily candles are read split-adjusted at warm-up. `services/evaluator.py` does not evaluate a stock whose first 5-minute open is more than 25 % from the previous close (`config/corporate_actions.py`: a split or bonus; intraday candles are not adjusted). Small ratios (5:4 bonus) still slip through; the gate's ex-date flag covers them at the decision engine. The days after the ex-date still carry pre-split prices in the 5-minute EMA, ATR and VWAP average until they age out (about one session).
- **9.6** The backtest report (now `apps/backtest_report.py`) applies the point-in-time gate when `TBO_TECHNICAL_FUNDAMENTAL_URL` is set (`--gate` / `--no-gate`); an ungated report says NOT GATED at the top. The cache fill (now `apps/fill_research_cache.py`, `services/cache_fill.py`) pages the feed's candle history into the cache, so 12 months do not need 12 months of uptime. **Still open in tbo_platform:** a named, writable volume shared by the signal worker and the research worker for `TBO_TECHNICAL_RESEARCH_CACHE_DIR` (no deploy file defines it).
- **9.7** Previous-session levels (pivots, Camarilla, CPR) and `prev_close` skip special sessions: a session counts only if it opens at 09:15 and its last bar starts at or after 15:00 (`indicators/levels.is_regular`); the previous regular session's levels carry over a Muhurat evening.
- **9.8** The daily warm-up reads 150 sessions (was 30), so the daily ATR of a restarted worker matches a long-running one and the backtest.
- **9.9** VWAP breakout needs its "bars below" in today's session. **9.10** Evidence is stored only from gated runs without overrides, so a narrow run cannot replace the broad one. **9.12** A research job does not start if it could run into the 09:00 blackout (it stays queued; the sweep re-sends), stops at the blackout if it overruns, and the backtest cap check is serialised by an advisory lock. **9.13** Indicator streams and cooldowns of stocks that left the universe are dropped (also on the REST universe path). **9.14** This file's pins are current; `numba` is a direct dependency. **9.11** Decision: the previous close for pivots and the gap is the last 5-minute close (15:25 bar), not NSE's weighted close; the manual chart review (gate 3) must use the same rule or compare on the 1d candle.
- **Not built (9.18):** the nightly live-versus-backtest comparison of the plan's TE5 "Quality" table. The tape test compares replay with the live worker on fixtures only. **9.19** The identity check stays as a guard; the 20 recorded sessions criterion stays open.
- **9.1 (outbox)** ported unchanged from the platform template fix (2.2): Redis down stops the sweep and counts nothing; only row faults (ValueError, TypeError, redis ResponseError) count, once per row per sweep; the request-path flush counts no attempt. `tests/test_outbox.py`.

## Review fixes (code review 2026-10-20, numbers 9.20 to 9.26)

- **9.20 memory.** `schemas/candle.Candle` is a slotted, immutable class (interned stock and timeframe, each price an
  integer coefficient and exponent that give back the exact `Decimal` the feed sent); `CandleMessage` (pydantic)
  validates the wire shape at the two boundaries (`Candle.from_message`). Measured: about 400 bytes a candle with the
  buffer's structures (was 1,570), 950,000 candles at 500 stocks = about 390 MB RSS (was 1.5 GB against the worker's
  1 GB limit). `tests/synthetic/test_memory.py` bounds it, `scripts/benchmark.py memory --stocks 500` measures it. The
  identity digests (candidates, indicators, study) are byte-equal to main's, and the replay benchmark is as fast.
- **9.21 tools in the image.** `session_report`, `fill_research_cache` and `backtest_report` live in
  `tbo_technical_engine.apps` (`python -m tbo_technical_engine.apps.<tool>`, in a container or with `uv run`);
  `scripts/` keeps thin wrappers and the dev tools (benchmark, identity check, swing review sheet).
  `backtest_report` reads its files from the settings (`TBO_TECHNICAL_STRATEGIES_FILE` and the rest). `tests/test_image.py`
  checks the COPY sources against `.dockerignore` and that the runbooks name modules, not script paths.
- **9.22 session hours.** `services/latency.regular_window` is the calendar's continuous window (a timing change moves
  it); a special session has none. Boundary rows, the gauges' `evaluation_session_day`, the market context and the
  session report follow it: a Muhurat session writes no rows (it was 75 rows of the whole universe absent) and is
  skipped by the clean-sessions streak; a delayed open starts at its own first boundary. The fresh warm-up is 15 minutes
  before the calendar's open (09:00 usually, 17:45 before an 18:00 special session). **Not changed:** the Beat swing
  snapshot stays Monday to Friday (whether a Saturday special session gets a snapshot is a decision), and the
  platform's alert rules still assume 09:15 to 15:30 on a delayed-open day.
- **9.23 one price scale.** The warm-up reads the raw daily closes of the intraday window, then the adjusted daily
  history; each session's factor (adjusted over raw close, `services/adjustment.py`) rescales that session's 5- and
  15-minute candles (prices to their own decimals, volume divided). The warm-up's history copy replaces a buffered
  candle of the same revision whose prices differ (`CandleBuffer.add(replace=True)`), so a long-running worker's
  buffer and daily candles move to the new scale; buffered intraday candles older than the window are dropped after a
  split. The cache fill does the same over its range, and a stock whose cached daily closes disagree with the new
  adjusted ones is dropped from the cache first (`research_cache_rebased`): refill it with `apps.fill_research_cache`.
  Test: a 1:2 split in the middle of a cache replays exactly like the unsplit history. If the feed has not applied an
  action yet (`is_adjustment_pending`) the factors are 1 and the opening-gap guard (9.5) still skips the ex-date.
- **9.25** every Redis client comes from `utils/redis.connect_redis` (connect 5 s, reply 30 s, health check 30 s;
  `config/redis.py`); a test refuses a bare `Redis.from_url`.
- **9.26** the boundary grace is at least `max_candle_age_seconds` (120 s), so a candle evaluated 31 to 120 s late is
  counted, not `absent`; a missing universe is asked for once a minute, not every 15 s tick; migration 0008 indexes
  `indicator_snapshot.computed_at` (concurrently). `evaluation_boundary.trade_date` was already indexed (0007).
- **Not done (Low):** 9.24 (a revised candle's earlier candidate is not withdrawn; needs a contract event), the
  `tbo_signals` gauge of buffered candles, 9.15's ingest-level retry test, 14.6 (Idempotency-Key as ULID).

## Open items

- **TE5 needs the owner's trading costs.** `config/costs.yaml` holds Zerodha's published intraday schedule (zerodha.com/charges, read
  2026-10-15, no "last updated" date on the page) as a placeholder; every result says `costs.status: placeholder`. Replace the figures
  with the broker actually used and set `status: confirmed`.
- **TE5 exit criterion is open:** no recorded candles exist yet (the research cache `.dev/research_cache` fills at warm-up once the feed
  runs). Run `python -m tbo_technical_engine.apps.backtest_report --from <date> --to <date>` when 12 months are there; it states the coverage it found.
- **TE5 contract change to decide:** serve the evidence summary and the extra result fields (`docs/te5-contract-proposal.md`).

- **Index data (ADR-019): built, not yet seen live.** The feed's index candles reach the context (see the TE4 row and the decision
  "Index source"). Open: run the feed's 12-month index backfill, fill the research cache (`python -m tbo_technical_engine.apps.fill_research_cache --indices
  nifty_50 --isins none`), re-run the TE5 `vwap_pivot` study with `require_index: true` (a `params_overrides` entry), then decide whether
  the live file sets `require_index: true` (a new `params_version`; the owner's call, not made here). Also open: TE6's sector-relative rank
  (the sector indices exist now) and CODE-CHANGES item 1 in tbo_plans.
- Gate 3 (chart review of 10 recorded sessions) and the TE4 exit criterion need recorded sessions; the replay tests use synthetic ones.

- **Trading calendar (reference data R5).** The kit's holiday file is gone; trading days and sessions come from the filings extractor
  (`GET /v1/market-calendar`, scope `filings:read`) through `TradingCalendar.from_service` (tbo_platform docs/calendar.md). Design here:
  `services/market_calendar.py` has `LiveCalendar`, a `TradingCalendar` whose data is swapped in place, so the supervisor, context service,
  snapshot service and latency gauges keep their constructors. It is rebuilt at start, hourly and on `tbo.filings.market_calendar.updated.v1`
  (consumer group `technical.calendar`); the API down answers from the 0600 last-good file (`TBO_TECHNICAL_CALENDAR_CACHE_FILE`), and with
  neither the calendar raises `CalendarUnknown`, which every caller already treats as "not ready" (the supervisor does not warm up, the
  snapshot job skips with `calendar_unavailable`); a market day is never assumed. A failed rebuild never replaces a working calendar by an
  empty one. Settings: `TBO_TECHNICAL_FILINGS_URL`, `TBO_TECHNICAL_CALENDAR_CACHE_FILE`. Needs in tbo_platform (routed by the coordinator):
  the grant `tbo_technical_engine -> tbo_filings_extractor_service: [filings:read]`, the two env lines in the service files, and a persistent
  path for the cache (`/app/data` is a tmpfs, so the fallback would not survive a restart).
- No `deploy/services/tbo_technical_engine.yml` in tbo_platform yet (env: `TBO_TECHNICAL_MARKET_FEED_URL`, `..._FUNDAMENTAL_URL`, Redis ACL
  read access to `tbo.market_feed.*` and `tbo.fundamental.universe`).
  The index stream `tbo.market_feed.index_candle` falls under `tbo.market_feed.*`.

## TE6 decisions (where the plan was silent)

- **Relative strength has no benchmark yet.** The plan ranks the 20- and 60-day return against Nifty and the sector index.
  Every stock is measured against the same Nifty, so subtracting it cannot change the order: the percentile of the plain
  returns *is* the Nifty-relative percentile. The sector index would change the order; the feed has sector index candles since ADR-019
  (2026-10-20) but the rank does not read them yet, so it is left out;
  reading them needs one subtraction per stock and each stock's sector index.
  `rs_percentile` is the weighted mean of the 20-day and 60-day return ranks (0.5 / 0.5), each rank 0-100 with ties counted half.
- **Trend** (`swing/trend.py`): six bullish conditions (close above the 20 and 50 EMA, 20 above 50, 50 above 200, 50 EMA rising,
  +DI above -DI) are weighted to 100 points; a condition that cannot be evaluated yet (the 200 EMA before 200 sessions)
  drops out and the rest are rescaled. `up` at 65 or more, `down` at 35 or less, else `sideways`; **ADX below 20 forces `sideways`**
  (the averages are always ordered one way, so alone they call a range-bound market a trend half the time). `trend_score` is the
  strength of the call, `|points - 50| * 2`, scaled down when ADX is under 30: an `up` of 71 and a `down` of 71 are equally convincing.
- **Levels:** swing highs and lows (3 sessions each side, so the last 3 sessions are never pivots) clustered within 0.5 ATR with at
  least 2 touches, plus the 52-week and prior-calendar-month extremes as a 0.25 ATR zone on the inner side. Kind by the zone's
  middle against the close; nearest first; at most 12.
- **Volatility:** `atr_pct_percentile` over the last 252 sessions (neutral 50 under 20 points of history); contracting at or
  under the 25th percentile, expanding at or over the 75th; squeeze = Bollinger(20, 2) bandwidth at or under the 20th percentile of the
  last 126 sessions (needs 60).
- **Liquidity:** `avg_turnover_20d` is the mean of close x volume over 20 sessions from the **unadjusted** daily candles (an adjusted
  close times a raw volume understates it after a split, and the gate would call a busy stock illiquid), so each stock costs two
  daily reads. The spread is the Corwin-Schultz high-low estimator over 20 pairs of adjusted sessions (negative pair estimates count as
  0; null under 10 usable pairs), because daily candles have no quotes; it reads high on volatile stocks and is a coarse tier input.
- **Setups are long only** (the plan names a breakout from a base, a pullback in an uptrend and a 52-week high breakout; no short
  version is described). Stop, T1 and T2 come from the rule and the risk: R multiples 1.5 / 3 (base), 1.5 / 2.5 (pullback), 2 / 4
  (52-week). A breakout is the **first** close above the level, so a stock that keeps climbing is reported once. The stop is
  clamped to 4 ATR from the entry and a stop closer than 0.5 ATR gives no setup. One setup per stock per session; when several fire the
  52-week breakout wins, then the base breakout, then the pullback. The pullback enters one tick above the day's high (a buy stop).
  A setup is valid for 3 sessions after the one it was found after (`valid_until_date`, from the trading calendar). Every number is
  in `config/swing.yaml` and untested on real data; tune it through the backtest (TE5).
- **Snapshot runs.** 16:00 IST: snapshots as of today's close, plus setups. 08:00 IST: the same snapshots as of the previous
  session, announced only for stocks whose numbers changed (a stored hash of the record without `computed_at` decides), and no setup
  search. A repeated run therefore announces nothing twice. The 08:00 run uses the previous session's universe when today's is not
  out yet (the universe is built at 08:20). A stock with no candle for the session, or fewer than 61 sessions, gets no snapshot.
  If more than 20% of the universe cannot be loaded nothing is published and the task retries (3 times, 10 minutes apart): a rank over
  a partial universe would be quietly wrong.
- **`?date=` on the snapshot routes is the trade date served**, as the contract says: `?date=2026-10-16` answers with the snapshot whose
  `as_of_date` is the latest before the 16th. The session is chosen once per answer, so a page never mixes two days. Swing setups'
  `date` is the session they were found after (`setup_date`).
- The worker running the `technical.swing` queue must be started with that queue (`-Q ...,technical.swing`); there is no
  `deploy/services/tbo_technical_engine.yml` in tbo_platform yet, so nothing consumes it today.

## TE6 open items

- The exit criterion needs recorded data: snapshots for 500 stocks by 16:30 measured against the real feed, and a manual review of 30
  setups found on real history (the fixture sample is `docs/swing-review-sample.md`, regenerate with `uv run python -m scripts.swing_review_sheet`).
- Sector-index relative strength waits for an index source (the same gap as TE4).
- Collisions: TE5 touches `tasks.py` (its `run_backtest`) and the contract-backed API files only, not `swing/`; whichever of TE5 and TE6 merges second needs a small rebase of `tasks.py`.

## Code standards pass (2026-10-15, branch `claude/code-standards`)

- `settings.py` moved to `config/settings.py` and `tables.py` to `store/tables.py` (imports updated; no behaviour change).
- Docstrings that say why added across every package; `Any` removed where a real type exists (`**params: float`,
  `Redis`, `Row[Any]`, `CursorResult`). Left as `Any`, each with a reason: `CooldownStore.redis` (redis stubs cannot be
  awaited under strict mypy), JSON event/response dicts, and the Celery/FastAPI state objects.
- Dataclasses (`WarmupSpec`, `Bars`, `Context`, `IndexSeries`, ...) stay beside the logic that owns them; only pydantic
  models live in `schemas/`.
- Checks (2026-10-16): `make check` = ruff (lint, McCabe 10, docstrings), ruff format, mypy --strict on src and tests,
  bandit, pip-audit, pytest with an 80 percent coverage floor (94.7 percent at the time); `.pre-commit-config.yaml` runs
  ruff, ruff format and mypy. Found and fixed: 45 mypy errors in tests (generics, unused ignores, loose dict types), 9
  packages without a docstring, three comprehension nits. pip-audit: pytest 8.4.2 (PYSEC-2026-1845, dev-only) ignored
  with a reason because the fix needs pytest 9 and pytest-asyncio 1.x (fixed 2026-10-21: pytest 9.1.1, pytest-asyncio 1.4.0, no ignore). Bandit: B101 skipped (as ruff S101), B104 marked.

## Stock profile check (stage V1, 2026-10-18)

read-only, no code change. The fundamental engine is read through `GET /v1/universe` and `GET /v1/gate`, both unchanged by contracts 1.14.x. The technical snapshot gains an optional `return_12m_pct` in the contract (filled by stage T1, below). Nothing to build for V1.

## Stock profile T1: 12-month return (2026-10-18)

The daily snapshot carries `return_12m_pct`: the last close against the close 250 sessions earlier, from the same adjusted daily candles as the 20 and 60 day returns (`swing/analysis.return_12m`). With 250 sessions or fewer it is null, never 0 (a flat year is a real 0.00). The history read (400 calendar days, about 275 sessions) is enough. The contract pin is raised to 1.14.3 (`46d2aec`) and `contract/openapi.json` is re-vendored. Stored snapshots change their content hash once, so the next daily run re-announces them with the new field. Feeds the stock profile's peer comparison.

## Stock master check (stage S3c, 2026-10-20)

Read-only check, no code change. The engine keeps no stock master of its own and reads none: the universe and the gate come from the fundamental engine's API (`GET /v1/universe`, `GET /v1/gate`), candles and feed status from the market feed's API, and nothing reads a name, symbol, sector or index membership. Its tables (`indicator_snapshot`, `technical_snapshot`, `signal_candidate`, `swing_setup`, `backtest_trade` and the rest) are high-volume derived results, which the user_data plan (1a.6) lists as written exceptions to the `market_data` foreign keys. Nothing to switch in S3c or S3d. The index and India VIX producer work (market feed) is where this engine next reads shared market data.

## SonarQube strict fixes (2026-10-23)

From `reviews/2026-10-21-sonarqube-strict/tbo_technical_engine.md` and FIX-BRIEFS 2.10 (pytest 9 was already in PR #28).

- **Bind host from settings:** `TBO_TECHNICAL_BIND_HOST` (default `127.0.0.1`) and `..._BIND_PORT`; the image sets `0.0.0.0`.
- **Backtest code split, results unchanged:** `simulate` is `_fill`, `_manage` and `_trade` around a `_Position` (stop, targets,
  exit legs); `run_grid` is `_replay_stock` and `_over_budget`; `build_tape` has `_replay_day`; the report's `render` has
  `_caveats`, `_section` and `_folds`; the walk-forward choice is `_choose`. `run_grid` and `run_strategy` lost their unused
  `strategy` argument, and `run_study` takes a `StudyData` (stocks, loader, sessions, range) instead of five arguments. Checked
  with `scripts/identity_check.py --digests --stocks 4 --sessions 60`: the candidate, indicator and study digests equal `main`'s
  (950 candidates, 285 trades).
- **Live code split, same arithmetic:** ADX smoothing (`_smoothed`, `_add_dx`), Supertrend bands (`_carried`, `_direction`), the
  evaluator's entry window and cooldown (`_open_sides`), ORB's gap check, VWAP pivot's entry test, the swing trend checks
  (`_known`, `_state`), the fundamental client's paging (`_page`), the outbox flush (`_batch`, `_publish_one`, `_record_row_fault`).
- **Smaller items:** ISIN patterns use `\d` with `re.ASCII`; scope and signal names are constants; `_session_of` is synchronous;
  `NoIndexSource` names `IndexSource` as its base; `may_evaluate` (unused, tool scan 9.5.1) removed; one `except ValueError` in
  paging; tests no longer compare an expression with itself, name the exception they expect, and skip with `assume`.
- virtualenv 21.14.13 (CVE-2026-113722, dev-only through pre-commit).
- **Left for SonarQube to accept:** `S7503` on the three consumer handlers in `consumers/handlers.py` (the kit's consumer awaits
  every handler, and these three do synchronous work). Not in this change: the test-style hits `S9073` and `S5778`, deferred by
  Bala on 2026-10-21.
