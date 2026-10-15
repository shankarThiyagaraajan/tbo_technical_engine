"""This component's settings: every value is TBO_TECHNICAL_<NAME> (or <NAME>_FILE for a secret).

The kit's ComponentSettings already has the database and Redis URLs, the token settings, the log level and the
request size limit; add this component's own settings below.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import AnyHttpUrl
from tbo_platform_kit.settings import ComponentSettings

REPOSITORY = "tbo_technical_engine"
SHORT_NAME = "technical"


class Settings(ComponentSettings, short_name=SHORT_NAME, repository=REPOSITORY):
    contract_file: Path = Path("contract/openapi.json")
    """The bundled contract this service serves at GET /openapi.json (scripts/vendor_contract.sh writes it)."""

    market_feed_url: AnyHttpUrl | None = None
    """The market feed (candle history, feed status); needed by the signal worker."""
    fundamental_url: AnyHttpUrl | None = None
    """The fundamental engine (the universe); needed by the signal worker."""
    research_cache_dir: Path = Path(".dev/research_cache")
    """Parquet cache of candles for research and backtests (`store/research_cache.py`). Not committed (`.dev/`)."""
    max_candle_age_seconds: int = 120
    """A closed candle older than this when it arrives is stored but not evaluated (late entries are not entries)."""
    strategies_file: Path = Path("config/strategies.yaml")
    """The strategy parameters (`config/strategies.yaml`); read at start, and a bad file stops the process."""
