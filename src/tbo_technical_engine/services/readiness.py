"""When the engine may produce candidates.

The plan's rule: the engine depends on the feed for every signal, so it stops the moment its input is unhealthy and
resumes only after a fresh warm-up. A feed that went down or stale may have dropped candles; the warm-up refills
the buffer from REST history, where the feed's own gap refill has already repaired them.

    feed healthy ──┐
                   ├──► active ──► strategies evaluate
    warm-up done ──┘
"""

from __future__ import annotations

from tbo_platform_kit.logging import get_logger

from tbo_technical_engine.schemas.candle import FeedStatus

log = get_logger(__name__)


class Readiness:
    """Tracks the two conditions for producing candidates: a healthy feed and a warm-up that finished after the last
    unhealthy status."""

    def __init__(self) -> None:
        self.feed_state = "connecting"
        self.needs_warmup = True
        """True at start and after any unhealthy status; only `mark_warm` clears it."""
        self.generation = 0
        """Counts unhealthy statuses, so a warm-up that was running while one arrived does not count as fresh."""

    @property
    def feed_healthy(self) -> bool:
        """True only for the feed state `healthy`; `connecting` (before the first status) is not healthy."""
        return self.feed_state == "healthy"

    @property
    def active(self) -> bool:
        """True when candidates may be produced."""
        return self.feed_healthy and not self.needs_warmup

    @property
    def data_quality(self) -> str:
        """The `data_quality` of the market context: degraded whenever candidates are suspended."""
        return "ok" if self.active else "degraded"

    def on_feed_status(self, status: FeedStatus) -> None:
        """Apply a `feed.status` event: anything but healthy suspends candidates until a fresh warm-up."""
        if status.state != "healthy":
            # A hole may have opened in the series: it is only trusted again after a fresh warm-up.
            self.needs_warmup = True
            self.generation += 1
        if status.state != self.feed_state:
            log.warning("feed_state_changed", old=self.feed_state, new=status.state, reason=status.reason)
        self.feed_state = status.state

    def request_warmup(self) -> None:
        """A new session (09:00 IST) or an operator asks for a fresh start."""
        self.needs_warmup = True

    def mark_warm(self, started_at_generation: int) -> bool:
        """The warm-up that began at `started_at_generation` finished. False (and still not warm) when the feed went
        unhealthy since: the history that was read may predate the hole, so the next warm-up has to redo it."""
        if started_at_generation != self.generation:
            return False
        self.needs_warmup = False
        return True
