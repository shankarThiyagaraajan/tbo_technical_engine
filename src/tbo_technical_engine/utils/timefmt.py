"""The contract's timestamp text: RFC 3339 UTC with a trailing Z. Everything is stored in UTC; IST is for display."""

from __future__ import annotations

from datetime import UTC, datetime


def z(ts: datetime) -> str:
    """A datetime as contract text, whole seconds: `2026-10-15T04:55:00Z`."""
    return ts.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_z(text: str) -> datetime:
    """Parse a contract timestamp (`2026-10-15T04:55:00Z`, fractional seconds allowed) into an aware UTC datetime."""
    return datetime.fromisoformat(text).astimezone(UTC)
