"""The service's signals (tbo_signals): built once per process from `config/signals.yaml`.

Not to be confused with the trade signals of `api/signals.py`: these say what the service itself is doing (stage counts,
worker activity, notable events) for the board and the audit store, and carry no prices, symbols or quantities.

The trade-signal route already owns `GET /v1/signals`, so the manifest the platform registry reads is served at
`MANIFEST_PATH` instead (`api/signal_manifest.py`).
"""

from __future__ import annotations

import functools
from collections.abc import Iterable
from typing import Any

from prometheus_client import CollectorRegistry, start_http_server
from redis import Redis
from redis.asyncio import Redis as AsyncRedis
from redis.exceptions import ResponseError
from tbo_platform_kit.logging import get_logger
from tbo_signals import Signals, StreamSink, load_manifest, stream_name
from tbo_signals.config.constants import Level
from tbo_signals.level import resolve_level

from tbo_technical_engine.config.settings import Settings
from tbo_technical_engine.schemas.latency import BoundaryLatency

log = get_logger(__name__)
MANIFEST_PATH = "/v1/signal-manifest"
"""Where the manifest is served. The library's default, `/v1/signals`, is this service's trade-signal list."""


def build_signals(settings: Settings, registry: CollectorRegistry | None = None) -> Signals:
    """The process's `Signals`. A bad manifest or level raises here, at start. At level off nothing is built and no
    Redis connection is made; otherwise events (full) and security incidents go to `<mode>.signals.v1` in the
    background."""
    manifest = load_manifest(settings.signals_file)
    mode = settings.profile.mode.value
    level = resolve_level(manifest.service, mode)
    sink = None
    if level is not Level.OFF and settings.redis_url is not None:
        sink = StreamSink(Redis.from_url(settings.redis_url.get_secret_value()), stream_name(mode))
    return Signals(manifest, mode=mode, level=level, registry=registry, sink=sink)


def serve_metrics(settings: Settings, signals: Signals) -> bool:
    """Expose this process's signal metrics on `signals_metrics_port`, for processes that have no HTTP server of their
    own (the signal worker). Does nothing without a port or while signals are off. True when a server was started."""
    port = settings.signals_metrics_port
    if port is None or signals.level is Level.OFF:
        return False
    start_http_server(port, registry=signals.registry)
    log.info("signals_metrics_serving", port=port)
    return True


@functools.cache
def process_signals(settings: Settings) -> Signals:
    """One `Signals` for a process that has no `app.state` (the Celery worker): built on first use."""
    return build_signals(settings)


def report_boundaries(signals: Signals, rows: Iterable[BoundaryLatency]) -> None:
    """Closed boundaries to the board: one with a miss is `boundaries_missed` (and a `boundary_missed` event with how
    many stocks), a clean one `boundaries_evaluated`. A boundary while the feed was down is
    neither: silence was right."""
    for row in rows:
        if row.suspended:
            continue
        if row.missed:
            signals.emit("technical.boundaries_missed", 1)
            signals.event("technical.boundary_missed", row.missed)
        else:
            signals.emit("technical.boundaries_evaluated", 1)


async def stream_backlog(redis: AsyncRedis, stream: str, group: str) -> int:
    """Entries of `stream` the `group` has not handled: not yet delivered (lag) plus delivered and not acknowledged.
    0 when the stream or the group does not exist yet."""
    try:
        groups: list[dict[str, Any]] = await redis.xinfo_groups(stream)
    except ResponseError:  # no such key
        return 0
    for found in groups:
        if _text(found.get("name")) == group:
            return int(found.get("lag") or 0) + int(found.get("pending") or 0)
    return 0


def _text(value: object) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)
