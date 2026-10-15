"""Tables, all in this component's own schema `technical`. Each change gets an Alembic migration."""

from __future__ import annotations

from sqlalchemy import Column, DateTime, Index, Integer, String, Table, Text
from tbo_platform_kit.db import component_metadata

from tbo_technical_engine.settings import SHORT_NAME

metadata = component_metadata(SHORT_NAME)

outbox = Table(
    "outbox",
    metadata,
    Column("event_id", String(26), primary_key=True),
    Column("stream", String(100), nullable=False),
    Column("payload", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("published_at", DateTime(timezone=True), nullable=True),
    # Publishing failures, per row, so one row Redis refuses never blocks the rest (outbox.flush).
    Column("attempts", Integer, nullable=False, server_default="0"),
    Column("last_error", String(500), nullable=True),
    Column("dead_at", DateTime(timezone=True), nullable=True),
)
Index("ix_outbox_unpublished", outbox.c.created_at, postgresql_where=outbox.c.published_at.is_(None))
