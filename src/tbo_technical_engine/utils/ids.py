"""A signal's (or a setup's) ID is a function of what it is, so the same candle evaluated twice (a redelivered event, a
replay, a retried job) gives the same record and the database refuses the second copy."""

from __future__ import annotations

import hashlib
from datetime import date, datetime

CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def derived_id(key: str, at: datetime) -> str:
    """A valid ULID: 48 bits of `at` (so IDs sort by time) and 80 bits of a hash of `key`."""
    millis = int(at.timestamp() * 1000)
    digest = hashlib.sha256(key.encode()).digest()[:10]
    number = (millis << 80) | int.from_bytes(digest, "big")
    return "".join(CROCKFORD[(number >> shift) & 31] for shift in range(125, -1, -5))


def signal_id(isin: str, strategy: str, side: str, candle_start_at: datetime, candle_close_at: datetime) -> str:
    """The ID of a candidate, from (stock, strategy, side, candle); the time part is the candle's close."""
    return derived_id(f"{isin}|{strategy}|{side}|{candle_start_at.isoformat()}", candle_close_at)


def setup_id(isin: str, setup_type: str, setup_date: date, found_at: datetime) -> str:
    """The ID of a swing setup, from (stock, type, session): found again by a retried job, it is the same setup."""
    return derived_id(f"{isin}|{setup_type}|buy|{setup_date.isoformat()}", found_at)
