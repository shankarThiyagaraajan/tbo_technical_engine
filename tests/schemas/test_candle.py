"""The compact `Candle` (review 9.20): the same values as the pydantic model it replaces, in a quarter of the memory."""

from __future__ import annotations

import copy
import pickle
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from tbo_technical_engine.schemas.candle import Candle, CandleMessage

WIRE: dict[str, Any] = {
    "isin": "INE002A01018",
    "timeframe": "5m",
    "start_at": "2026-10-15T03:45:00Z",
    "open": "1245.50",
    "high": "1251",
    "low": "1.244E+3",
    "close": "1249.850",
    "volume": 182340,
    "revision": 2,
    "open_interest": None,
}


def test_prices_come_back_with_the_digits_and_exponent_they_arrived_with() -> None:
    c = Candle.from_message(WIRE)
    message = CandleMessage.model_validate(WIRE)
    for name in ("open", "high", "low", "close"):
        assert getattr(c, name).as_tuple() == getattr(message, name).as_tuple()
        assert str(getattr(c, name)) == str(getattr(message, name))
    assert (c.isin, c.timeframe, c.start_at, c.volume, c.revision) == (
        "INE002A01018",
        "5m",
        datetime(2026, 10, 15, 3, 45, tzinfo=UTC),
        182340,
        2,
    )
    assert float(c.close) == 1249.85


def test_the_wire_form_is_still_validated() -> None:
    with pytest.raises(ValidationError):
        Candle.from_message({**WIRE, "volume": -1})
    with pytest.raises(ValidationError):
        Candle.from_message({**WIRE, "timeframe": "7m"})
    with pytest.raises(ValidationError):
        Candle.from_message({**WIRE, "close": "NaN"})
    infinite = {**Candle.from_message(WIRE).as_dict(), "close": Decimal("Infinity")}
    with pytest.raises(ValueError, match="finite"):
        Candle(**infinite)


def test_equal_by_value_hashable_and_immutable() -> None:
    a = Candle.from_message(WIRE)
    b = Candle.from_message({**WIRE, "open": "1245.5"})
    assert a == b  # 1245.50 == 1245.5, as the pydantic model compared them
    assert hash(a) == hash(b)
    assert a != Candle.from_message({**WIRE, "close": "1249.90"})
    assert a != Candle.from_message({**WIRE, "revision": 3})
    assert len({a, b}) == 1
    with pytest.raises(AttributeError):
        a.close = Decimal("1")  # type: ignore[misc]
    with pytest.raises(AttributeError):
        setattr(a, "extra", 1)  # noqa: B010  (the point: any attribute is refused)


def test_copies_pickles_and_dumps_like_the_model() -> None:
    a = Candle.from_message(WIRE)
    assert pickle.loads(pickle.dumps(a)) == a
    assert copy.deepcopy(a) == a
    corrected = a.model_copy(update={"close": Decimal("1250.00"), "revision": 3})
    assert (corrected.close, corrected.revision, corrected.open) == (Decimal("1250.00"), 3, a.open)
    assert a.model_dump(mode="json") == CandleMessage.model_validate(WIRE).model_dump(mode="json")
    assert Candle.from_message(a.model_dump(mode="json")) == a
    assert "close=Decimal('1249.850')" in repr(a)


def test_stock_and_timeframe_strings_are_shared_between_candles() -> None:
    a = Candle.from_message(WIRE)
    b = Candle.from_message({**WIRE, "start_at": "2026-10-15T03:50:00Z"})
    assert a.isin is b.isin
    assert a.timeframe is b.timeframe
