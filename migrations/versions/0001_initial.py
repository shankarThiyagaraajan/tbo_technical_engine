"""The first tables: the kit's (processed events, idempotency keys), the outbox.

Revision ID: 0001
Revises:
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from tbo_platform_kit.db import NAMING_CONVENTION, kit_tables

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None
SCHEMA = "technical"


def upgrade() -> None:
    # The kit's tables as of kit 1.x (a change to them is a kit major release with its own migration note).
    kit = sa.MetaData(schema=SCHEMA, naming_convention=NAMING_CONVENTION)
    kit_tables(kit)
    kit.create_all(op.get_bind())

    op.create_table(
        "outbox",
        sa.Column("event_id", sa.String(26), primary_key=True),
        sa.Column("stream", sa.String(100), nullable=False),
        sa.Column("payload", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(500), nullable=True),
        sa.Column("dead_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_outbox_unpublished",
        "outbox",
        ["created_at"],
        schema=SCHEMA,
        postgresql_where=sa.text("published_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_table("outbox", schema=SCHEMA)
    op.drop_table("kit_idempotency_keys", schema=SCHEMA)
    op.drop_table("kit_processed_events", schema=SCHEMA)
