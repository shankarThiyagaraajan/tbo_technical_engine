# tbo_technical_engine

Turns market-feed candles into trade candidates, market context and technical snapshots.

Generated from the tbo_platform service template (bfaaabf54981055950ae03bad9ed14c384c83c9c); `copier update` adopts a later release.

```sh
uv sync
docker compose -f compose.dev.yml up -d --wait
export TBO_TEST_DATABASE_URL=postgresql+asyncpg://tbo:tbo@127.0.0.1:55432/tbo_dev TBO_TEST_REDIS_URL=redis://127.0.0.1:56379/0
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

- Tooling: uv is the only Python tool (a TBO rule): dependencies in `pyproject.toml`, `uv.lock` committed, every command through `uv run`; no pip, venv, poetry, pipenv, conda or requirements files.
- Contract: `contract/openapi.json` is the bundled contract of `tbo_contracts` at the ref in `contract/source.json`;
  `scripts/vendor_contract.sh` rewrites it. The app's routes must be exactly the contract's (a test checks it).
- Settings: `TBO_TECHNICAL_*` (see `src/tbo_technical_engine/config/settings.py` and the kit's `ComponentSettings`).
- Monitoring: Prometheus scrapes `/metrics` once `deploy/prometheus/targets/technical.metrics.json` is in
  tbo_platform; errors go to this repository's Sentry or GlitchTip project when `TBO_TECHNICAL_SENTRY_DSN_FILE` is set.
- Migrations: `alembic upgrade head` (schema `technical`). Worker: `celery -A tbo_technical_engine.tasks worker`.
