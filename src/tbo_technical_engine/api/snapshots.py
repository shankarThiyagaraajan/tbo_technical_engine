"""GET /v1/snapshots, /v1/snapshots/{isin} and /v1/swing-setups (TE6).

A snapshot's `date` is the trade date it serves: the snapshot built after the previous session's close, so
`?date=2026-10-15` answers with the one whose `as_of_date` is the latest before 2026-10-15. Without a date it is the
latest there is. The session is chosen once for the whole answer, so a page never mixes two days.
"""

from __future__ import annotations

import re
from datetime import date as date_type
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from tbo_platform_kit.http_server import requires
from tbo_platform_kit.problems import Problem
from tbo_platform_kit.tokens import Claims

from tbo_technical_engine.store import snapshots as snapshot_store
from tbo_technical_engine.store import swing_setups as setup_store
from tbo_technical_engine.utils import paging

ISIN = re.compile(r"^IN[A-Z0-9]{9}[0-9]$")

router = APIRouter()


def _after(cursor: str | None) -> str | None:
    """The ISIN a cursor stands for (the pages are ordered by ISIN)."""
    decoded = paging.decode(cursor)
    if decoded is None:
        return None
    if len(decoded) != 1 or not isinstance(decoded[0], str) or not ISIN.fullmatch(decoded[0]):
        raise Problem(
            "validation_failed",
            "The cursor is not valid.",
            errors=[{"field": "cursor", "message": "not a cursor from this API"}],
        )
    return decoded[0]


def _page(rows: list[dict[str, Any]], limit: int) -> dict[str, Any]:
    items, more = rows[:limit], len(rows) > limit
    return {"items": items, "next_cursor": paging.encode([items[-1]["isin"]]) if more else None}


@router.get("/v1/snapshots")
async def list_snapshots(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    cursor: Annotated[str | None, Query(min_length=1, max_length=512)] = None,
    date: Annotated[date_type | None, Query()] = None,
    caller: Claims = requires("technical:read"),
) -> dict[str, Any]:
    after = _after(cursor)
    async with request.app.state.sessions() as session:
        as_of = await snapshot_store.serving(session, date)
        if as_of is None:
            return {"items": [], "next_cursor": None}
        rows = await snapshot_store.page(session, as_of, after=after, limit=limit)
    return _page(rows, limit)


@router.get("/v1/snapshots/{isin}")
async def get_snapshot(
    request: Request,
    isin: str,
    date: Annotated[date_type | None, Query()] = None,
    caller: Claims = requires("technical:read"),
) -> dict[str, Any]:
    if not ISIN.fullmatch(isin):
        raise Problem("validation_failed", "Not an ISIN.", errors=[{"field": "isin", "message": "not an ISIN"}])
    async with request.app.state.sessions() as session:
        as_of = await snapshot_store.serving(session, date)
        found = None if as_of is None else await snapshot_store.get(session, as_of, isin)
    if found is None:
        raise Problem("not_found", f"No technical snapshot for {isin}.")
    return found


@router.get("/v1/swing-setups")
async def list_swing_setups(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    cursor: Annotated[str | None, Query(min_length=1, max_length=512)] = None,
    date: Annotated[date_type | None, Query()] = None,
    caller: Claims = requires("technical:read"),
) -> dict[str, Any]:
    """Setups found after the session `date` (default: the latest session that has any), ordered by ISIN."""
    after = _after(cursor)
    async with request.app.state.sessions() as session:
        day = date or await setup_store.latest_date(session)
        if day is None:
            return {"items": [], "next_cursor": None}
        rows = await setup_store.page(session, day, after=after, limit=limit)
    return _page(rows, limit)
