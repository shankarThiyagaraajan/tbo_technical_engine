"""Tables, all in this component's own schema `technical`. Each change gets an Alembic migration."""

from __future__ import annotations

from sqlalchemy import JSON, Column, Date, DateTime, Index, Integer, Numeric, String, Table, Text
from tbo_platform_kit.db import component_metadata

from tbo_technical_engine.config.settings import SHORT_NAME

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

# One row per evaluated candle: the indicator values that existed when a strategy looked, for audit and for
# GET /v1/indicators/{isin}. `values` maps an indicator name to a decimal string, or null while it warms up.
indicator_snapshot = Table(
    "indicator_snapshot",
    metadata,
    Column("isin", String(12), primary_key=True),
    Column("timeframe", String(4), primary_key=True),
    Column("candle_start_at", DateTime(timezone=True), primary_key=True),
    Column("values", JSON, nullable=False),
    Column("backend", String(10), nullable=False),
    Column("computed_at", DateTime(timezone=True), nullable=False),
)

# Every candidate emitted. The ID is derived from (isin, strategy, side, candle), so the same candle evaluated twice
# cannot be stored twice.
signal_candidate = Table(
    "signal_candidate",
    metadata,
    Column("signal_id", String(26), primary_key=True),
    Column("isin", String(12), nullable=False),
    Column("strategy", String(60), nullable=False),
    Column("side", String(4), nullable=False),
    Column("timeframe", String(4), nullable=False),
    Column("candle_start_at", DateTime(timezone=True), nullable=False),
    Column("entry_low", Numeric(14, 2), nullable=False),
    Column("entry_high", Numeric(14, 2), nullable=False),
    Column("stop", Numeric(14, 2), nullable=False),
    Column("target_1", Numeric(14, 2), nullable=False),
    Column("target_2", Numeric(14, 2), nullable=False),
    Column("strength", Numeric(5, 4), nullable=False),
    Column("components", JSON, nullable=False),
    Column("indicator_values", JSON, nullable=False),
    Column("params_version", String(40), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
Index("ix_signal_candidate_created", signal_candidate.c.created_at.desc(), signal_candidate.c.signal_id.desc())
Index("ix_signal_candidate_isin", signal_candidate.c.isin, signal_candidate.c.created_at.desc())

# The market context every 5 minutes (one row per boundary: 09:15, 09:20, ... 15:30 IST).
market_context = Table(
    "market_context",
    metadata,
    Column("context_at", DateTime(timezone=True), primary_key=True),
    Column("trade_date", Date, nullable=False),
    Column("data", JSON, nullable=False),
    Column("data_quality", String(10), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
