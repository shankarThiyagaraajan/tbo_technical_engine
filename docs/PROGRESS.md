# tbo_technical_engine progress

Plan: `tbo_plans/tbo_technical_engine/PLAN.md` in the project's shared folder. What is built, what is not, decisions.

## Status

| Phase | State |
|---|---|
| TE0 Repository foundation | Built (branch `claude/technical-te0-te4-1mrk0g`). Generated from the tbo_platform service template at `bfaaabf`; the contract is vendored (`v1.2.0`); all 12 operations are protected stubs answering 501; Celery with the outbox sweep; research queue guarded by the kit's mode blackout; signal worker skeleton. Checked locally only (CI did not run). |
| TE1 Market feed integration | Built, tested against a fake feed over `httpx.MockTransport` and a real Redis (no real feed or Kite). `candle.closed`, `feed.status` and `universe.published` consumers; typed history client with cursor paging; Parquet research cache (write-through at warm-up); warm-up at start, 09:00 IST and after an outage; evaluation suspended while the feed is unhealthy. |
| TE2 Indicator library | Built. EMA/SMA, ATR, ADX, RSI, Supertrend, session VWAP and bands, classic/Camarilla pivots, CPR, opening range, volume average, relative strength; batch and streaming forms agree over a year of data; EMA/ATR/ADX/RSI match TA-Lib to 1e-6 (TA-Lib is a test-only dependency). Live `IndicatorEngine` (rebuilds on any non-append change), `indicator_snapshot` table (14-day retention, pruned 02:30 IST), `GET /v1/indicators/{isin}`. |
| TE3 Intraday strategy engine | Built. The five strategies (`ema_9_21_crossover`, `vwap_breakout`, `orb_15m`, `pivot_supertrend`, `vwap_pivot`), long and short, as pure rules over a context; parameters and `params_version` in `config/strategies.yaml`; entry windows (09:30-14:30 IST, ORB to 11:30) and per-strategy cooldowns (30 min; ORB once a session); cooldown checkpoint in Redis; candidates stored in `signal_candidate` and announced as `signal.candidate.v1` through the outbox; `GET /v1/signals`, `/v1/signals/{id}`, `/v1/strategies`. A replayed or restarted session gives identical candidates (tested). Not done: gate 3 (chart review of 10 recorded sessions) needs real recorded sessions. |
| TE4 Market context | Built, with one gap. Breadth over the universe (advancing/declining/unchanged against the previous close), data quality from the feed's health, CPR day class, index states (VWAP side, 15-minute trend) and the VIX change, published as `market.context.v1` every 5 minutes from 09:15 to 15:30 IST (once per boundary, stored in `market_context`); `GET /v1/context` and `/v1/context/history`. **The market feed has no index or VIX data**, so index states, VIX and CPR class are empty/null until an `IndexSource` exists; the code and tests for them run on fake index data. Exit criterion (5 sessions on time, matched by hand) needs live sessions. |
| TE6 Swing analysis and daily snapshot | Built (branch `claude/technical-te6-9kz86w`), tested on fixtures and a fake feed with a real PostgreSQL and Redis; no real feed or recorded history yet. `swing/` (pure): trend state and score, relative-strength percentile, support and resistance zones, ATR % and its one-year percentile, volatility regime, Bollinger squeeze, 20-day traded value and a Corwin-Schultz spread estimate, and three long-only setup detectors (base breakout, pullback to the 20 or 50 EMA, 52-week high breakout). `SnapshotService` runs at 16:00 IST (snapshots + setups) and 08:00 IST (snapshot refresh) as Celery tasks on the new `technical.swing` queue; announces `technical.snapshot.v1` and `swing.setup.v1` through the outbox; `GET /v1/snapshots`, `/v1/snapshots/{isin}`, `/v1/swing-setups`. Parameters in `config/swing.yaml`. 500 stocks are measured and ranked in about two seconds (the daily-candle fetch is the rest of the half hour). Exit criterion 2 (30 setups reviewed) is prepared as `docs/swing-review-sample.md` on fixtures; the review on recorded data waits for recorded sessions. |
| TE5, TE7, TE8 | Not built (TE5 needs the broker cost figures; it is on its own branch) |

## Decisions

- Pins: tbo_contracts tag `v1.2.0` and tbo_platform commit `bfaaabf` (no platform tag yet). The kit's own sources still pin
  tbo_contracts v1.1.0, so `[tool.uv] override-dependencies` forces v1.2.0; drop it when the kit moves.
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

## Open items

- **No index or VIX in the market feed contract (blocks the index half of TE4 and `vwap_pivot`'s index filter).** Options: the feed adds index
  and VIX instruments (a contract change, then an `IndexSource` over its REST API), or this service gets its own source. Not decided; not done
  here because the feed is another repository and a second Kite client is forbidden.
- Gate 3 (chart review of 10 recorded sessions) and the TE4 exit criterion need recorded sessions; the replay tests use synthetic ones.

- **The kit's NSE holiday file ships empty.** `TradingCalendar.load()` raises `CalendarUnknown` for 2026, so the supervisor cannot
  warm anything until the owner fills `nse_holidays.json` (or `TBO_NSE_HOLIDAYS_FILE`) from NSE's circular. Tests use their own calendar.
- No `deploy/services/tbo_technical_engine.yml` in tbo_platform yet (env: `TBO_TECHNICAL_MARKET_FEED_URL`, `..._FUNDAMENTAL_URL`, Redis ACL
  read access to `tbo.market_feed.*` and `tbo.fundamental.universe`).

## TE6 decisions (where the plan was silent)

- **Relative strength has no benchmark yet.** The plan ranks the 20- and 60-day return against Nifty and the sector index.
  Every stock is measured against the same Nifty, so subtracting it cannot change the order: the percentile of the plain
  returns *is* the Nifty-relative percentile. The sector index would change the order and is not available (the feed has
  no index data), so it is left out; the rank then needs one subtraction per stock when an index source exists.
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
- Collisions: the code standards PR (#1) moves `settings.py` to `config/` and `tables.py` to `store/`; TE6 adds fields to the first
  and two tables to the second, so whichever merges second needs a small rebase of those files. TE5 touches `tasks.py` (its
  `run_backtest`) and the contract-backed API files only, not `swing/`.
