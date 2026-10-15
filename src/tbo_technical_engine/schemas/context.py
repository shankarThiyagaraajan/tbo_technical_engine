"""The market context as `tbo.technical.market.context.v1` and `GET /v1/context` carry it."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class IndexState(BaseModel):
    model_config = ConfigDict(frozen=True)

    index_code: str
    last_price: str
    vwap: str | None
    is_above_vwap: bool
    trend_15m: Literal["up", "down", "sideways"]


class Vix(BaseModel):
    model_config = ConfigDict(frozen=True)

    level: str
    change_pct: str


class Breadth(BaseModel):
    model_config = ConfigDict(frozen=True)

    advancing: int = Field(ge=0)
    declining: int = Field(ge=0)
    unchanged: int = Field(ge=0)


class MarketContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    context_at: str
    trade_date: str
    index_states: list[IndexState]
    vix: Vix | None
    breadth: Breadth
    cpr_class: Literal["narrow", "normal", "wide"] | None
    data_quality: Literal["ok", "degraded"]
