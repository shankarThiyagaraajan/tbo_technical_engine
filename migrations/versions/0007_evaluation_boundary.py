"""TE8: one row per 5-minute boundary with its evaluation latency and missed counts.

Revision ID: 0007
Revises: 0006
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None
SCHEMA = "technical"


def upgrade() -> None:
    op.create_table(
        "evaluation_boundary",
        sa.Column("boundary_at", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("expected", sa.Integer, nullable=False),
        sa.Column("evaluated", sa.Integer, nullable=False),
        sa.Column("stale", sa.Integer, nullable=False),
        sa.Column("cold", sa.Integer, nullable=False),
        sa.Column("inactive", sa.Integer, nullable=False),
        sa.Column("failed", sa.Integer, nullable=False),
        sa.Column("absent", sa.Integer, nullable=False),
        sa.Column("suspended", sa.Boolean, nullable=False),
        sa.Column("p50_seconds", sa.Float, nullable=True),
        sa.Column("p95_seconds", sa.Float, nullable=True),
        sa.Column("max_seconds", sa.Float, nullable=True),
        sa.Column("written_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )
    op.create_index("ix_evaluation_boundary_trade_date", "evaluation_boundary", ["trade_date"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_table("evaluation_boundary", schema=SCHEMA)
