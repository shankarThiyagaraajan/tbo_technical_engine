"""A closed candle as the market feed sends it (`candle.v1`), an index candle (`index_candle.v1`), and the feed's
health."""

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


class IndexCandle(BaseModel):
    """`index_candle.v1`: one closed 5m, 15m or 1d candle of an index or India VIX (ADR-019), keyed by
    (index_code, timeframe, start_at); a correction arrives with a higher `revision`.

    An index has no volume, so the feed sends `session_vwap`: the session's time-weighted mean of the 5-minute typical
    price up to this candle. Responses are open, so extra fields are ignored.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    index_code: str
    timeframe: Literal["5m", "15m", "1d"]
    start_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    session_vwap: Decimal | None = None
    revision: int = Field(ge=1, default=1)
    source: str = "historical"
    """`historical` (Kite's candle API) or `quote` (built from quote polls when history is refused)."""

    @property
    def key(self) -> tuple[str, str, datetime]:
        """What identifies an index candle: index, timeframe and start."""
        return (self.index_code, self.timeframe, self.start_at)

    def as_candle(self) -> Candle:
        """The same bar as a `Candle` (the index code in place of an ISIN, volume 0), so the indicators read it."""
        return Candle(
            isin=self.index_code,
            timeframe=self.timeframe,
            start_at=self.start_at,
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=0,
            revision=self.revision,
        )


class IndexInfo(BaseModel):
    """One row of the feed's `GET /v1/indices`: an index it knows and whether it polls it."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    index_code: str
    name: str
    kind: str
    is_tracked: bool
    last_candle_at: datetime | None = None


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
