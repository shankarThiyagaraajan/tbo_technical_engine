"""What a synthetic market is made of: stocks, sessions and the seed, so the same spec always gives the same candles."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field


class SyntheticSpec(BaseModel):
    """A reproducible synthetic market. `seed` plus the stock's index fix every price, so two machines agree."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    stocks: int = Field(ge=1, le=2000)
    sessions: int = Field(ge=1, le=800)
    end: date = Field(description="The last session (inclusive); earlier sessions are the previous weekdays.")
    seed: int = 7
    start_price: float = Field(default=500.0, gt=1)


def isin_of(index: int) -> str:
    """A well-formed fake ISIN for stock number `index` (`INE000000A00`...): `IN`, nine alphanumerics, a digit."""
    return f"INE{index:06d}A0{index % 10}"
