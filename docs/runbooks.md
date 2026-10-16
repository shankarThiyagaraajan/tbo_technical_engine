# Runbooks: signal worker (TE8)

Each section is what to do when the alert of the same name fires. Everything below was rehearsed as a drill in
`tests/drills/` against a real PostgreSQL and Redis; the tests say what the engine does, and these pages say what to do.

First look, always: `uv run python -m scripts.session_report --days 1` and the `evaluation_boundary` rows of today.

## TechnicalEvaluationsMissed

Rows of the last 10 minutes have a `missed` count. Look at which column is non-zero.

- `inactive` for every stock: warm-up is pending. Normal for about a minute after a feed outage or at 09:00; see
  *Feed outage*. If it lasts, check the warm-up: log lines `warmup_done` / `warmup_failed` and the feed's REST health.
- `cold` for some stocks: those stocks' history is short (a new listing, a feed gap). The next warm-up retries them.
- `stale`: candles arrived more than 120 s late. The worker is behind: check the stream backlog
  (`XINFO GROUPS lab.tbo.market_feed.candle`: `lag`) and CPU on the host; see *Latency high*.
- `failed`: an evaluator raised. Search the log for `evaluator_failed` (it names the evaluator and the exception class);
  the candle is stored and will not be evaluated again, so fix the cause before the next boundary.
- `absent`: the feed did not publish those candles. Check the feed's status event and `tbo_market_feed_*` metrics; the
  feed's gap refill repairs REST history, and the next warm-up reads it.

## TechnicalLatencyHigh

p95 over 2 s at the latest boundaries. The batch of one boundary queues behind one consumer, so latency grows with the
universe. In order: (1) is the host busy (a backtest on the research queue, a Celery swing job)? The research and swing
queues must not share the host's cores with the session when it is tight. (2) Look at `max_seconds` against `p50_seconds`: a
high max with a normal median is one slow stock or a stall (a database or Redis pause), a high median is throughput.
(3) `scripts/benchmark.py live --stocks <universe size>` on a scratch database tells you what the worker does by itself;
the benchmark's 500-stock p95 was 0.9 s on the build machine (ADR 0001), so a real figure far above it points at the feed,
the stream or schema validation in the consumer, not the strategies. Do not raise the 2 s target to quiet the alert.

## TechnicalNoBoundaryRecords

During market hours on a trading day no row has been written for 15 minutes (or none today). Either the signal worker is
not running or its database is unreachable.

1. Is the process up? Restart it (it keeps no state it cannot rebuild: it warms from REST history); see *Worker crash*.
2. Log lines `evaluation_boundary_write_failed` mean the database is the problem: the rows wait in memory (about 25
   sessions' worth) and are written when it is back, so the record is not lost, only late.
3. Both fine but no rows: the universe is empty (rows are only written for a universe of at least one stock). Check
   the fundamental engine's universe endpoint and the worker's `universe_unavailable` warnings.

## TechnicalGaugesStale

The API process stopped refreshing the gauges (log: `evaluation_gauges_refresh_failed`). The signal worker may be fine;
check the database from the API container, then restart the API. The other alerts mean nothing while this one fires.

## Feed outage

Meant to happen (drill: `test_feed_outage_…`): when the feed's status is not healthy the engine stops producing candidates
and the boundaries are recorded as `suspended` (not misses). When it is healthy again the engine waits for a fresh warm-up
(supervisor tick, up to 15 s plus the warm-up itself); candles in that window are stored and not evaluated, and are counted
as missed. Nothing to do unless the window is long. The session is not clean, by design.

## Redis restart

Meant to happen (drill: `test_redis_restart_…`): with persistence (the platform's Redis writes an append-only file) the
consumers log `consumer_error` for the length of the outage, then carry on from where their groups stopped, and nothing
is evaluated twice. Without its data (a lost volume or a flush) the groups are gone: the consumers recreate them
(`consumer_group_lost` in the log) and ask for a fresh warm-up, because candles published while the group was missing never
arrive. Candles of the recovery window are missed (visible in the record). The kit's own consumer retried a missing group
for ever; `consumers/handlers.py` fixes that for this worker.

## Worker crash

Meant to happen (drill: `test_worker_crash_…`): a replacement process claims the dead one's unacknowledged entries after the
consumer's `claim_idle_ms` (60 s), stores nothing twice (candidate IDs are derived from stock, strategy, side and candle),
and evaluates again after its own warm-up. Candles that arrived while nothing ran are read back over REST at warm-up, so
they are already stored when the stream redelivers them and are **not evaluated** (a backlog after a restart is not a
signal). The boundaries in between have no row, so the session report shows them as missing and the session is not clean.

What to do: restart the worker (or let the container manager do it); confirm `warmup_done` with `cold=0` in the log and a
row for the next boundary. The restart cost is the warm-up (REST history for the whole universe) plus up to one 15 s
supervisor tick.
