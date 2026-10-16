"""One 5-minute boundary's evaluation record, and a session's roll-up of them."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class BoundaryLatency(BaseModel):
    """What happened to the universe's candles at one 5-minute boundary.

    `expected` is the universe size when the row was written. Every stock of it is in exactly one of `evaluated`,
    `stale`, `cold`, `inactive`, `failed` or `absent` (no candle arrived within the grace period). While the
    feed was not healthy the engine is meant to be silent: such a row has `suspended` set and none of it counts as
    missed.
    """

    model_config = ConfigDict(frozen=True)

    boundary_at: datetime = Field(description="When the 5-minute candles of this boundary closed (UTC).")
    trade_date: date
    expected: int = Field(ge=0)
    evaluated: int = Field(ge=0)
    stale: int = Field(ge=0, description="Arrived too late to be a signal (older than the maximum candle age).")
    cold: int = Field(ge=0, description="Arrived for a stock whose history was not warm.")
    inactive: int = Field(ge=0, description="Arrived while warm-up was pending after an outage or at the open.")
    failed: int = Field(ge=0, description="An evaluator raised.")
    absent: int = Field(ge=0, description="No candle arrived within the grace period.")
    suspended: bool = Field(description="The feed was not healthy: silence was the correct behaviour.")
    p50_seconds: float | None = None
    p95_seconds: float | None = None
    max_seconds: float | None = None
    written_at: datetime

    @property
    def missed(self) -> int:
        """Evaluations that should have happened and did not (0 for a suspended boundary)."""
        if self.suspended:
            return 0
        return self.stale + self.cold + self.inactive + self.failed + self.absent


class SessionReport(BaseModel):
    """One trading day: how many boundaries it should have had, and what the rows say."""

    model_config = ConfigDict(frozen=True)

    trade_date: date
    boundaries_expected: int
    boundaries_recorded: int
    boundaries_missing: int = Field(description="Boundaries with no row at all (the worker was not running).")
    suspended: int
    missed_evaluations: int
    slow_boundaries: int = Field(description="Boundaries whose p95 was over the target.")
    worst_p95_seconds: float | None
    clean: bool
