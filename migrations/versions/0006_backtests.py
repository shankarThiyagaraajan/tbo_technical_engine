"""TE5: backtest runs, their trades and the per-strategy evidence.

Revision ID: 0006
Revises: 0005
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None
SCHEMA = "technical"


def upgrade() -> None:
    op.create_table(
        "backtest",
        sa.Column("backtest_id", sa.String(26), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("strategy", sa.String(60), nullable=False),
        sa.Column("params_version", sa.String(40), nullable=False),
        sa.Column("params_overrides", sa.JSON, nullable=False),
        sa.Column("from_date", sa.Date, nullable=False),
        sa.Column("to_date", sa.Date, nullable=False),
        sa.Column("isins", sa.JSON, nullable=False),
        sa.Column("metrics", sa.JSON, nullable=True),
        sa.Column("result", sa.JSON, nullable=True),
        sa.Column("code_version", sa.String(60), nullable=False),
        sa.Column("backend", sa.String(10), nullable=False),
        sa.Column("error", sa.String(500), nullable=True),
        sa.Column("requested_by", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("backtest_id", name="pk_backtest"),
        schema=SCHEMA,
    )
    op.create_index("ix_backtest_status", "backtest", ["status"], schema=SCHEMA)
    op.create_index(
        "ix_backtest_created",
        "backtest",
        [sa.text("created_at DESC"), sa.text("backtest_id DESC")],
        schema=SCHEMA,
    )
    op.create_table(
        "backtest_trade",
        sa.Column("backtest_id", sa.String(26), nullable=False),
        sa.Column("trade_no", sa.Integer, nullable=False),
        sa.Column("isin", sa.String(12), nullable=False),
        sa.Column("side", sa.String(4), nullable=False),
        sa.Column("entry_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("exit_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("entry_price", sa.Numeric(14, 2), nullable=False),
        sa.Column("exit_price", sa.Numeric(14, 2), nullable=False),
        sa.Column("exit_reason", sa.String(20), nullable=False),
        sa.Column("r_multiple", sa.Numeric(12, 4), nullable=False),
        sa.Column("gross_r", sa.Numeric(12, 4), nullable=False),
        sa.Column("quantity", sa.Integer, nullable=False),
        sa.Column("costs", sa.Numeric(14, 2), nullable=False),
        sa.PrimaryKeyConstraint("backtest_id", "trade_no", name="pk_backtest_trade"),
        schema=SCHEMA,
    )
    op.create_table(
        "strategy_evidence",
        sa.Column("strategy", sa.String(60), nullable=False),
        sa.Column("params_version", sa.String(40), nullable=False),
        sa.Column("backtest_id", sa.String(26), nullable=False),
        sa.Column("data", sa.JSON, nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("strategy", "params_version", name="pk_strategy_evidence"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("strategy_evidence", schema=SCHEMA)
    op.drop_table("backtest_trade", schema=SCHEMA)
    op.drop_table("backtest", schema=SCHEMA)
