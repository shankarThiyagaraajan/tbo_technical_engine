"""Indicator values as the API and the stored snapshot carry them."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class IndicatorSnapshot(BaseModel):
    """The values of one stock and timeframe at one closed candle. A value is a decimal string, or None while that
    indicator is still warming up."""

    model_config = ConfigDict(frozen=True)

    isin: str
    timeframe: str
    candle_start_at: datetime
    values: dict[str, str | None]
    backend: str = "numpy"
    computed_at: datetime
