"""GET /v1/context and /v1/context/history (TE4)."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from tbo_platform_kit.config.calendar import IST
from tbo_platform_kit.http_server import requires
from tbo_platform_kit.problems import Problem
from tbo_platform_kit.tokens import Claims

from tbo_technical_engine.store import context as store
from tbo_technical_engine.utils import paging
from tbo_technical_engine.utils.timefmt import parse_z, z

router = APIRouter()


@router.get("/v1/context")
async def get_context(request: Request, caller: Claims = requires("technical:read")) -> dict[str, Any]:
    """The latest 5-minute context; before 09:15 IST that is the previous session's."""
    async with request.app.state.sessions() as session:
        found = await store.latest(session)
    if found is None:
        raise Problem("not_found", "No market context has been published yet.")
    return found


@router.get("/v1/context/history")
async def list_context_history(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    cursor: Annotated[str | None, Query(min_length=1, max_length=512)] = None,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: Annotated[datetime | None, Query()] = None,
    caller: Claims = requires("technical:read"),
) -> dict[str, Any]:
    """Contexts in [from, to), oldest first; without both, today's session (IST)."""
    if (from_ is None) != (to is None):
        raise Problem(
            "validation_failed",
            "Give both from and to, or neither.",
            errors=[{"field": "from" if from_ is None else "to", "message": "from and to go together"}],
        )
    if from_ is None or to is None:
        start = datetime.combine(datetime.now(IST).date(), time(0, 0), tzinfo=IST)
        from_, to = start, start + timedelta(days=1)
    if from_ >= to:
        raise Problem(
            "validation_failed", "from must be before to.", errors=[{"field": "to", "message": "not after from"}]
        )
    after = paging.decode(cursor)
    marker = None
    if after is not None:
        try:
            marker = parse_z(str(after[0]))
        except (IndexError, ValueError) as exc:
            raise Problem(
                "validation_failed",
                "The cursor is not valid.",
                errors=[{"field": "cursor", "message": "not a cursor from this API"}],
            ) from exc
    async with request.app.state.sessions() as session:
        rows = await store.history(session, start=from_, end=to, after=marker, limit=limit)
    page, more = rows[:limit], len(rows) > limit
    return {"items": [d for _, d in page], "next_cursor": paging.encode([z(page[-1][0])]) if more else None}
