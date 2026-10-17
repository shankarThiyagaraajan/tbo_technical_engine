"""Hand-built candidates and candles for the simulator tests: every expected number can be checked on paper."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from tbo_technical_engine.backtest.costs import load_costs
from tbo_technical_engine.schemas.backtest import Execution
from tbo_technical_engine.schemas.candle import Candle
from tbo_technical_engine.schemas.costs import CostSchedule
from tbo_technical_engine.schemas.signal import SignalCandidate
from tbo_technical_engine.services.backtests import load_settings
from tests.support import ISIN_A, session_start

ROOT = Path(__file__).resolve().parents[2]
DAY = date(2026, 10, 15)
COSTS = load_costs(ROOT / "config" / "costs.yaml")
SETTINGS = load_settings(ROOT / "config" / "backtest.yaml")


def execution(**changes: object) -> Execution:
    """The shipped execution settings with a few overrides; slippage defaults to zero so hand numbers stay round."""
    base = {"slippage_bps": Decimal(0), **changes}
    return SETTINGS.execution.model_copy(update=base)


def free() -> CostSchedule:
    """A schedule that charges nothing, to test price paths without cost noise."""
    return COSTS.model_copy(
        update={k: Decimal(0) for k in (
            "brokerage_pct", "brokerage_cap", "stt_sell_pct", "exchange_txn_pct", "sebi_per_crore",
            "stamp_buy_pct", "ipft_per_crore", "gst_pct",
        )}
    )  # fmt: skip


def at(index: int, day: date = DAY) -> datetime:
    """The start of the `index`th 5-minute candle of a session (0 = 09:15 IST)."""
    return session_start(day) + timedelta(minutes=5 * index)


def bar(index: int, o: str, h: str, low: str, c: str, day: date = DAY, isin: str = ISIN_A) -> Candle:
    return Candle(
        isin=isin,
        timeframe="5m",
        start_at=at(index, day),
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(low),
        close=Decimal(c),
        volume=1000,
    )


def candidate(
    side: str = "buy",
    *,
    index: int = 10,
    low: str = "100.00",
    high: str = "100.20",
    stop: str = "99.00",
    t1: str = "101.50",
    t2: str = "102.50",
    day: date = DAY,
    isin: str = ISIN_A,
) -> SignalCandidate:
    return SignalCandidate(
        signal_id=f"01J{index:023d}",
        isin=isin,
        strategy="vwap_breakout",
        side=side,
        timeframe="5m",
        candle_start_at=at(index, day),
        entry_low=low,
        entry_high=high,
        stop=stop,
        target_1=t1,
        target_2=t2,
        strength="0.7000",
        components=[],
        indicator_values={},
        params_version="test",
        created_at=at(index, day),
    )


def flat(first: int, last: int, price: str = "100.10", day: date = DAY) -> list[Candle]:
    """Quiet candles from index `first` to `last` inclusive that touch no level of `candidate()`."""
    p = Decimal(price)
    return [
        bar(i, price, str(p + Decimal("0.10")), str(p - Decimal("0.10")), price, day) for i in range(first, last + 1)
    ]
