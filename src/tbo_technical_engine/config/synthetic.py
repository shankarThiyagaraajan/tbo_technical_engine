"""Numbers for the synthetic market the profiling and failure drills run on. None of them is a claim about real prices:
they only make the paths busy enough that all five strategies fire now and then."""

from __future__ import annotations

SESSION_MINUTES = 375
"""09:15 to 15:30 IST, one candle per minute."""

MINUTE_SIGMA = 0.0006
"""Standard deviation of a one-minute return (0.06%); a stock moves about 1.2% over a session."""

GAP_SIGMA = 0.004
"""Standard deviation of the overnight gap (0.4%)."""

BIG_GAP_CHANCE = 0.05
"""Share of sessions that open with a gap three times larger (the ORB gap filter needs some)."""

TREND_DAY_CHANCE = 0.35
"""Share of sessions with a drift through the day (the rest are ranges); a drift is up or down with equal chance."""

TREND_DRIFT = 0.00012
"""Per-minute drift on a trend day (about 4.5% over a session, before noise)."""

BURST_CHANCE = 0.012
"""Chance per minute of a volume burst with a larger move (what breakouts need)."""

BURST_VOLUME = 6.0
"""A burst trades this many times the normal volume."""

BURST_SIGMA = 4.0
"""A burst moves this many times the normal one-minute sigma."""

BASE_VOLUME = 400
"""Mean shares per minute for a stock with volume scale 1.0."""

TICKS_PER_MINUTE = 4
"""Ticks written per minute in the feed's tick format: open, low, high, close in a fixed order within the minute."""

PRICE_TICK = 0.05
"""Prices are rounded to the exchange tick, as real ones are."""
