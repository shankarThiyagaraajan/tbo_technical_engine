"""What a strategy sees and what it is.

A strategy is a pure function of a `Context`: the last few 5-minute rows (candle and indicator values), the
15-minute indicators as of the entry candle, the daily ATR, and today's candles. It keeps no state between candles, so
a restarted worker, a replay and a backtest give the same candidates. The only state in the engine is the cooldown,
which lives in the evaluator.

Long and short are one rule with a direction `d` (+1 buy, -1 sell): "above" is `d * (a - b) > 0`.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import ClassVar, Literal

from tbo_technical_engine.schemas.candle import Candle
from tbo_technical_engine.schemas.signal import Component, Draft
from tbo_technical_engine.schemas.strategy import Params

Side = Literal["buy", "sell"]
SIDES: tuple[Side, ...] = ("buy", "sell")
NAN = math.nan


def direction(side: Side) -> int:
    return 1 if side == "buy" else -1


@dataclass(frozen=True)
class Row:
    """A closed 5-minute candle and the indicator values at it."""

    candle: Candle
    v: Mapping[str, float]


@dataclass(frozen=True)
class Context:
    isin: str
    rows: Sequence[Row]
    """The last 5-minute rows, oldest first; the last one is the candle being evaluated."""
    trend15: Mapping[str, float] | None
    """15-minute indicators as of the entry candle's start (`IndicatorEngine.values_asof`)."""
    daily: Mapping[str, float] | None
    session: Sequence[Candle]
    """Today's 5-minute candles up to and including the evaluated one."""
    prev_close: float | None
    """The last 5-minute close of the previous session, for the gap."""
    index_side: int | None = None
    """+1 when the index is above its VWAP, -1 below, None when no index data exists (the feed has none yet)."""

    @property
    def now(self) -> Row:
        return self.rows[-1]


def finite(*values: float) -> bool:
    return all(math.isfinite(x) for x in values)


def component(name: str, is_met: bool, value: float | None = None) -> Component:
    from tbo_technical_engine.indicators.format import decimal_text

    return Component(name=name, is_met=is_met, value=None if value is None else decimal_text(value))


class Strategy(ABC):
    name: ClassVar[str]

    def warmup(self, history: Sequence[Row]) -> None:
        """Nothing to prime: the context carries everything, so a strategy that has seen no candle behaves like one
        that has seen them all."""
        return None

    def on_candle(self, ctx: Context, p: Params, sides: Sequence[Side] = SIDES) -> list[Draft]:
        drafts = []
        for side in sides:
            draft = self.evaluate(ctx, p, side)
            if draft is not None:
                drafts.append(draft)
        return drafts

    @abstractmethod
    def evaluate(self, ctx: Context, p: Params, side: Side) -> Draft | None:
        """The candidate for `side` on the context's newest candle, or None."""
