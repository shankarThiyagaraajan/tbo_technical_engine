"""Records as the API writes them."""

from __future__ import annotations

from typing import Any

from tbo_technical_engine.schemas.signal import SignalCandidate
from tbo_technical_engine.utils.timefmt import z


def candidate_dict(c: SignalCandidate) -> dict[str, Any]:
    """A candidate as the API returns it: `model_dump` writes datetimes as `+00:00`, the contract wants `Z`
    (`2026-10-15T04:05:00Z`), so the two time fields are re-rendered."""
    data = c.model_dump(mode="json")
    data["candle_start_at"] = z(c.candle_start_at)
    data["created_at"] = z(c.created_at)
    return data
