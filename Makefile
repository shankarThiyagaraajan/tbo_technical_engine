# One command for every standard: `make check`. CI is manual-only, so this is what runs before a push.
# Tests need PostgreSQL and Redis (TBO_TEST_DATABASE_URL, TBO_TEST_REDIS_URL; see compose.dev.yml).

.PHONY: check lint format-check types security audit test hooks

check: lint format-check types security audit test

lint:
	uv run ruff check .

format-check:
	uv run ruff format --check .

types:
	uv run mypy

security:
	uv run bandit -q -c pyproject.toml -r src

# Known-vulnerability check of the locked dependencies. PYSEC-2026-1845 (pytest <= 9.0.2, shared /tmp/pytest-of-USER
# directory on multi-user hosts) is ignored on purpose: pytest is a dev-only dependency, never in the image, and the
# fix (pytest 9) needs pytest-asyncio 1.x, which is a separate change. Drop the ignore when pytest moves to 9.
audit:
	uv export -q --frozen --no-emit-project --no-hashes -o .audit-requirements.txt
	uv run pip-audit -r .audit-requirements.txt --no-deps --disable-pip --ignore-vuln PYSEC-2026-1845 \
		|| (rm -f .audit-requirements.txt; exit 1)
	rm -f .audit-requirements.txt

# Coverage floor (fail_under in pyproject.toml) is 80 percent.
test:
	uv run pytest --cov --cov-report=term-missing:skip-covered

# Install the git hook that runs ruff, ruff format and mypy on each commit.
hooks:
	uv run pre-commit install
