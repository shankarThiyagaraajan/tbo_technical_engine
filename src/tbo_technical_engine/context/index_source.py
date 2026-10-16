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
    """Recent candles of one index, with the previous session's daily candle for its CPR."""

    code: str
    """`nifty_50`, `nifty_bank`, ...: lower snake case, as the contract's index_code."""
    candles_5m: list[Candle]
    candles_15m: list[Candle]
    previous_day: Candle | None
    """The previous session's daily candle: the high, low and close of the CPR."""


@dataclass(frozen=True)
class VixReading:
    """India VIX now and at the previous close."""

    level: float
    previous_close: float


@dataclass(frozen=True)
class IndexSnapshot:
    """Everything an `IndexSource` returns for one context run."""

    indices: list[IndexSeries] = field(default_factory=list)
    vix: VixReading | None = None


class IndexSource(Protocol):
    """Anything that can supply index and VIX data. A protocol so a real source can be added without touching the
    context code."""

    async def fetch(self, now: datetime) -> IndexSnapshot:
        """Index data as of `now` (UTC)."""
        ...


class NoIndexSource:
    """The default until the market feed carries indices: returns no data, so the context leaves those fields null."""

    async def fetch(self, now: datetime) -> IndexSnapshot:
        """Always an empty snapshot."""
        return IndexSnapshot()
