repo: shankarThiyagaraajan/tbo_technical_engine
level: macro

## What changed
SonarQube strict fixes (PR #33): the API binds to `TBO_TECHNICAL_BIND_HOST` / `TBO_TECHNICAL_BIND_PORT` (default 127.0.0.1:8000; the image sets 0.0.0.0); the backtest, indicator, strategy, outbox and evaluator functions over the complexity limit are split with identical identity-check digests; `run_study` takes a `StudyData`; ISIN patterns match ASCII digits only; virtualenv 21.14.13 for its new advisory.

## Why
Review fix: the SonarQube "TBO strict" list for this repository, and every flagged vulnerability fixed.

## Action needed
None. The image keeps listening on 0.0.0.0; a process started outside the image now listens on 127.0.0.1 unless `TBO_TECHNICAL_BIND_HOST` is set.
