"""TE0 stubs: each contract operation is protected by its scope and answers 501 until its phase lands."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from tbo_platform_kit.testing import TestKeys

from tbo_technical_engine.settings import REPOSITORY

ROOT = Path(__file__).resolve().parents[1]
DOC = json.loads((ROOT / "contract" / "openapi.json").read_text())
# Add a path prefix here when a phase replaces its stub.
IMPLEMENTED: tuple[str, ...] = (
    "/v1/indicators",
    "/v1/signals",
    "/v1/strategies",
    "/v1/context",
    "/v1/snapshots",
    "/v1/swing-setups",
)
OPERATIONS = [
    (method.upper(), path, op["security"][0]["bearerAuth"][0])
    for path, item in DOC["paths"].items()
    for method, op in item.items()
    if method in {"get", "put", "post", "delete"} and op.get("security") and not path.startswith(IMPLEMENTED)
]
OTHER_SCOPE = {"technical:read": "technical:research", "technical:research": "technical:read"}


def _url(path: str) -> str:
    return (
        path.replace("{isin}", "INE002A01018")
        .replace("{signal_id}", "01J00000000000000000000000")
        .replace("{backtest_id}", "01J00000000000000000000000")
    )


@pytest.mark.parametrize(("method", "path", "scope"), OPERATIONS)
async def test_stub_is_protected_and_not_implemented(
    client: httpx.AsyncClient, keys: TestKeys, method: str, path: str, scope: str
) -> None:
    url = _url(path)
    assert (await client.request(method, url)).status_code == 401
    wrong = keys.bearer("tbo_platform", REPOSITORY, [OTHER_SCOPE[scope]])
    assert (await client.request(method, url, headers=wrong)).status_code == 403
    right = keys.bearer("tbo_platform", REPOSITORY, [scope])
    answer = await client.request(method, url, headers=right)
    assert answer.status_code == 501
    assert answer.headers["content-type"].startswith("application/problem+json")
    assert answer.json()["code"] == "not_implemented"
