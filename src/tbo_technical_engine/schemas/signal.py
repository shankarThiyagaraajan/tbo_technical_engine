"""A signal candidate as `tbo.technical.signal.candidate.v1` carries it, and the strategy drafts it is made from."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict


class Component(BaseModel):
    """One condition of a strategy as evaluated. `value` is the number behind it, if there is one."""

    model_config = ConfigDict(frozen=True)

    name: str
    is_met: bool
    value: str | None = None


class SignalCandidate(BaseModel):
    """Prices and ratios are decimal strings, as in the contract. Not a call: the decision engine scores it."""

    model_config = ConfigDict(frozen=True)

    signal_id: str
    isin: str
    strategy: str
    side: Literal["buy", "sell"]
    timeframe: Literal["5m"]
    candle_start_at: datetime
    entry_low: str
    entry_high: str
    stop: str
    target_1: str
    target_2: str
    strength: str
    components: list[Component]
    indicator_values: dict[str, str | None]
    params_version: str
    created_at: datetime


@dataclass(frozen=True)
class Draft:
    """What a strategy decides for one candle and side, before ids, versions and times are added. Prices are already on
    the tick grid (`strategies.pricing.finish`)."""

    side: Literal["buy", "sell"]
    entry_low: Decimal
    entry_high: Decimal
    stop: Decimal
    target_1: Decimal
    target_2: Decimal
    strength: Decimal
    components: tuple[Component, ...]
    indicators: dict[str, float]
