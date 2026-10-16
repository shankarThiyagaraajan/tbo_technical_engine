"""TE6: the daily technical snapshot and the swing setups.

Revision ID: 0005
Revises: 0004
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None
SCHEMA = "technical"


def upgrade() -> None:
    op.create_table(
        "technical_snapshot",
        sa.Column("as_of_date", sa.Date, nullable=False),
        sa.Column("isin", sa.String(12), nullable=False),
        sa.Column("data", sa.JSON, nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("as_of_date", "isin", name="pk_technical_snapshot"),
        schema=SCHEMA,
    )
    op.create_table(
        "swing_setup",
        sa.Column("setup_id", sa.String(26), nullable=False),
        sa.Column("isin", sa.String(12), nullable=False),
        sa.Column("setup_date", sa.Date, nullable=False),
        sa.Column("setup_type", sa.String(40), nullable=False),
        sa.Column("side", sa.String(4), nullable=False),
        sa.Column("entry", sa.Numeric(14, 2), nullable=False),
        sa.Column("stop", sa.Numeric(14, 2), nullable=False),
        sa.Column("target_1", sa.Numeric(14, 2), nullable=False),
        sa.Column("target_2", sa.Numeric(14, 2), nullable=True),
        sa.Column("valid_until_date", sa.Date, nullable=False),
        sa.Column("params_version", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("setup_id", name="pk_swing_setup"),
        sa.UniqueConstraint("isin", "setup_date", name="uq_swing_setup_isin_date"),
        schema=SCHEMA,
    )
    op.create_index("ix_swing_setup_date", "swing_setup", ["setup_date", "isin"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_index("ix_swing_setup_date", table_name="swing_setup", schema=SCHEMA)
    op.drop_table("swing_setup", schema=SCHEMA)
    op.drop_table("technical_snapshot", schema=SCHEMA)
