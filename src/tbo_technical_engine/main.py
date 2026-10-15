"""Process entry point: `uv run python -m tbo_technical_engine.main` locally; the image runs the same module."""

from __future__ import annotations

import uvicorn
from tbo_platform_kit.logging import configure_logging

from tbo_technical_engine.app import build_app
from tbo_technical_engine.settings import Settings


def main() -> None:
    settings = Settings()
    configure_logging(
        service=settings.short_name,
        version=settings.version,
        mode=settings.profile.mode.value,
        level=settings.log_level,
    )
    uvicorn.run(
        build_app(settings),
        host="0.0.0.0",  # noqa: S104 - inside its container, on the stack's internal network only
        port=8000,
        log_config=None,
        proxy_headers=False,
        server_header=False,
    )


if __name__ == "__main__":
    main()
