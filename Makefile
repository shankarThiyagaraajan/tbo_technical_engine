# One command for every standard: `make check`. CI is manual-only, so this is what runs before a push.
# Tests need PostgreSQL and Redis (TBO_TEST_DATABASE_URL, TBO_TEST_REDIS_URL; see compose.dev.yml).

.PHONY: check lint format-check types security audit test hooks

check: lint format-check types security audit test

lint:
	uv run ruff check --quiet .

format-check:
	uv run ruff format --check --quiet .

types:
	uv run mypy --no-error-summary

security:
	uv run bandit -q -c pyproject.toml -r src

# Known-vulnerability check of the locked dependencies, dev tools included. No advisory is ignored: a flagged
# package is upgraded (owner's rule, 2026-10-21).
audit:
	uv export -q --frozen --no-emit-project --no-hashes --no-emit-package tbo-contracts --no-emit-package tbo-platform-kit --no-emit-package tbo-signals -o .audit-requirements.txt
	uv run pip-audit -r .audit-requirements.txt --no-deps --disable-pip \
		|| (rm -f .audit-requirements.txt; exit 1)
	rm -f .audit-requirements.txt

# Coverage floor (fail_under in pyproject.toml) is 80 percent.
test:
	uv run pytest -q --disable-warnings --cov --cov-report=term-missing:skip-covered

# Install the git hook that runs ruff, ruff format and mypy on each commit.
hooks:
	uv run pre-commit install
