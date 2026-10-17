"""Derived IDs: valid ULIDs, stable, time-ordered, and signal IDs unchanged by the shared helper."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, date, datetime

from tbo_technical_engine.utils.ids import CROCKFORD, derived_id, setup_id, signal_id

ULID = re.compile(r"^[0-7][0-9A-HJKMNP-TV-Z]{25}$")
START = datetime(2026, 10, 15, 4, 0, tzinfo=UTC)
CLOSE = datetime(2026, 10, 15, 4, 5, tzinfo=UTC)


def test_signal_ids_are_what_they_were_before_the_helper_was_shared() -> None:
    digest = hashlib.sha256(f"INE002A01018|orb_15m|buy|{START.isoformat()}".encode()).digest()[:10]
    number = (int(CLOSE.timestamp() * 1000) << 80) | int.from_bytes(digest, "big")
    legacy = "".join(CROCKFORD[(number >> shift) & 31] for shift in range(125, -1, -5))
    assert signal_id("INE002A01018", "orb_15m", "buy", START, CLOSE) == legacy


def test_a_setup_id_is_a_ulid_that_depends_on_stock_type_and_session_only() -> None:
    found = datetime(2026, 10, 15, 10, 0, tzinfo=UTC)
    first = setup_id("INE002A01018", "base_breakout", date(2026, 10, 15), found)
    assert ULID.fullmatch(first)
    assert setup_id("INE002A01018", "base_breakout", date(2026, 10, 15), found) == first
    assert setup_id("INE002A01018", "ema_pullback", date(2026, 10, 15), found) != first
    assert setup_id("INE040A01034", "base_breakout", date(2026, 10, 15), found) != first
    assert setup_id("INE002A01018", "base_breakout", date(2026, 10, 16), found) != first


def test_ids_sort_by_time() -> None:
    early = derived_id("k", datetime(2026, 10, 15, 4, 0, tzinfo=UTC))
    late = derived_id("k", datetime(2026, 10, 15, 4, 1, tzinfo=UTC))
    assert early < late
