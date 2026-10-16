from __future__ import annotations

from datetime import date
from typing import Any

import httpx
import pytest
from tbo_platform_kit.http_client import UpstreamUnavailable

from tests.support import ISIN_A, ISIN_B, fundamental_client


def row(isin: str, version: int = 1) -> dict[str, object]:
    return {"trade_date": "2026-10-15", "isin": isin, "universe_version": version}


async def test_reads_every_page() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.params["date"] == "2026-10-15"
        if request.url.params.get("cursor") is None:
            return httpx.Response(200, json={"items": [row(ISIN_A)], "next_cursor": "c"})
        return httpx.Response(200, json={"items": [row(ISIN_B)], "next_cursor": None})

    snapshot = await fundamental_client(handle).universe(date(2026, 10, 15))
    assert snapshot is not None
    assert snapshot.isins == (ISIN_A, ISIN_B) and snapshot.universe_version == 1


async def test_not_published_yet_is_none() -> None:
    assert await fundamental_client(lambda r: httpx.Response(404)).universe(date(2026, 10, 15)) is None


async def test_an_empty_page_is_none() -> None:
    body: dict[str, Any] = {"items": [], "next_cursor": None}
    assert await fundamental_client(lambda r: httpx.Response(200, json=body)).universe() is None


async def test_a_server_error_raises() -> None:
    with pytest.raises(UpstreamUnavailable):
        await fundamental_client(lambda r: httpx.Response(503)).universe(date(2026, 10, 15))
