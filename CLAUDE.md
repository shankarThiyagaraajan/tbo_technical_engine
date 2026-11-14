# tbo_technical_engine

**Main session only, not subagents:** read `/home/shankar/LAB/Rust/TradeBOT/Docs/tbo_docs/docs/THREAD-RULES.md` once, for how to run this session. Where it differs, this file and `RULES.md` win, except that they can't lower its safety floor.

Turns market-feed candles into trade candidates, market context and technical snapshots. Part of TBO; plans in the project's shared folder `tbo_plans/` (start with `INDEX.md`).

**Read `docs/PROGRESS.md` first**, and keep it and this repository's row in `tbo_plans/PROGRESS.md` current in the
same change as the code.

**Read `docs/PROGRESS.md` before any work**, and update it in the same PR as every change, a docs-only one too (and this repository's row in `tbo_plans/PROGRESS.md`), so it never goes stale.

## Task files

Before work, read `../tbo_platform/RULES.md` (`services/tbo_platform/RULES.md` on Bala's laptop; [on GitHub](https://github.com/shankarThiyagaraajan/tbo_platform/blob/main/RULES.md)), this repository's `TASKS.md` and the common `../tbo_platform/TASKS.md` ([on GitHub](https://github.com/shankarThiyagaraajan/tbo_platform/blob/main/TASKS.md)). Set the task's status and result when you stop.

## Layout

`src/tbo_technical_engine/`: `api/` routes (`stub.py` = 501 answers, removed as phases land) · `schemas/` pydantic (requests,
internal records) · `config/` (`settings.py`: `Settings`, `REPOSITORY`, `SHORT_NAME`; no logic) · `clients/` (market feed, fundamental engine) · `consumers/` (`candle.closed`, `feed.status`) ·
`indicators/` · `strategies/` (the five rules) · `context/` (pure market-context functions, index source seam) · `swing/` (pure daily-candle analysis: trend, levels, volatility, liquidity, rank, setups) · `services/` (evaluator, signal runner, cooldowns, snapshot service) · `store/` (SQL; `tables.py` = the SQLAlchemy tables) · `utils/` · `apps/` (process entry points:
`signal_worker`) · `backtest/` (TE5, pure: `replay` runs candles through the live evaluator, `simulate` fills and exits, `costs`, `metrics`,
`walkforward`, `study` = one whole run, `stats` = vectorbt returns, `evidence`, `summary` = the Markdown report) · `tasks.py` (Celery: `run_backtest` on `technical.research`, `requeue_waiting_backtests`; Beat 16:00 and 08:00 IST run the swing snapshot on the `technical.swing` queue) · `main.py` (api). The top-level `config/` holds `strategies.yaml`, `swing.yaml`, `backtest.yaml` (caps, execution assumptions, walk-forward, grids) and `costs.yaml` (a placeholder schedule until the owner confirms it); the Dockerfile must copy them. `scripts/backtest_report.py` writes the report from the research cache. `synthetic/` (seeded synthetic market: candles, feed-format tick files, research-cache fill; only for benchmarks, drills and identity checks, never for results that claim anything about strategies; constants in `config/synthetic.py`) with `scripts/benchmark.py` (replay, sweep, live at 500 stocks) and `scripts/identity_check.py` (Numba on/off digests); ADRs in `docs/adr/`. `config/signals.yaml` + `services/platform_signals.py` (tbo_signals: the manifest, one `Signals` per process, `api/signal_manifest.py` serves `GET /v1/signal-manifest`; a signal is added in the manifest and one call), `services/market_calendar.py` (`LiveCalendar`: the trading calendar from the filings extractor, rebuilt hourly and on `market_calendar.updated`; unset `TBO_TECHNICAL_FILINGS_URL` = empty calendar), `services/latency.py` (the per-boundary evaluation record; `store/latency.py`, `services/latency_gauges.py`, `services/session_report.py`, `scripts/session_report.py`), `tests/drills/` (failure drills on a real PostgreSQL and Redis), runbooks in `docs/runbooks.md`, `docs/monitoring.md`. `tests/` mirrors the package.

## Check before pushing

**Which checks a change runs (the full rule is `../tbo_platform/RULES.md`, "Check levels"; read that file before work):** docs only: none. Small or medium, on any path (level B): lint, format, types, security and only the tests it touches, no SonarQube. Big (level C: a breaking change, or a large change on a risk path): the full `make check` below, once, on the final head. SonarQube scans `main` every night and a level C PR for information; no session waits for it. Say the level in the PR body.

```sh
make check
```

`make check` runs, in order (each is also its own target): `ruff check` (pycodestyle, pyflakes, isort, pyupgrade, bugbear,
simplify, comprehensions, pydocstyle for module/class/function/package docstrings, security, McCabe complexity 10) ·
`ruff format --check` · `mypy --strict` on `src` and `tests` · `bandit` on `src` · `pip-audit` on the locked dependencies
(through `uv export`; no advisory is ignored, a flagged package is upgraded) · `pytest` with an 80 percent coverage floor.
`make hooks` installs a pre-commit hook (ruff, ruff format, mypy). Config lives in `pyproject.toml`; do not silence a
rule inline without a reason next to it.

## Rules

- Contract first: an API or event change goes into `tbo_contracts` first, then `scripts/vendor_contract.sh`.
- Request bodies strict, responses open; errors are RFC 9457 with catalogue codes (`Problem`); timestamps RFC 3339
  UTC `Z`; IDs are ULIDs.
- Nothing is open by default: every route but health takes `requires(scope)`.
- Events go through the outbox (`outbox.add` in the change's transaction), never straight to Redis from a handler.
- Every Celery task is idempotent. Migrations stay backward compatible for one release.
- uv is the only Python tool: `pyproject.toml` + a committed `uv.lock`, `uv sync --frozen`, `uv run`, the `uv_build` backend; no pip, venv, poetry, pipenv, conda or requirements files.
- Never commit `.history/` (an editor's local history); it is in `.gitignore` and CI fails on a tracked one.
- Never commit a `.env*` file except `.env.example` (a skeleton without values), and ask the owner before committing any file that may hold sensitive data; CI fails on a tracked `.env*`.
- No secrets in the repository; settings come from `TBO_TECHNICAL_*` and `*_FILE`.

## CI is not run

CI is manual-only (`../tbo_platform/RULES.md`, "Checks and output"): do not trigger it and do not wait on it. Run the checks above locally and say in each PR that CI did not run.
Tests need PostgreSQL and Redis (`TBO_TEST_DATABASE_URL`, `TBO_TEST_REDIS_URL`; see `compose.dev.yml`).

## Output

`make check` is quiet on purpose (ruff `--quiet`, `mypy --no-error-summary`, `pytest -q --disable-warnings`); a failure still prints in full. Thresholds and tool lists are unchanged.

## Releases

See `../tbo_platform/RULES.md` "Releases". This repository is in the release watcher's list.
