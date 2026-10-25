"""Index `indicator_snapshot.computed_at`: the 02:30 IST prune deletes by it (review 9.26).

Built CONCURRENTLY outside the migration's transaction, so the signal worker keeps writing audit rows while it is built
(the table holds 14 days of rows). `IF NOT EXISTS` and the drop-first make a half-run (an invalid index) repairable by
running again (lesson 53).

Revision ID: 0008
Revises: 0007
"""

from __future__ import annotations

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None
SCHEMA = "technical"
INDEX = "ix_indicator_snapshot_computed_at"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {SCHEMA}.{INDEX}")  # an invalid one left by a half-run
        op.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {INDEX} ON {SCHEMA}.indicator_snapshot (computed_at)")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {SCHEMA}.{INDEX}")
