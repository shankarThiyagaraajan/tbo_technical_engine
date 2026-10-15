# tbo_technical_engine progress

Plan: `tbo_plans/tbo_technical_engine/PLAN.md` in the project's shared folder. What is built, what is not, decisions.

## Status

| Phase | State |
|---|---|
| TE0 Repository foundation | Built (branch `claude/technical-te0-te4-1mrk0g`). Generated from the tbo_platform service template at `bfaaabf`; the contract is vendored (`v1.2.0`); all 12 operations are protected stubs answering 501; Celery with the outbox sweep; research queue guarded by the kit's mode blackout; signal worker skeleton. Checked locally only (CI did not run). |
| TE1 Market feed integration | Built, tested against a fake feed over `httpx.MockTransport` and a real Redis (no real feed or Kite). `candle.closed`, `feed.status` and `universe.published` consumers; typed history client with cursor paging; Parquet research cache (write-through at warm-up); warm-up at start, 09:00 IST and after an outage; evaluation suspended while the feed is unhealthy. |
| TE2 Indicator library | Built. EMA/SMA, ATR, ADX, RSI, Supertrend, session VWAP and bands, classic/Camarilla pivots, CPR, opening range, volume average, relative strength; batch and streaming forms agree over a year of data; EMA/ATR/ADX/RSI match TA-Lib to 1e-6 (TA-Lib is a test-only dependency). Live `IndicatorEngine` (rebuilds on any non-append change), `indicator_snapshot` table (14-day retention, pruned 02:30 IST), `GET /v1/indicators/{isin}`. |
| TE3 Intraday strategy engine | Not started |
| TE4 Market context | Not started |
| TE5 to TE8 | Out of scope for the current build (TE5 needs the broker cost figures) |

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

## Open items

- **The kit's NSE holiday file ships empty.** `TradingCalendar.load()` raises `CalendarUnknown` for 2026, so the supervisor cannot
  warm anything until the owner fills `nse_holidays.json` (or `TBO_NSE_HOLIDAYS_FILE`) from NSE's circular. Tests use their own calendar.
- No `deploy/services/tbo_technical_engine.yml` in tbo_platform yet (env: `TBO_TECHNICAL_MARKET_FEED_URL`, `..._FUNDAMENTAL_URL`, Redis ACL
  read access to `tbo.market_feed.*` and `tbo.fundamental.universe`).
