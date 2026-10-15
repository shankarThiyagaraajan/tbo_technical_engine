"""The five intraday strategies (TE3). Each is a pure rule over a `Context`; `Evaluator` adds the entry window, the
cooldown, ids and versions."""

from tbo_technical_engine.strategies.base import Strategy
from tbo_technical_engine.strategies.ema_cross import EmaCrossover
from tbo_technical_engine.strategies.orb import OpeningRangeBreakout
from tbo_technical_engine.strategies.pivot_supertrend import PivotSupertrend
from tbo_technical_engine.strategies.vwap_breakout import VwapBreakout
from tbo_technical_engine.strategies.vwap_pivot import VwapPivot

REGISTRY: dict[str, Strategy] = {
    s.name: s for s in (EmaCrossover(), VwapBreakout(), OpeningRangeBreakout(), PivotSupertrend(), VwapPivot())
}
