repo: shankarThiyagaraajan/tbo_technical_engine
level: sub

## What changed
The market context now has index states, India VIX and the CPR day class from the market feed's index candles (ADR-019). A new consumer (`technical.indices`) reads `tbo.market_feed.index_candle.closed.v1` into `FeedIndexSource`; the first context of a day and any index whose events stopped read `GET /v1/index-candles` instead, and the index list comes from `GET /v1/indices`. The index VWAP is the feed's time-weighted `session_vwap`. An index with no candles is left out (never guessed) and counted by the new signal `technical.index_missing`; `technical.index_reads` counts the REST reads; the worker label `indices` joins `technical.worker`. TE5 can apply `vwap_pivot`'s index filter in a backtest from cached nifty_50 candles (`scripts/fill_research_cache.py --indices nifty_50`), and the report says "Index filter off" when it could not. tbo-contracts pinned at 1.30.0. `require_index` stays false.

## Why
TE4's index half and the VWAP and pivot strategy's index filter had no data; the feed is now the index producer.

## Action needed
None to deploy beyond the image. The signal worker needs Redis read access to `tbo.market_feed.index_candle` (inside `tbo.market_feed.*`) and the existing `market_feed:read` grant. After the feed's index backfill: fill the research cache with `--indices nifty_50` and re-run the `vwap_pivot` study with `require_index: true`.
