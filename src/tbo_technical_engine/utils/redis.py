"""The one way this service opens Redis: with connect and reply timeouts (review 9.25, lesson 57).

    redis = connect_redis(settings.redis_url.get_secret_value())

A bare `Redis.from_url(url)` waits for ever on a Redis that accepts the connection and then stops answering (a failover,
a full `maxmemory` with a blocked client, a partition that drops packets): the signal worker's 15 s supervise tick, its
consumers and the outbox flush would hang while the API's health check still answers, so the stack would call the
worker healthy while nothing is evaluated.
"""

from __future__ import annotations

import redis.asyncio as aioredis

from tbo_technical_engine.config.redis import (
    REDIS_CONNECT_TIMEOUT_SECONDS,
    REDIS_HEALTH_CHECK_SECONDS,
    REDIS_SOCKET_TIMEOUT_SECONDS,
)


def connect_redis(url: str, *, socket_timeout: float = REDIS_SOCKET_TIMEOUT_SECONDS) -> aioredis.Redis:
    """An async Redis client for `url` that gives up on a dead or stalled server instead of waiting for ever.
    `socket_timeout` is a parameter only so a test can see the give-up without waiting 30 s."""
    client: aioredis.Redis = aioredis.Redis.from_url(
        url,
        socket_connect_timeout=REDIS_CONNECT_TIMEOUT_SECONDS,
        socket_timeout=socket_timeout,
        health_check_interval=REDIS_HEALTH_CHECK_SECONDS,
    )
    return client
