"""TE3: the signal candidates.

Revision ID: 0003
Revises: 0002
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None
SCHEMA = "technical"


def upgrade() -> None:
    op.create_table(
        "signal_candidate",
        sa.Column("signal_id", sa.String(26), nullable=False),
        sa.Column("isin", sa.String(12), nullable=False),
        sa.Column("strategy", sa.String(60), nullable=False),
        sa.Column("side", sa.String(4), nullable=False),
        sa.Column("timeframe", sa.String(4), nullable=False),
        sa.Column("candle_start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("entry_low", sa.Numeric(14, 2), nullable=False),
        sa.Column("entry_high", sa.Numeric(14, 2), nullable=False),
        sa.Column("stop", sa.Numeric(14, 2), nullable=False),
        sa.Column("target_1", sa.Numeric(14, 2), nullable=False),
        sa.Column("target_2", sa.Numeric(14, 2), nullable=False),
        sa.Column("strength", sa.Numeric(5, 4), nullable=False),
        sa.Column("components", sa.JSON, nullable=False),
        sa.Column("indicator_values", sa.JSON, nullable=False),
        sa.Column("params_version", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("signal_id", name="pk_signal_candidate"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_signal_candidate_created",
        "signal_candidate",
        [sa.text("created_at DESC"), sa.text("signal_id DESC")],
        schema=SCHEMA,
    )
    op.create_index("ix_signal_candidate_isin", "signal_candidate", ["isin", sa.text("created_at DESC")], schema=SCHEMA)


def downgrade() -> None:
    op.drop_table("signal_candidate", schema=SCHEMA)
