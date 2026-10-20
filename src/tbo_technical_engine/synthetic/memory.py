"""How much memory the candle buffer needs at capacity (review 9.20): the signal worker runs under a 1 GB limit and
holds every series of the universe up to `CAPACITY`, 1,900 candles a stock (1,000 of 5 minutes, 500 of 15, 400 daily).

The candles are made the way the live path makes them (`Candle.from_message` on the feed's JSON shape) with prices
that differ from candle to candle, so nothing is shared by accident and the figure is what a real universe costs.

    uv run python -m scripts.benchmark memory --stocks 500
"""

from __future__ import annotations

import gc
import random
import tracemalloc
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from tbo_technical_engine.schemas.candle import Candle
from tbo_technical_engine.services.candle_buffer import CAPACITY, CandleBuffer
from tbo_technical_engine.utils.timefmt import z

TIMEFRAMES = ("5m", "15m", "1d")
"""What the signal worker keeps (`STORED_TIMEFRAMES`)."""
FIRST_START = datetime(2026, 1, 1, 3, 45, tzinfo=UTC)
STEP = {"5m": timedelta(minutes=5), "15m": timedelta(minutes=15), "1d": timedelta(days=1)}


@dataclass(frozen=True)
class BufferMemory:
    """What a full buffer of `stocks` costs, measured by `tracemalloc` (Python's own allocations, buffer included)."""

    stocks: int
    candles: int
    traced_bytes: int

    @property
    def bytes_per_candle(self) -> float:
        """Traced bytes over held candles: the candle, its key in the series and its start in the sorted list."""
        return self.traced_bytes / self.candles

    def projected_mb(self, stocks: int) -> float:
        """The same per-stock cost for a universe of `stocks`, in MB."""
        return self.traced_bytes / self.stocks * stocks / 1_000_000


def candles_at_capacity(isin: str, rnd: random.Random) -> list[Candle]:
    """One stock's candles of every kept timeframe, `CAPACITY` of each, parsed from the feed's JSON shape."""
    out: list[Candle] = []
    for timeframe in TIMEFRAMES:
        for i in range(CAPACITY[timeframe]):
            price = 500 + rnd.randint(0, 20_000) * 0.05
            out.append(
                Candle.from_message(
                    {
                        "isin": isin,
                        "timeframe": timeframe,
                        "start_at": z(FIRST_START + STEP[timeframe] * i),
                        "open": f"{price:.2f}",
                        "high": f"{price + 1.5:.2f}",
                        "low": f"{price - 1.25:.2f}",
                        "close": f"{price + 0.35:.2f}",
                        "volume": 100_000 + rnd.randint(0, 1_000_000),
                        "revision": 1,
                    }
                )
            )
    return out


def fill(stocks: int, seed: int = 1) -> CandleBuffer:
    """A fresh buffer filled to capacity for `stocks` stocks."""
    rnd = random.Random(seed)  # noqa: S311  (synthetic prices, not a secret)
    buffer = CandleBuffer()
    for s in range(stocks):
        buffer.add_many(candles_at_capacity(f"INE{s:06d}A01", rnd))
    return buffer


def held(buffer: CandleBuffer, stocks: int) -> int:
    """How many candles the buffer holds for the first `stocks` synthetic stocks."""
    return sum(buffer.count(f"INE{s:06d}A01", tf) for s in range(stocks) for tf in TIMEFRAMES)


def measure(stocks: int, seed: int = 1) -> tuple[CandleBuffer, BufferMemory]:
    """Fill a fresh buffer to capacity for `stocks` stocks and measure what it holds."""
    gc.collect()
    started = not tracemalloc.is_tracing()
    if started:
        tracemalloc.start()
    before, _ = tracemalloc.get_traced_memory()
    buffer = fill(stocks, seed)
    gc.collect()
    after, _ = tracemalloc.get_traced_memory()
    if started:
        tracemalloc.stop()
    return buffer, BufferMemory(stocks, held(buffer, stocks), after - before)
