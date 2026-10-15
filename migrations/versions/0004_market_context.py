"""TE4: the market context every 5 minutes.

Revision ID: 0004
Revises: 0003
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None
SCHEMA = "technical"


def upgrade() -> None:
    op.create_table(
        "market_context",
        sa.Column("context_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("data", sa.JSON, nullable=False),
        sa.Column("data_quality", sa.String(10), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("context_at", name="pk_market_context"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("market_context", schema=SCHEMA)
