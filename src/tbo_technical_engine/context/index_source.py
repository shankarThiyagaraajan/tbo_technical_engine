"""Where index and VIX data come from.

The market feed publishes stock candles only (no index, no sector index, no India VIX in its contract), so the engine
cannot read them today. `IndexSource` is the seam: a source returns today's and recent candles of each index and the VIX
reading, and everything else here is a pure function of that. Until a source exists `NoIndexSource` is used and the
context carries no index states, no VIX and no CPR class, which is the honest answer and not a guess.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from tbo_technical_engine.schemas.candle import Candle


@dataclass(frozen=True)
class IndexSeries:
    code: str
    """`nifty_50`, `nifty_bank`, ...: lower snake case, as the contract's index_code."""
    candles_5m: list[Candle]
    candles_15m: list[Candle]
    previous_day: Candle | None
    """The previous session's daily candle: the high, low and close of the CPR."""


@dataclass(frozen=True)
class VixReading:
    level: float
    previous_close: float


@dataclass(frozen=True)
class IndexSnapshot:
    indices: list[IndexSeries] = field(default_factory=list)
    vix: VixReading | None = None


class IndexSource(Protocol):
    async def fetch(self, now: datetime) -> IndexSnapshot: ...


class NoIndexSource:
    async def fetch(self, now: datetime) -> IndexSnapshot:
        return IndexSnapshot()
