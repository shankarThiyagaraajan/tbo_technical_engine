"""A closed candle as the market feed sends it (`candle.v1`), an index candle (`index_candle.v1`), and the feed's
health."""

from __future__ import annotations

import sys
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, NoReturn

from pydantic import BaseModel, ConfigDict, Field

Timeframe = Literal["1m", "5m", "15m", "60m", "1d", "1w"]
FeedState = Literal["connecting", "healthy", "stale", "down"]


class CandleMessage(BaseModel):
    """A candle as it arrives (`candle.closed` event data, a `GET /v1/candles` item), validated. Only the boundary
    uses it: `Candle.from_message` turns it into the compact `Candle` the engine keeps.

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


_UNITS: dict[int, Decimal] = {}
"""`Decimal` 1 at each exponent seen (`-2` -> `0.01`): coefficient times unit is the exact price again, digits and
exponent as they arrived (`100.50` stays `100.50`). Bounded by the few exponents prices have."""


def _pack(price: Decimal | int | float | str) -> tuple[int, int]:
    """A price as (coefficient, exponent): `Decimal("101.25")` -> `(10125, -2)`. Exact; a non-finite price raises.
    Anything else is read through its text, as pydantic read it (`101.2` -> `Decimal("101.2")`)."""
    exact = price if isinstance(price, Decimal) else Decimal(str(price))
    sign, digits, exponent = exact.as_tuple()
    if not isinstance(exponent, int):
        raise ValueError(f"a candle price must be finite, got {exact}")
    if exponent not in _UNITS:
        _UNITS[exponent] = Decimal((0, (1,), exponent))
    coefficient = int("".join(map(str, digits)))
    return (-coefficient if sign else coefficient), exponent


class Candle:
    """One closed candle. Its key is (isin, timeframe, start_at); a correction arrives with a higher `revision`.

    Kept compact because the signal worker holds about 950,000 of them at 500 stocks (review 9.20): slots, the stock
    and timeframe strings interned, and each price stored as an integer coefficient and an exponent instead of a
    `Decimal` object. Reading `open`, `high`, `low` or `close` gives the same `Decimal` the feed sent (same digits,
    same exponent), so every result computed from a candle is unchanged. About 330 bytes a candle, against 1,570 as
    a pydantic model. Immutable and hashable, like the frozen model it replaces.
    """

    __slots__ = (
        "_c",
        "_ce",
        "_h",
        "_he",
        "_l",
        "_le",
        "_o",
        "_oe",
        "isin",
        "revision",
        "start_at",
        "timeframe",
        "volume",
    )
    isin: str
    timeframe: str
    start_at: datetime
    volume: int
    revision: int
    _o: int
    _oe: int
    _h: int
    _he: int
    _l: int
    _le: int
    _c: int
    _ce: int

    def __init__(
        self,
        *,
        isin: str,
        timeframe: str,
        start_at: datetime,
        open: Decimal,
        high: Decimal,
        low: Decimal,
        close: Decimal,
        volume: int,
        revision: int = 1,
    ) -> None:
        put = object.__setattr__
        put(self, "isin", sys.intern(isin))
        put(self, "timeframe", sys.intern(timeframe))
        put(self, "start_at", start_at)
        put(self, "volume", volume)
        put(self, "revision", revision)
        for name, price in (("_o", open), ("_h", high), ("_l", low), ("_c", close)):
            coefficient, exponent = _pack(price)
            put(self, name, coefficient)
            put(self, name + "e", exponent)

    @classmethod
    def from_message(cls, data: Mapping[str, Any]) -> Candle:
        """Validate a candle as the feed sends it and keep it compact; raises pydantic's `ValidationError`."""
        m = CandleMessage.model_validate(data)
        return cls(
            isin=m.isin,
            timeframe=m.timeframe,
            start_at=m.start_at,
            open=m.open,
            high=m.high,
            low=m.low,
            close=m.close,
            volume=m.volume,
            revision=m.revision,
        )

    @property
    def open(self) -> Decimal:
        """The opening price, exactly as sent."""
        return Decimal(self._o) * _UNITS[self._oe]

    @property
    def high(self) -> Decimal:
        """The highest price, exactly as sent."""
        return Decimal(self._h) * _UNITS[self._he]

    @property
    def low(self) -> Decimal:
        """The lowest price, exactly as sent."""
        return Decimal(self._l) * _UNITS[self._le]

    @property
    def close(self) -> Decimal:
        """The closing price, exactly as sent."""
        return Decimal(self._c) * _UNITS[self._ce]

    @property
    def key(self) -> tuple[str, str, datetime]:
        """What identifies a candle: stock, timeframe and start; the revision says which copy of it is newest."""
        return (self.isin, self.timeframe, self.start_at)

    def as_dict(self) -> dict[str, Any]:
        """The fields by name, prices as `Decimal` (the constructor's keywords)."""
        o, h, lo, c = self._prices()
        return {
            "isin": self.isin,
            "timeframe": self.timeframe,
            "start_at": self.start_at,
            "open": o,
            "high": h,
            "low": lo,
            "close": c,
            "volume": self.volume,
            "revision": self.revision,
        }

    def model_dump(self, *, mode: Literal["python", "json"] = "python") -> dict[str, Any]:
        """The candle as the pydantic model dumped it (`mode="json"` gives the wire form), so tools that publish a
        candle as an event keep working."""
        return CandleMessage.model_construct(**self.as_dict()).model_dump(mode=mode)

    def model_copy(self, *, update: Mapping[str, Any] | None = None) -> Candle:
        """A new candle with `update` applied (the pydantic model's way of making a corrected copy)."""
        return Candle(**{**self.as_dict(), **(update or {})})

    def _fields(self) -> tuple[str, str, datetime, int, int]:
        return (self.isin, self.timeframe, self.start_at, self.volume, self.revision)

    def _prices(self) -> tuple[Decimal, Decimal, Decimal, Decimal]:
        return (self.open, self.high, self.low, self.close)

    def __eq__(self, other: object) -> bool:
        """Equal fields and equal prices by value (`100.5 == 100.50`), as the pydantic model compared them."""
        if self is other:
            return True
        if not isinstance(other, Candle):
            return NotImplemented
        return self._fields() == other._fields() and self._prices() == other._prices()

    def __hash__(self) -> int:
        return hash(self._fields())

    def __repr__(self) -> str:
        o, h, lo, c = self._prices()
        return (
            f"Candle(isin={self.isin!r}, timeframe={self.timeframe!r}, start_at={self.start_at!r}, open={o!r}, "
            f"high={h!r}, low={lo!r}, close={c!r}, volume={self.volume!r}, revision={self.revision!r})"
        )

    def __reduce__(self) -> tuple[Any, ...]:
        """Pickle and copy through the constructor (the slots are read-only)."""
        return (_rebuild, (self.isin, self.timeframe, self.start_at, *self._prices(), self.volume, self.revision))

    def __setattr__(self, name: str, value: object) -> NoReturn:
        raise AttributeError(f"Candle is immutable; cannot set {name}")

    def __delattr__(self, name: str) -> NoReturn:
        raise AttributeError(f"Candle is immutable; cannot delete {name}")


def _rebuild(
    isin: str,
    timeframe: str,
    start_at: datetime,
    open: Decimal,
    high: Decimal,
    low: Decimal,
    close: Decimal,
    volume: int,
    revision: int,
) -> Candle:
    """The unpickling side of `Candle.__reduce__`."""
    return Candle(
        isin=isin,
        timeframe=timeframe,
        start_at=start_at,
        open=open,
        high=high,
        low=low,
        close=close,
        volume=volume,
        revision=revision,
    )


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
