# tbo_technical_engine

Turns market-feed candles into trade candidates, market context and technical snapshots. Part of TBO; plans in the project's shared folder `tbo_plans/` (start with `INDEX.md`).

**Read `docs/PROGRESS.md` first**, and keep it and this repository's row in `tbo_plans/PROGRESS.md` current in the
same change as the code.

## Layout

`src/tbo_technical_engine/`: `api/` routes (`stub.py` = 501 answers, removed as phases land) · `schemas/` pydantic (requests,
internal records) · `config/` (`settings.py`: `Settings`, `REPOSITORY`, `SHORT_NAME`; no logic) · `clients/` (market feed, fundamental engine) · `consumers/` (`candle.closed`, `feed.status`) ·
`indicators/` · `strategies/` (the five rules) · `context/` (pure market-context functions, index source seam) · `swing/` (pure daily-candle analysis: trend, levels, volatility, liquidity, rank, setups) · `services/` (evaluator, signal runner, cooldowns, snapshot service) · `store/` (SQL; `tables.py` = the SQLAlchemy tables) · `utils/` · `apps/` (process entry points:
`signal_worker`) · `backtest/` (TE5, pure: `replay` runs candles through the live evaluator, `simulate` fills and exits, `costs`, `metrics`,
`walkforward`, `study` = one whole run, `stats` = vectorbt returns, `evidence`, `summary` = the Markdown report) · `tasks.py` (Celery: `run_backtest` on `technical.research`, `requeue_waiting_backtests`; Beat 16:00 and 08:00 IST run the swing snapshot on the `technical.swing` queue) · `main.py` (api). The top-level `config/` holds `strategies.yaml`, `swing.yaml`, `backtest.yaml` (caps, execution assumptions, walk-forward, grids) and `costs.yaml` (a placeholder schedule until the owner confirms it); the Dockerfile must copy them. `scripts/backtest_report.py` writes the report from the research cache. `synthetic/` (seeded synthetic market: candles, feed-format tick files, research-cache fill; only for benchmarks, drills and identity checks, never for results that claim anything about strategies; constants in `config/synthetic.py`) with `scripts/benchmark.py` (replay, sweep, live at 500 stocks) and `scripts/identity_check.py` (Numba on/off digests); ADRs in `docs/adr/`. `config/signals.yaml` + `services/platform_signals.py` (tbo_signals: the manifest, one `Signals` per process, `api/signal_manifest.py` serves `GET /v1/signal-manifest`; a signal is added in the manifest and one call), `services/market_calendar.py` (`LiveCalendar`: the trading calendar from the filings extractor, rebuilt hourly and on `market_calendar.updated`; unset `TBO_TECHNICAL_FILINGS_URL` = empty calendar), `services/latency.py` (the per-boundary evaluation record; `store/latency.py`, `services/latency_gauges.py`, `services/session_report.py`, `scripts/session_report.py`), `tests/drills/` (failure drills on a real PostgreSQL and Redis), runbooks in `docs/runbooks.md`, `docs/monitoring.md`. `tests/` mirrors the package.

## Check before pushing

```sh
make check
```

`make check` runs, in order (each is also its own target): `ruff check` (pycodestyle, pyflakes, isort, pyupgrade, bugbear,
simplify, comprehensions, pydocstyle for module/class/function/package docstrings, security, McCabe complexity 10) ·
`ruff format --check` · `mypy --strict` on `src` and `tests` · `bandit` on `src` · `pip-audit` on the locked dependencies
(through `uv export`; the one ignored advisory has its reason in the Makefile) · `pytest` with an 80 percent coverage floor.
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

GitHub Actions minutes are used up (owner's rule, 2026-10-15, all TBO repositories). The workflow stays but is manual-only:
do not trigger it and do not wait on it. Run the checks above locally and say in each PR that CI did not run.
Tests need PostgreSQL and Redis (`TBO_TEST_DATABASE_URL`, `TBO_TEST_REDIS_URL`; see `compose.dev.yml`).

## Output

`make check` is quiet on purpose (ruff `--quiet`, `mypy --no-error-summary`, `pytest -q --disable-warnings`); a failure still prints in full. Thresholds and tool lists are unchanged.

## Releases

Checks run locally (`make check`); GitHub's runner is used only for tags and releases (project rule 17). Versions are `vMAJOR.MINOR.PATCH`,
each number 0 to 50, starting at v0.0.0 until the first major release: `main` = breaking change, `sub` = notable change or feature,
`macro` = bug fix or review fix. A change of real size or type gets a release; a typo or tiny edit gets none. After such a merge, the
merging thread starts Actions -> Release -> Run workflow on `main` (or `workflow_dispatch` through the GitHub API) with the level and
three notes: what changed, why, and action needed (or None). Tags are made only by that workflow, never by hand (lesson 7).

Before you report a change done, use the `release-request` skill to decide whether it needs a release (a change of real size or type, see above; a docs-only change or a typo gets none). If it does, add `releases/requests/<name>.md` (`repo:`, `level:`, then What changed, Why, Action needed) and merge it to `main`; the laptop release watcher then makes the tag and release. Never tag by hand, and never say a release exists until you have seen it. Say in your thread what you decided: request filed with its level, or "no release" with the reason.
