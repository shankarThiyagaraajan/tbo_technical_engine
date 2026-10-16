"""The universe the engine evaluates: the last published one, kept when the fundamental engine is unreachable."""

from __future__ import annotations

from tbo_technical_engine.schemas.candle import UniverseSnapshot


class UniverseHolder:
    """Holds the newest universe seen, so a feed or fundamental-engine outage keeps the last known list."""

    def __init__(self) -> None:
        self.snapshot: UniverseSnapshot | None = None
        self._isins: frozenset[str] = frozenset()

    def offer(self, snapshot: UniverseSnapshot) -> bool:
        """Take `snapshot` if it is newer (a later date, or the same date with a higher version); True if taken.

        Why: events are at-least-once and can arrive late or twice; an old version must never replace a new one.
        """
        held = self.snapshot
        if held is not None and (snapshot.trade_date, snapshot.universe_version) <= (
            held.trade_date,
            held.universe_version,
        ):
            return False
        self.snapshot = snapshot
        self._isins = frozenset(snapshot.isins)
        return True

    @property
    def isins(self) -> frozenset[str]:
        """The held stocks as a set, for fast membership tests."""
        return self._isins

    def __contains__(self, isin: object) -> bool:
        return isin in self._isins
