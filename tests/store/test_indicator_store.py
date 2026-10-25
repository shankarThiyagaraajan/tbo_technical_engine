from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from tbo_technical_engine.schemas.indicators import IndicatorSnapshot
from tbo_technical_engine.store import indicators as store
from tests.support import ISIN_A, ISIN_B

T0 = datetime(2026, 10, 15, 4, 25, tzinfo=UTC)


def snap(
    isin: str = ISIN_A, start: datetime = T0, ema: str | None = "100.5", computed: datetime = T0
) -> IndicatorSnapshot:
    return IndicatorSnapshot(
        isin=isin, timeframe="5m", candle_start_at=start, values={"ema_9": ema, "adx_14": None}, computed_at=computed
    )


async def test_latest_is_the_newest_candle_of_that_stock_and_timeframe(sessions: Any) -> None:
    async with sessions() as session, session.begin():
        await store.save(session, snap(start=T0))
        await store.save(session, snap(start=T0 + timedelta(minutes=5), ema="101"))
        await store.save(session, snap(isin=ISIN_B, start=T0 + timedelta(minutes=10)))
    async with sessions() as session:
        got = await store.latest(session, ISIN_A, "5m")
        assert got is not None and got.values == {"ema_9": "101", "adx_14": None}
        assert await store.latest(session, ISIN_A, "15m") is None
        assert await store.latest(session, "INE000000000", "5m") is None


async def test_saving_the_same_candle_again_replaces_it(sessions: Any) -> None:
    async with sessions() as session, session.begin():
        await store.save(session, snap(ema="1"))
        await store.save(session, snap(ema="2"))
    async with sessions() as session:
        got = await store.latest(session, ISIN_A, "5m")
        assert got is not None and got.values["ema_9"] == "2"


async def test_prune_deletes_by_age_and_is_idempotent(sessions: Any) -> None:
    async with sessions() as session, session.begin():
        await store.save(session, snap(start=T0, computed=T0 - timedelta(days=20)))
        await store.save(session, snap(start=T0 + timedelta(minutes=5), computed=T0))
    async with sessions() as session, session.begin():
        assert await store.prune(session, T0 - timedelta(days=14)) == 1
    async with sessions() as session, session.begin():
        assert await store.prune(session, T0 - timedelta(days=14)) == 0
        got = await store.latest(session, ISIN_A, "5m")
        assert got is not None and got.candle_start_at == T0 + timedelta(minutes=5)


async def test_the_tables_that_grow_every_session_have_the_indexes_their_reads_and_prune_use(sessions: Any) -> None:
    """9.26: the 02:30 prune deletes indicator rows by `computed_at`; `for_day` reads boundary rows by `trade_date`."""
    from sqlalchemy import text

    from tbo_technical_engine.store.tables import evaluation_boundary, indicator_snapshot

    query = text("SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'technical'")
    async with sessions() as session:
        found = {str(name): str(definition) for name, definition in (await session.execute(query)).all()}
    assert "(computed_at)" in found["ix_indicator_snapshot_computed_at"]
    assert "(trade_date)" in found["ix_evaluation_boundary_trade_date"]
    declared = {i.name for t in (indicator_snapshot, evaluation_boundary) for i in t.indexes}
    assert declared == {"ix_indicator_snapshot_computed_at", "ix_evaluation_boundary_trade_date"}
