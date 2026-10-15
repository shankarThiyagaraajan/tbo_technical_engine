"""A signal's ID is a function of what it is, so the same candle evaluated twice (a redelivered event, a replay) gives
the same candidate and the database refuses the second copy."""

from __future__ import annotations

import hashlib
from datetime import datetime

CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def signal_id(isin: str, strategy: str, side: str, candle_start_at: datetime, candle_close_at: datetime) -> str:
    """A valid ULID: 48 bits of the candle's close time (so IDs sort by time) and 80 bits of a hash of the key."""
    millis = int(candle_close_at.timestamp() * 1000)
    digest = hashlib.sha256(f"{isin}|{strategy}|{side}|{candle_start_at.isoformat()}".encode()).digest()[:10]
    number = (millis << 80) | int.from_bytes(digest, "big")
    return "".join(CROCKFORD[(number >> shift) & 31] for shift in range(125, -1, -5))
