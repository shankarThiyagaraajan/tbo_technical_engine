"""Review 9.20: the candle buffer at capacity must fit the signal worker's 1 GB limit at 500 stocks.

The pydantic `Candle` cost about 1,570 bytes a candle (1.55 GB RSS for 950,000 candles). The compact one measures
about 400 bytes with the buffer's own structures; the bound below leaves a margin over that (lesson 29: a measured
number with a margin), and still fails long before the old size.
"""

from __future__ import annotations

from tbo_technical_engine.synthetic.memory import measure

BYTES_PER_CANDLE_MAX = 520
UNIVERSE = 500
BUFFER_MB_MAX_AT_500 = 500


def test_a_full_buffer_costs_under_the_bound_per_candle_and_fits_500_stocks() -> None:
    _, memory = measure(stocks=3)
    assert memory.candles == 3 * (1000 + 500 + 400)
    assert memory.bytes_per_candle < BYTES_PER_CANDLE_MAX, memory
    assert memory.projected_mb(UNIVERSE) < BUFFER_MB_MAX_AT_500, memory
