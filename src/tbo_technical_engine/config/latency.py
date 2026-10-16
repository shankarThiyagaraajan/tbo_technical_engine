"""Constants of the evaluation-latency record (TE8). Targets come from the plan; the rest are how the record is kept."""

from __future__ import annotations

from datetime import time, timedelta

TARGET_P95_SECONDS = 2.0
"""Plan, TE8: candle close to candidate, p95 under 2 seconds."""
BOUNDARY_GRACE = timedelta(seconds=30)
"""How long after a boundary's close it stays open for late candles before its row is written. The kit's consumer
reclaims an entry nobody acknowledged after 60 s, so a candle can legitimately arrive later than this: it is stored
and counted `stale` against its boundary only if the row has not been written yet, and otherwise not at all (it was
already counted as `absent`)."""
FIRST_CLOSE = time(9, 20)
"""IST close of the first 5-minute candle of the session (09:15 to 09:20)."""
LAST_CLOSE = time(15, 30)
"""IST close of the last 5-minute candle of the session."""
BOUNDARY_MINUTES = 5
GAUGE_REFRESH_SECONDS = 15
"""How often the API process re-reads today's rows to refresh its Prometheus gauges."""
CLEAN_SESSIONS_REQUIRED = 10
"""Plan, TE8 exit criterion: ten consecutive sessions without a missed evaluation."""

Outcome = str
EVALUATED: Outcome = "evaluated"
STALE: Outcome = "stale"
COLD: Outcome = "cold"
INACTIVE: Outcome = "inactive"
FAILED: Outcome = "failed"
