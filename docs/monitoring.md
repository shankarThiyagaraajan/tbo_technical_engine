# Monitoring the signal worker (TE8)

## What is measured

For every 5-minute boundary of a trading day (09:20 to 15:30 IST, 75 of them) the signal worker writes one row to
`technical.evaluation_boundary`, 30 seconds after the boundary closed. It says, for the universe of that moment:

| Column | Meaning |
|---|---|
| `evaluated` | Candles evaluated by every evaluator. |
| `stale` | Arrived more than `TBO_TECHNICAL_MAX_CANDLE_AGE_SECONDS` (120) after their close: stored, not evaluated. |
| `cold` | The stock's history was not warm. |
| `inactive` | Warm-up pending (the open, or after an outage), or the feed was down. |
| `failed` | An evaluator raised (logged as `evaluator_failed`). |
| `absent` | No candle arrived within the 30 s grace period. |
| `suspended` | The feed was not healthy when the row was written: silence was correct, so none of the row counts as missed. |
| `p50_seconds`, `p95_seconds`, `max_seconds` | Candle close to the end of evaluation, over the evaluated candles. |

**Missed** = `stale + cold + inactive + failed + absent`, and 0 for a suspended boundary.

Latency is measured on the worker's clock against the candle's close time, so it includes the feed's time to build and
publish the candle, the stream and the queue behind the single consumer. It is the figure the plan's "candle close to
candidate, p95 under 2 s" means, measured where it counts, on a real session. (`scripts/benchmark.py live` measures only
the part inside the worker.)

A boundary with **no row** means no worker was running at its close.

## The ten-session exit criterion

```sh
docker compose exec technical_worker python -m tbo_technical_engine.apps.session_report --days 10   # exit 0 only with ten clean sessions in a row
uv run python -m tbo_technical_engine.apps.session_report --days 10                                # the same from a checkout
```

A session is clean when all of its boundaries (75 from 09:15 to 15:30; fewer when the calendar moves the open) have a
row, none has a missed evaluation and none has a p95 over 2 s. Boundaries with the feed down do not count against it. The plan's criterion (ten consecutive sessions without a missed evaluation) is
**pending until real market sessions have run**; nothing here can satisfy it on synthetic data. A special session (a
Muhurat hour) has no boundary rows and is skipped by the streak, neither breaking nor extending it (review 9.22).

## Prometheus

The signal worker has no HTTP port. The API process, which Prometheus scrapes, re-reads today's rows every 15 s and sets:

| Metric | Meaning |
|---|---|
| `tbo_technical_evaluation_latency_p95_seconds` | p95 at the latest boundary. |
| `tbo_technical_evaluation_latency_max_seconds` | Slowest evaluation at the latest boundary. |
| `tbo_technical_evaluation_boundaries_today_count` | Boundary rows today. |
| `tbo_technical_evaluation_missed_today_count` | Missed evaluations today (a gauge that grows through the day). |
| `tbo_technical_evaluation_slow_boundaries_today_count` | Boundaries today with p95 over 2 s. |
| `tbo_technical_evaluation_last_record_age_seconds` | Seconds since the newest row was written; -1 when there is none today. |
| `tbo_technical_evaluation_session_day_count` | 1 on a trading day. |
| `tbo_technical_evaluation_gauges_refreshed_seconds` | Unix time of the last refresh. |

Alert rules, their promtool tests and the dashboard panels live in `tbo_platform` (`deploy/prometheus/rules/technical.yml`,
`deploy/grafana/make_dashboards.py`); the runbook per alert is `docs/runbooks.md` here. Alerts: `TechnicalEvaluationsMissed`,
`TechnicalLatencyHigh`, `TechnicalNoBoundaryRecords`, `TechnicalGaugesStale`.
