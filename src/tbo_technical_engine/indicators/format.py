"""Indicator values as the contract writes them: a Decimal string, or null while the indicator is warming up."""

from __future__ import annotations

import math


def decimal_text(value: float, places: int = 6) -> str | None:
    """`24.6` for 24.600000001, None for nan or infinity. Never an exponent, never `-0` (the Decimal pattern allows
    neither a bare `.5` nor `1e-07`)."""
    if not math.isfinite(value):
        return None
    text = f"{value:.{places}f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def decimals(values: dict[str, float]) -> dict[str, str | None]:
    """`decimal_text` applied to a name -> value map (an indicator snapshot)."""
    return {name: decimal_text(v) for name, v in values.items()}
