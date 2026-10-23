"""One closed 5-minute candle in, the candidates it produces out. No I/O: the same code runs live, in a replay of a
recorded session and (TE5) in a backtest, which is what makes their candidates identical.

For every active strategy it checks the entry window and the cooldown, builds the context from the indicator engine and
the candle buffer, asks the strategy per allowed side, and turns a draft into a candidate with a deterministic ID.
`accept` records that a candidate was really emitted (stored); until then the cooldown does not move, so a failed
write cannot swallow a signal.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, time, timedelta

from tbo_platform_kit.config.calendar import IST
from tbo_platform_kit.logging import get_logger

from tbo_technical_engine.config.corporate_actions import MAX_OPENING_GAP
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
LAST_BAR_FROM, LAST_BAR_UNTIL = time(15, 0), time(15, 30)
"""A regular 5-minute session's last bar starts at 15:25, so a bar starting in this window closes a regular session. A
Muhurat evening session (about 18:15 to 19:15) never has one."""
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
        contexts: Mapping[datetime, Context | None] | None = None,
    ) -> None:
        self.engine = engine
        self.buffer = buffer
        self.config = config
        self.registry = registry or REGISTRY
        self.index_side = index_side
        self.contexts = contexts
        """Contexts built beforehand, by candle start (a backtest's tape: the same candles under many parameter sets
        build each context once). When set, `evaluate` reads them instead of building one from the buffer."""
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
        day_start = datetime.combine(candle.start_at.astimezone(IST).date(), time(0, 0), tzinfo=IST)
        # Walk back from the newest candle: today's candles first, then the previous session's last close, and stop.
        # (The buffer's newest 5-minute candle is this one, checked above.) A candle belongs to today when it starts at
        # or after IST midnight, so no time-zone conversion is needed per candle; scanning the whole 1,000-candle series
        # with one for each was most of the cost of a replay.
        today: list[Candle] = []
        prev_close: float | None = None
        for c in reversed(self.buffer.view(candle.isin, "5m")):
            if c.start_at >= day_start:
                today.append(c)
            elif LAST_BAR_FROM <= c.start_at.astimezone(IST).time() < LAST_BAR_UNTIL:
                # The last bar of a regular session. A special session (Muhurat) ends earlier in the day, so its
                # bars are skipped and the previous regular session's close is the gap's base (9.7).
                prev_close = float(c.close)
                break
        today.reverse()
        if prev_close and today and abs(float(today[0].open) / prev_close - 1) > MAX_OPENING_GAP:
            return None  # a split or bonus: the previous session's levels are on another price scale (9.5)
        trend = self.engine.values_asof(candle.isin, "15m", candle.start_at)
        daily = self.engine.values(candle.isin, "1d")
        return Context(
            isin=candle.isin,
            rows=[Row(c, v) for c, v in recent],
            trend15=None if trend is None else trend[1],
            daily=None if daily is None else daily[1],
            session=today,
            prev_close=prev_close,
            index_side=None if self.index_side is None else self.index_side(candle.start_at),
        )

    def evaluate(self, candle: Candle, now: datetime) -> list[SignalCandidate]:
        """Candidates for `candle`. Skips outside a strategy's entry window or inside its cooldown; a strategy that
        raises is logged and the rest still run."""
        if candle.timeframe != "5m":
            return []
        close_at = candle.start_at + length("5m")
        ctx: Context | None = None
        found: list[SignalCandidate] = []
        for name, strategy, params in self.active():
            try:
                sides = self._open_sides(candle.isin, name, params, close_at)
                if not sides:
                    continue
                ctx = ctx or self._context_of(candle)
                if ctx is None:
                    return []
                found.extend(
                    self._candidate(candle, name, d, close_at, now) for d in strategy.on_candle(ctx, params, sides)
                )
            except Exception as exc:
                # A strategy that fails (a bad parameter, a bug) is silent; the others still run.
                log.error("strategy_failed", strategy=name, isin=candle.isin, error=type(exc).__name__)
        return found

    def _context_of(self, candle: Candle) -> Context | None:
        """The candle's context: from the shared cache when there is one, else built here."""
        return self.contexts.get(candle.start_at) if self.contexts is not None else self.context(candle)

    def _open_sides(self, isin: str, name: str, params: Params, close_at: datetime) -> list[Side]:
        """The sides a strategy may fire on now: none outside its entry window, and none still in its cooldown."""
        local = close_at.astimezone(IST).time()
        if not (params.clock("entry_from") <= local <= params.clock("entry_until")):
            return []
        cooldown = timedelta(minutes=params.integer("cooldown_minutes"))
        return [s for s in SIDES if (last := self.fired.get((isin, name, s))) is None or close_at - last >= cooldown]

    def forget(self, isins: frozenset[str] | set[str]) -> None:
        """Drop the cooldowns of stocks that left the universe, so the map does not grow with every stock ever seen."""
        for key in [k for k in self.fired if k[0] in isins]:
            del self.fired[key]

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
