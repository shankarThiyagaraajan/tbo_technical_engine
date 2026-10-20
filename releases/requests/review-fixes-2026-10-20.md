repo: shankarThiyagaraajan/tbo_technical_engine
level: macro

## What changed
Review fixes of 2026-10-20 (area 9). The candle is a compact slotted record, so the signal worker's buffer at 500 stocks needs about 390 MB instead of 1.5 GB (9.20); the operating tools (`session_report`, `fill_research_cache`, `backtest_report`) run in the image as `python -m tbo_technical_engine.apps.<tool>` (9.21); boundary rows, market context and the warm-up follow the calendar's session, so a Muhurat session is no longer a day of absent evaluations and a delayed open moves the first boundary (9.22); intraday candles are rescaled to the adjusted daily scale after a split, in the buffer and the research cache, and a cache written before a split is dropped instead of mixed (9.23); Redis clients have connect and reply timeouts (9.25); the boundary grace covers the evaluated candle age, the universe is asked for once a minute after a miss, and `indicator_snapshot.computed_at` is indexed (9.26). certifi 2026.10.19 (GHSA-6q48-6c7w-9gw2).

## Why
The worker was going to be OOM-killed after about six sessions at 500 stocks, the runbooks named scripts the image did not have, and special sessions and splits gave false alarms and mixed price scales.

## Action needed
Run the migrations (`alembic upgrade head`, 0008 builds an index concurrently). Use `python -m tbo_technical_engine.apps.session_report` (and the other two tools) in the containers; `scripts/` still works from a checkout. After a split, refill the stock's research cache with `apps.fill_research_cache` (the worker logs `research_cache_rebased`).
