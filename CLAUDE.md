# tbo_technical_engine

Turns market-feed candles into trade candidates, market context and technical snapshots. Part of TBO; plans in the project's shared folder `tbo_plans/` (start with `INDEX.md`).

**Read `docs/PROGRESS.md` first**, and keep it and this repository's row in `tbo_plans/PROGRESS.md` current in the
same change as the code.

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

## Check before pushing

```sh
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

## Layout

`src/tbo_technical_engine/`: `api/` routes (`stub.py` = 501 answers, removed as phases land) · `schemas/` pydantic (requests,
internal records) · `clients/` (market feed, fundamental engine) · `consumers/` (`candle.closed`, `feed.status`) ·
`indicators/` · `strategies/` · `context/` · `services/` · `store/` · `utils/` · `apps/` (process entry points:
`signal_worker`) · `tasks.py` (Celery) · `main.py` (api). `config/` holds `strategies.yaml`. `tests/` mirrors the package.

## CI is not run

GitHub Actions minutes are used up (owner's rule, 2026-10-15, all TBO repositories). The workflow stays but is manual-only:
do not trigger it and do not wait on it. Run the checks above locally and say in each PR that CI did not run.
Tests need PostgreSQL and Redis (`TBO_TEST_DATABASE_URL`, `TBO_TEST_REDIS_URL`; see `compose.dev.yml`).
