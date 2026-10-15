"""Property-based tests of every operation against the served contract (Schemathesis), over real HTTP.

Checked: no 5xx, responses and headers match the contract's schemas, content types are right, and a protected
operation refuses a request without its token. Status codes outside an operation's list are not checked: the kit
answers 413, 415 and 428 on every route, and the contract lists those once, in the shared error catalogue.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import schemathesis
import uvicorn
from hypothesis import HealthCheck, settings
from schemathesis.checks import not_a_server_error
from schemathesis.specs.openapi.checks import (
    content_type_conformance,
    ignored_auth,
    response_headers_conformance,
    response_schema_conformance,
)
from tbo_platform_kit.testing import TestKeys

from tbo_technical_engine.app import build_app
from tbo_technical_engine.config.settings import REPOSITORY, SHORT_NAME, Settings

ROOT = Path(__file__).resolve().parents[1]


def _tolerates_stubs(ctx: Any, response: Any, case: Any) -> None:
    """not_a_server_error, except a 501: operations whose phase is not built answer the catalogue's not_implemented.

    Remove this wrapper (use not_a_server_error again) once the last stub is replaced.
    """
    if response.status_code == 501:
        return
    not_a_server_error(ctx, response, case)


CHECKS = [
    _tolerates_stubs,
    response_schema_conformance,
    response_headers_conformance,
    content_type_conformance,
    ignored_auth,
]


@pytest.fixture(scope="module")
def server(database_url: str, redis_url: str, keys: TestKeys) -> Iterator[str]:
    """The app under uvicorn on a free loopback port, in its own thread and event loop."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("TBO_MODE", "lab")
        app = build_app(
            Settings(database_url=database_url, redis_url=redis_url, contract_file=ROOT / "contract" / "openapi.json"),
            verifier=keys.verifier(REPOSITORY),
        )
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None, lifespan="on"))
        thread = threading.Thread(target=srv.run, daemon=True)
        thread.start()
        for _ in range(100):
            if srv.started:
                break
            time.sleep(0.05)
        yield f"http://127.0.0.1:{port}"
        srv.should_exit = True
        thread.join(10)


@pytest.fixture(scope="module")
def api_schema(server: str) -> Any:
    return schemathesis.openapi.from_url(f"{server}/openapi.json")


schema = schemathesis.pytest.from_fixture("api_schema")


@schema.parametrize()
@settings(max_examples=25, deadline=None, suppress_health_check=list(HealthCheck))
def test_api(case: Any, server: str, keys: TestKeys) -> None:
    token = keys.token("tbo_platform", REPOSITORY, [f"{SHORT_NAME}:read", f"{SHORT_NAME}:research"])
    case.call_and_validate(base_url=server, headers={"Authorization": f"Bearer {token}"}, checks=CHECKS)
