from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from tbo_technical_engine.schemas.signal import Component, SignalCandidate
from tbo_technical_engine.store import signals as store
from tests.support import ISIN_A, ISIN_B

START = datetime(2026, 10, 15, 4, 25, tzinfo=UTC)
ID = "01JA8ZQ3W7K4N2C9V5T6B1M30A"


def candidate(signal_id: str = ID, isin: str = ISIN_A, strategy: str = "orb_15m", minutes: int = 0) -> SignalCandidate:
    return SignalCandidate(
        signal_id=signal_id,
        isin=isin,
        strategy=strategy,
        side="buy",
        timeframe="5m",
        candle_start_at=START + timedelta(minutes=minutes),
        entry_low="100.40",
        entry_high="100.50",
        stop="99.00",
        target_1="102.40",
        target_2="104.40",
        strength="0.7200",
        components=[Component(name="close_beyond_opening_range", is_met=True, value="100")],
        indicator_values={"or_high": "100", "vol_ratio_20": None},
        params_version="2026.10.1",
        created_at=START + timedelta(minutes=minutes + 5),
    )


async def test_save_and_get_round_trip(sessions: Any) -> None:
    async with sessions() as session, session.begin():
        assert await store.save(session, candidate()) is True
    async with sessions() as session:
        assert await store.get(session, ID) == candidate()
        assert await store.get(session, "01JA8ZQ3W7K4N2C9V5T6B1M30B") is None


async def test_the_same_id_is_stored_once(sessions: Any) -> None:
    async with sessions() as session, session.begin():
        assert await store.save(session, candidate()) is True
        assert await store.save(session, candidate()) is False


async def test_page_filters_orders_and_pages(sessions: Any) -> None:
    ids = [f"01JA8ZQ3W7K4N2C9V5T6B1M30{c}" for c in "ABCD"]
    async with sessions() as session, session.begin():
        await store.save(session, candidate(ids[0], minutes=0))
        await store.save(session, candidate(ids[1], minutes=5))
        await store.save(session, candidate(ids[2], isin=ISIN_B, minutes=10))
        await store.save(session, candidate(ids[3], strategy="vwap_pivot", minutes=10))
    day = (START - timedelta(hours=4), START + timedelta(hours=20))
    async with sessions() as session:
        every = await store.page(session, start=day[0], end=day[1], isin=None, strategy=None, after=None, limit=10)
        assert [c.signal_id for c in every] == [ids[3], ids[2], ids[1], ids[0]]  # created_at, then id, both descending
        only = await store.page(session, start=day[0], end=day[1], isin=ISIN_B, strategy=None, after=None, limit=10)
        assert [c.signal_id for c in only] == [ids[2]]
        by = await store.page(session, start=day[0], end=day[1], isin=None, strategy="vwap_pivot", after=None, limit=10)
        assert [c.signal_id for c in by] == [ids[3]]
        first = await store.page(session, start=day[0], end=day[1], isin=None, strategy=None, after=None, limit=2)
        assert len(first) == 3  # one extra: there is a next page
        after = (first[1].created_at, first[1].signal_id)
        rest = await store.page(session, start=day[0], end=day[1], isin=None, strategy=None, after=after, limit=2)
        assert [c.signal_id for c in rest] == [ids[1], ids[0]]
        nothing = await store.page(
            session, start=day[1], end=day[1] + timedelta(days=1), isin=None, strategy=None, after=None, limit=5
        )
        assert nothing == []
