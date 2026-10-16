"""Tables, all in this component's own schema `technical`. Each change gets an Alembic migration."""

from __future__ import annotations

from sqlalchemy import JSON, Column, Date, DateTime, Index, Integer, Numeric, String, Table, Text, UniqueConstraint
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

# The daily snapshot of one stock (TE6): `data` is the contract's technical_snapshot record. `content_hash` is the hash
# of that record without `computed_at`, so the 08:00 refresh publishes only the stocks whose snapshot changed and a
# retried job publishes nothing twice.
technical_snapshot = Table(
    "technical_snapshot",
    metadata,
    Column("as_of_date", Date, primary_key=True),
    Column("isin", String(12), primary_key=True),
    Column("data", JSON, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

# One swing setup per stock per session (the detectors' precedence picks it). The ID is derived, so a retry is a no-op.
swing_setup = Table(
    "swing_setup",
    metadata,
    Column("setup_id", String(26), primary_key=True),
    Column("isin", String(12), nullable=False),
    Column("setup_date", Date, nullable=False),
    Column("setup_type", String(40), nullable=False),
    Column("side", String(4), nullable=False),
    Column("entry", Numeric(14, 2), nullable=False),
    Column("stop", Numeric(14, 2), nullable=False),
    Column("target_1", Numeric(14, 2), nullable=False),
    Column("target_2", Numeric(14, 2), nullable=True),
    Column("valid_until_date", Date, nullable=False),
    Column("params_version", String(40), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("isin", "setup_date", name="uq_swing_setup_isin_date"),
)
Index("ix_swing_setup_date", swing_setup.c.setup_date, swing_setup.c.isin)

# TE5: backtest runs, their trades, and the per-strategy evidence the decision engine attaches to a call.
backtest = Table(
    "backtest",
    metadata,
    Column("backtest_id", String(26), primary_key=True),
    Column("status", String(10), nullable=False),
    Column("strategy", String(60), nullable=False),
    Column("params_version", String(40), nullable=False),
    Column("params_overrides", JSON, nullable=False),
    Column("from_date", Date, nullable=False),
    Column("to_date", Date, nullable=False),
    Column("isins", JSON, nullable=False),
    Column("metrics", JSON, nullable=True),
    # Everything else a run produces: coverage, costs schedule and status, settings used, walk-forward, stats.
    Column("result", JSON, nullable=True),
    Column("code_version", String(60), nullable=False),
    Column("backend", String(10), nullable=False),
    Column("error", String(500), nullable=True),
    Column("requested_by", String(100), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=True),
    Column("finished_at", DateTime(timezone=True), nullable=True),
)
Index("ix_backtest_status", backtest.c.status)
Index("ix_backtest_created", backtest.c.created_at.desc(), backtest.c.backtest_id.desc())

backtest_trade = Table(
    "backtest_trade",
    metadata,
    Column("backtest_id", String(26), primary_key=True),
    Column("trade_no", Integer, primary_key=True),
    Column("isin", String(12), nullable=False),
    Column("side", String(4), nullable=False),
    Column("entry_at", DateTime(timezone=True), nullable=False),
    Column("exit_at", DateTime(timezone=True), nullable=False),
    Column("entry_price", Numeric(14, 2), nullable=False),
    Column("exit_price", Numeric(14, 2), nullable=False),
    Column("exit_reason", String(20), nullable=False),
    Column("r_multiple", Numeric(12, 4), nullable=False),
    Column("gross_r", Numeric(12, 4), nullable=False),
    Column("quantity", Integer, nullable=False),
    Column("costs", Numeric(14, 2), nullable=False),
)

# The latest evidence per (strategy, params_version): written when a backtest at the file's own parameters (no
# overrides) succeeds. One row, replaced, so the decision engine always reads the newest.
strategy_evidence = Table(
    "strategy_evidence",
    metadata,
    Column("strategy", String(60), primary_key=True),
    Column("params_version", String(40), primary_key=True),
    Column("backtest_id", String(26), nullable=False),
    Column("data", JSON, nullable=False),
    Column("computed_at", DateTime(timezone=True), nullable=False),
)
