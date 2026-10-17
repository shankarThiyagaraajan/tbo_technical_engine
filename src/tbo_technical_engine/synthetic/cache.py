"""Fill the research cache (`ResearchCache`) with a synthetic market, the files a recorded year would leave behind."""

from __future__ import annotations

from pathlib import Path

from tbo_technical_engine.schemas.synthetic import SyntheticSpec
from tbo_technical_engine.store.research_cache import ResearchCache
from tbo_technical_engine.synthetic.generate import stock_candles


def fill_cache(spec: SyntheticSpec, root: Path) -> list[str]:
    """Write every stock's candles to `root`; returns the ISINs written."""
    cache = ResearchCache(root)
    isins = []
    for index in range(spec.stocks):
        data = stock_candles(spec, index)
        cache.write(data.isin, "5m", data.five)
        cache.write(data.isin, "15m", data.fifteen)
        cache.write(data.isin, "1d", data.daily)
        isins.append(data.isin)
    return isins
