"""Opaque cursors: base64url JSON of the last key returned. A cursor that does not decode is a 422, not a 500."""

from __future__ import annotations

import base64
import json
from typing import Any

from tbo_platform_kit.problems import Problem


def encode(payload: list[Any]) -> str:
    """Turn the last row's sort key into an opaque cursor string."""
    return base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")


def decode(cursor: str | None) -> list[Any] | None:
    """The key inside a cursor; None when no cursor was given, a 422 Problem when it is not one of ours."""
    if cursor is None:
        return None
    try:
        value = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        if isinstance(value, list):
            return value
    except (ValueError, UnicodeDecodeError):
        pass
    raise Problem(
        "validation_failed",
        "The cursor is not valid.",
        errors=[{"field": "cursor", "message": "not a cursor from this API"}],
    )
