from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from tbo_technical_engine.services.candle_buffer import CandleBuffer
from tbo_technical_engine.services.indicator_engine import IndicatorEngine
from tbo_technical_engine.services.recorder import IndicatorRecorder
from tbo_technical_engine.store import indicators as store
from tests.support import ISIN_A, candle, history, session_candles

TODAY = date(2026, 10, 15)
NOW = datetime(2026, 10, 15, 4, 31, tzinfo=UTC)


async def test_records_the_values_of_the_evaluated_candle_as_contract_decimals(sessions: Any) -> None:
    buffer = CandleBuffer()
    buffer.add_many(c for tf in history(ISIN_A, TODAY, sessions=5).values() for c in tf)
    first = session_candles(ISIN_A, "5m", TODAY)[0]
    buffer.add(first)
    recorder = IndicatorRecorder(IndicatorEngine(buffer), sessions, lambda: NOW)
    await recorder(first)
    async with sessions() as session:
        five = await store.latest(session, ISIN_A, "5m")
        fifteen = await store.latest(session, ISIN_A, "15m")
    assert five is not None and five.candle_start_at == first.start_at and five.computed_at == NOW
    assert five.values["ema_9"] is not None and "." in five.values["ema_9"]
    assert five.values["or_high"] is None  # the opening range is still forming at 09:15
    assert fifteen is not None  # the previous session's last 15-minute candle


async def test_the_fifteen_minute_row_is_written_once_per_candle(sessions: Any) -> None:
    buffer = CandleBuffer()
    buffer.add_many(c for tf in history(ISIN_A, TODAY, sessions=5).values() for c in tf)
    recorder = IndicatorRecorder(IndicatorEngine(buffer), sessions, lambda: NOW)
    saved = 0
    original = recorder.__class__.__call__

    for c in session_candles(ISIN_A, "5m", TODAY)[:3]:
        buffer.add(c)
        await recorder(c)
    async with sessions() as session:
        fifteen = await store.latest(session, ISIN_A, "15m")
    assert fifteen is not None and saved == 0 and original is not None
    assert len(recorder._saved) == 2  # one entry per timeframe: later 5-minute candles do not rewrite the 15-minute row


async def test_a_corrected_candle_is_recorded_again(sessions: Any) -> None:
    buffer = CandleBuffer()
    buffer.add_many(c for tf in history(ISIN_A, TODAY, sessions=5).values() for c in tf)
    first = session_candles(ISIN_A, "5m", TODAY)[0]
    buffer.add(first)
    recorder = IndicatorRecorder(IndicatorEngine(buffer), sessions, lambda: NOW)
    await recorder(first)
    async with sessions() as session:
        before = await store.latest(session, ISIN_A, "5m")
    buffer.add(candle(ISIN_A, "5m", first.start_at, "300.00", revision=2))
    await recorder(first)
    async with sessions() as session:
        after = await store.latest(session, ISIN_A, "5m")
    assert before is not None and after is not None and before.values["ema_9"] != after.values["ema_9"]
