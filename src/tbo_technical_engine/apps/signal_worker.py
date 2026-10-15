"""The signal worker process: `uv run python -m tbo_technical_engine.apps.signal_worker`.

One process handles the whole universe (plan: "one signal worker at the planned scale"). TE0 only starts it, logs in
the kit's format and waits for SIGTERM; TE1 adds the candle and feed-status consumers, TE3 the strategies.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal

from tbo_platform_kit.logging import configure_logging, get_logger

from tbo_technical_engine.settings import Settings

log = get_logger(__name__)


async def run(stop: asyncio.Event, settings: Settings | None = None) -> None:
    settings = settings or Settings()
    log.info("signal_worker_started", mode=settings.profile.mode.value)
    await stop.wait()
    log.info("signal_worker_stopped")


def main() -> None:
    settings = Settings()
    configure_logging(
        service=settings.short_name,
        version=settings.version,
        mode=settings.profile.mode.value,
        level=settings.log_level,
    )

    async def _main() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            with contextlib.suppress(NotImplementedError):  # not on Windows; the image is Linux
                loop.add_signal_handler(sig, stop.set)
        await run(stop, settings)

    asyncio.run(_main())


if __name__ == "__main__":
    main()
