"""`config/costs.yaml` as a typed record. Rates are Decimal, read from strings, so 0.03 is exactly 0.03."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CostSchedule(BaseModel):
    """Intraday equity charges. An unknown key is an error: a misspelled charge must not silently cost nothing."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schedule_version: str = Field(min_length=1, max_length=60)
    status: Literal["placeholder", "confirmed"]
    """`placeholder` until the owner replaces the figures; every backtest result carries it."""
    source: str = Field(min_length=1, max_length=300)
    brokerage_pct: Decimal = Field(ge=0)
    brokerage_cap: Decimal = Field(ge=0)
    stt_sell_pct: Decimal = Field(ge=0)
    exchange_txn_pct: Decimal = Field(ge=0)
    sebi_per_crore: Decimal = Field(ge=0)
    stamp_buy_pct: Decimal = Field(ge=0)
    ipft_per_crore: Decimal = Field(ge=0)
    gst_pct: Decimal = Field(ge=0)
