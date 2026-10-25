"""Process entry point: `uv run python -m tbo_technical_engine.main` locally; the image runs the same module."""

from __future__ import annotations

import uvicorn
from tbo_platform_kit.logging import configure_logging

from tbo_technical_engine.app import build_app
from tbo_technical_engine.config.settings import Settings


def main() -> None:
    """Serve the API on the configured host and port (loopback unless set; the image sets all interfaces)."""
    settings = Settings()
    configure_logging(
        service=settings.short_name,
        version=settings.version,
        mode=settings.profile.mode.value,
        level=settings.log_level,
    )
    uvicorn.run(
        build_app(settings),
        host=settings.bind_host,
        port=settings.bind_port,
        log_config=None,
        proxy_headers=False,
        server_header=False,
    )


if __name__ == "__main__":
    main()
