"""Records as the API writes them."""

from __future__ import annotations

from typing import Any

from tbo_technical_engine.schemas.signal import SignalCandidate
from tbo_technical_engine.utils.timefmt import z


def candidate_dict(c: SignalCandidate) -> dict[str, Any]:
    data = c.model_dump(mode="json")
    data["candle_start_at"] = z(c.candle_start_at)
    data["created_at"] = z(c.created_at)
    return data
