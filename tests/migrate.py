"""Alembic for the tests: `uv run python -m tbo_platform_kit.testing.run tests.migrate upgrade head` (P19).

The kit's runner installs the test profile and refuses a set `TBO_MODE`; this module then runs Alembic as
`alembic <args>` would. One difference, a workaround for kit 3.0.0: `run_migrations` checks the connection with
`require_test_database` (P22), whose SELECT leaves a transaction open, so Alembic does not begin its own and migration
0008's `autocommit_block` (CREATE INDEX CONCURRENTLY) fails with `assert self._transaction is not None`. The check is
wrapped here to end that transaction; the guard itself is unchanged and still refuses a real database. Remove the
wrapper when the kit commits after its own check, and run through `alembic` directly again.
"""

from __future__ import annotations

import sys

from alembic.config import main
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from tbo_platform_kit import guards

_check = guards.require_test_database


async def _check_then_end_the_transaction(conn: AsyncConnection | AsyncEngine) -> str:
    name = await _check(conn)
    if isinstance(conn, AsyncConnection):
        await conn.rollback()
    return name


guards.require_test_database = _check_then_end_the_transaction

if __name__ == "__main__":
    main(sys.argv[1:], prog="alembic")
