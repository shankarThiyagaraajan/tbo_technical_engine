"""One closed 5-minute candle in, the candidates it produces out. No I/O: the same code runs live, in a replay of a
recorded session and (TE5) in a backtest, which is what makes their candidates identical.

For every active strategy it checks the entry window and the cooldown, builds the context from the indicator engine and
the candle buffer, asks the strategy per allowed side, and turns a draft into a candidate with a deterministic ID.
`accept` records that a candidate was really emitted (stored); until then the cooldown does not move, so a failed
write cannot swallow a signal.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

from tbo_platform_kit.calendar import IST
from tbo_platform_kit.logging import get_logger

from tbo_technical_engine.indicators.format import decimals
from tbo_technical_engine.schemas.candle import Candle
from tbo_technical_engine.schemas.signal import Draft, SignalCandidate
from tbo_technical_engine.schemas.strategy import Params, StrategyFile
from tbo_technical_engine.services.candle_buffer import CandleBuffer
from tbo_technical_engine.services.indicator_engine import IndicatorEngine
from tbo_technical_engine.strategies import REGISTRY
from tbo_technical_engine.strategies.base import SIDES, Context, Row, Side, Strategy
from tbo_technical_engine.utils.ids import signal_id
from tbo_technical_engine.utils.timeframes import length

log = get_logger(__name__)
IndexSide = Callable[[datetime], int | None]
FiredKey = tuple[str, str, str]
"""(isin, strategy, side)."""


class StrategyEvaluator:
    """Runs every active strategy on a closed 5-minute candle and turns their drafts into candidates. Pure of I/O:
    storing and publishing belong to `SignalRunner`, so a replay or backtest can reuse it."""

    def __init__(
        self,
        engine: IndicatorEngine,
        buffer: CandleBuffer,
        config: StrategyFile,
        registry: dict[str, Strategy] | None = None,
        index_side: IndexSide | None = None,
    ) -> None:
        self.engine = engine
        self.buffer = buffer
        self.config = config
        self.registry = registry or REGISTRY
        self.index_side = index_side
        self.fired: dict[FiredKey, datetime] = {}
        """When (the entry candle's close) each strategy last produced a candidate for a stock and side."""

    def active(self) -> list[tuple[str, Strategy, Params]]:
        """The active strategies with their parameters, in file order."""
        return [
            (name, self.registry[name], Params(spec.params))
            for name, spec in self.config.strategies.items()
            if spec.is_active
        ]

    def context(self, candle: Candle) -> Context | None:
        """Everything a strategy may look at for `candle`, or None when a newer candle has already arrived (then this
        one is history, not an entry)."""
        recent = self.engine.recent(candle.isin, "5m")
        if not recent or recent[-1][0].start_at != candle.start_at:
            return None  # a newer candle has arrived since: this one is history, not an entry
        local_day = candle.start_at.astimezone(IST).date()
        series = self.buffer.series(candle.isin, "5m")
        session = [
            c for c in series if c.start_at <= candle.start_at and c.start_at.astimezone(IST).date() == local_day
        ]
        earlier = [c for c in series if c.start_at.astimezone(IST).date() < local_day]
        trend = self.engine.values_asof(candle.isin, "15m", candle.start_at)
        daily = self.engine.values(candle.isin, "1d")
        return Context(
            isin=candle.isin,
            rows=[Row(c, v) for c, v in recent],
            trend15=None if trend is None else trend[1],
            daily=None if daily is None else daily[1],
            session=session,
            prev_close=float(earlier[-1].close) if earlier else None,
            index_side=None if self.index_side is None else self.index_side(candle.start_at),
        )

    def evaluate(self, candle: Candle, now: datetime) -> list[SignalCandidate]:
        """Candidates for `candle`. Skips outside a strategy's entry window or inside its cooldown; a strategy that
        raises is logged and the rest still run."""
        if candle.timeframe != "5m":
            return []
        close_at = candle.start_at + length("5m")
        local = close_at.astimezone(IST).time()
        ctx: Context | None = None
        found: list[SignalCandidate] = []
        for name, strategy, params in self.active():
            try:
                if not (params.clock("entry_from") <= local <= params.clock("entry_until")):
                    continue
                cooldown = timedelta(minutes=params.integer("cooldown_minutes"))
                sides: list[Side] = [
                    s
                    for s in SIDES
                    if (last := self.fired.get((candle.isin, name, s))) is None or close_at - last >= cooldown
                ]
                if not sides:
                    continue
                if ctx is None:
                    ctx = self.context(candle)
                    if ctx is None:
                        return []
                for draft in strategy.on_candle(ctx, params, sides):
                    found.append(self._candidate(candle, name, draft, close_at, now))
            except Exception as exc:
                # A strategy that fails (a bad parameter, a bug) is silent; the others still run.
                log.error("strategy_failed", strategy=name, isin=candle.isin, error=type(exc).__name__)
        return found

    def accept(self, candidate: SignalCandidate) -> None:
        """Start the cooldown of a candidate that was emitted."""
        close_at = candidate.candle_start_at + length("5m")
        self.fired[(candidate.isin, candidate.strategy, candidate.side)] = close_at

    def _candidate(self, candle: Candle, name: str, draft: Draft, close_at: datetime, now: datetime) -> SignalCandidate:
        return SignalCandidate(
            signal_id=signal_id(candle.isin, name, draft.side, candle.start_at, close_at),
            isin=candle.isin,
            strategy=name,
            side=draft.side,
            timeframe="5m",
            candle_start_at=candle.start_at,
            entry_low=format(draft.entry_low, "f"),
            entry_high=format(draft.entry_high, "f"),
            stop=format(draft.stop, "f"),
            target_1=format(draft.target_1, "f"),
            target_2=format(draft.target_2, "f"),
            strength=format(draft.strength, "f"),
            components=list(draft.components),
            indicator_values=decimals(draft.indicators),
            params_version=self.config.params_version,
            created_at=now,
        )
