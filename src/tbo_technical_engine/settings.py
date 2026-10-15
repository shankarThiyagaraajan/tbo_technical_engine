"""This component's settings: every value is TBO_TECHNICAL_<NAME> (or <NAME>_FILE for a secret).

The kit's ComponentSettings already has the database and Redis URLs, the token settings, the log level and the
request size limit; add this component's own settings below.
"""

from __future__ import annotations

from pathlib import Path

from tbo_platform_kit.settings import ComponentSettings

REPOSITORY = "tbo_technical_engine"
SHORT_NAME = "technical"


class Settings(ComponentSettings, short_name=SHORT_NAME, repository=REPOSITORY):
    contract_file: Path = Path("contract/openapi.json")
    """The bundled contract this service serves at GET /openapi.json (scripts/vendor_contract.sh writes it)."""
