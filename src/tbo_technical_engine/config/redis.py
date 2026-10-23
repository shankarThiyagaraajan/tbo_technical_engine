"""How this service talks to Redis (review 9.25, lesson 57): how long to wait before giving up on a stalled server."""

from __future__ import annotations

REDIS_CONNECT_TIMEOUT_SECONDS = 5.0
"""A Redis that does not accept the connection in 5 s is down; the caller's retry or the next tick tries again."""
REDIS_SOCKET_TIMEOUT_SECONDS = 30.0
"""Longest wait for one reply. Above the consumers' 5 s `XREADGROUP` block (the kit's `block_ms`), which a healthy
server answers within, and far below "for ever", which is what a bare client waits on a stalled one."""
REDIS_HEALTH_CHECK_SECONDS = 30
"""A pooled connection idle this long is pinged before use, so a half-open one is found before a command hangs."""
