"""This component's settings: every value is TBO_TECHNICAL_<NAME> (or <NAME>_FILE for a secret).

The kit's ComponentSettings already has the database and Redis URLs, the token settings, the log level and the
request size limit; add this component's own settings below.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import AnyHttpUrl, Field
from tbo_platform_kit.settings import ComponentSettings

REPOSITORY = "tbo_technical_engine"
SHORT_NAME = "technical"


class Settings(ComponentSettings, short_name=SHORT_NAME, repository=REPOSITORY):
    """This component's configuration. A field `x` is read from `TBO_TECHNICAL_X`; the string under each says what it
    is."""

    bind_host: str = "127.0.0.1"
    """The address the API listens on. Loopback by default, so a process started by hand is not reachable from the
    network; the image sets TBO_TECHNICAL_BIND_HOST=0.0.0.0, because inside its container only the stack's internal
    network and Caddy reach it."""
    bind_port: int = Field(default=8000, ge=1, le=65_535)
    """The API port (the image's EXPOSE uses 8000)."""
    contract_file: Path = Path("contract/openapi.json")
    """The bundled contract this service serves at GET /openapi.json (scripts/vendor_contract.sh writes it)."""

    market_feed_url: AnyHttpUrl | None = None
    """The market feed (candle history, feed status); needed by the signal worker."""
    fundamental_url: AnyHttpUrl | None = None
    """The fundamental engine (the universe); needed by the signal worker."""
    filings_url: AnyHttpUrl | None = None
    """The filings extractor (`GET /v1/market-calendar`: holidays, special sessions, timing changes). Unset: the trading
    calendar stays empty, every day-dependent job reports "calendar unknown" and the worker logs one warning."""
    calendar_cache_file: Path = Path(".dev/calendar.json")
    """The last-good copy of the calendar (mode 0600, written atomically by the kit); used when the API is down."""
    research_cache_dir: Path = Path(".dev/research_cache")
    """Parquet cache of candles for research and backtests (`store/research_cache.py`). Not committed (`.dev/`)."""
    max_candle_age_seconds: int = 120
    """A closed candle older than this when it arrives is stored but not evaluated (late entries are not entries)."""
    strategies_file: Path = Path("config/strategies.yaml")
    """The strategy parameters (`config/strategies.yaml`); read at start, and a bad file stops the process."""
    cpr_narrow_pct: float = 0.25
    """A previous-session CPR narrower than this percent of its pivot makes the day `narrow` (trend day likely)."""
    cpr_wide_pct: float = 0.5
    """Wider than this: `wide` (range day likely). Starting values, to be tuned by the backtest."""
    swing_file: Path = Path("config/swing.yaml")
    """The swing analysis parameters (`config/swing.yaml`); read by the daily snapshot job, and a bad file stops it."""
    backtest_file: Path = Path("config/backtest.yaml")
    """Caps, execution assumptions, walk-forward windows and parameter grids for backtests (TE5)."""
    costs_file: Path = Path("config/costs.yaml")
    """The trading-cost schedule for backtests. Placeholder figures until the owner confirms them (TE5)."""
    signals_file: Path = Path("config/signals.yaml")
    """The service's signals (tbo_signals): ids, kinds, labels, links. A bad file stops the process at start."""
    signals_metrics_port: int | None = None
    """A port for the signal worker's own metrics (it has no API). Unset: not served.
    The level is TBO_TECHNICAL_SIGNALS."""
