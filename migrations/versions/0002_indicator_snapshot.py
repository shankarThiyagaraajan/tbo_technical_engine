"""TE2: the indicator values at each evaluated candle.

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None
SCHEMA = "technical"


def upgrade() -> None:
    op.create_table(
        "indicator_snapshot",
        sa.Column("isin", sa.String(12), nullable=False),
        sa.Column("timeframe", sa.String(4), nullable=False),
        sa.Column("candle_start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("values", sa.JSON, nullable=False),
        sa.Column("backend", sa.String(10), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("isin", "timeframe", "candle_start_at", name="pk_indicator_snapshot"),
        schema=SCHEMA,
    )
    # The retention job deletes by age.
    op.create_index("ix_indicator_snapshot_computed_at", "indicator_snapshot", ["computed_at"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_table("indicator_snapshot", schema=SCHEMA)
