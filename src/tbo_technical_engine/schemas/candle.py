"""A closed candle as the market feed sends it (`candle.v1`), and the feed's health."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Timeframe = Literal["1m", "5m", "15m", "60m", "1d", "1w"]
FeedState = Literal["connecting", "healthy", "stale", "down"]


class Candle(BaseModel):
    """One closed candle. Its key is (isin, timeframe, start_at); a correction arrives with a higher `revision`.

    Responses are open (the feed may add fields), so extra fields are ignored rather than rejected.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    isin: str
    timeframe: Timeframe
    start_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int = Field(ge=0)
    revision: int = Field(ge=1, default=1)

    @property
    def key(self) -> tuple[str, str, datetime]:
        """What identifies a candle: stock, timeframe and start; the revision says which copy of it is newest."""
        return (self.isin, self.timeframe, self.start_at)


class FeedStatus(BaseModel):
    """`feed.status.v1`: `healthy` is the only state in which candidates may be produced."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    state: FeedState
    changed_at: datetime
    reason: str | None = None
    last_tick_at: datetime | None = None


class UniverseSnapshot(BaseModel):
    """The tradable universe for a date, as `universe.published` or `GET /v1/universe` give it."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    trade_date: str
    universe_version: int = Field(ge=1)
    isins: tuple[str, ...]
