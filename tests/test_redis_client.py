"""Review 9.25 (lesson 57): every Redis client of the service gives up on a stalled server instead of waiting."""

from __future__ import annotations

import ast
import asyncio
import time
from pathlib import Path

import pytest
import redis.exceptions

from tbo_technical_engine.utils.redis import connect_redis

SRC = Path(__file__).resolve().parents[1] / "src" / "tbo_technical_engine"


async def test_the_client_has_connect_and_reply_timeouts() -> None:
    client = connect_redis("redis://127.0.0.1:1/0")  # never connected: building it opens nothing
    kwargs = client.connection_pool.connection_kwargs
    assert (kwargs["socket_connect_timeout"], kwargs["socket_timeout"], kwargs["health_check_interval"]) == (
        5.0,
        30.0,
        30,
    )
    assert kwargs["socket_timeout"] > 5  # above the consumers' 5 s XREADGROUP block
    await client.aclose()


async def test_a_redis_that_accepts_and_never_answers_is_given_up_on() -> None:
    held: list[asyncio.StreamWriter] = []

    async def silent(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        held.append(writer)  # accept, read nothing, answer nothing: a stalled server

    server = await asyncio.start_server(silent, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    client = connect_redis(f"redis://127.0.0.1:{port}/0", socket_timeout=0.3)
    began = time.monotonic()
    try:
        with pytest.raises(redis.exceptions.TimeoutError):
            await client.ping()
    finally:
        await client.aclose()
        for w in held:
            w.close()
        server.close()
        await server.wait_closed()
    assert time.monotonic() - began < 5


def test_no_module_builds_a_bare_client() -> None:
    bare = []
    for path in SRC.rglob("*.py"):
        if path.name == "redis.py" and path.parent.name == "utils":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Attribute) and node.attr == "from_url" and "Redis" in ast.unparse(node.value):
                bare.append(f"{path.relative_to(SRC)}:{node.lineno}")
    assert bare == []
