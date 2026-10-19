"""The outbox sweep (review 9.1, the platform template's 2.2): a Redis outage counts nothing, a row fault counts once
per sweep, and a full batch of failing rows cannot burn all attempts in one call."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import ResponseError
from sqlalchemy import select
from tbo_platform_kit.ids import new_ulid

from tbo_technical_engine import outbox
from tbo_technical_engine.store.tables import outbox as table


class Fake:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0

    async def publish(self, stream: str, event: dict[str, Any]) -> str:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return "1-0"


@pytest.fixture(autouse=True)
async def clean(sessions: Any) -> Any:
    """The table is shared with other tests, whose sweeps would publish these rows: leave it as found."""
    async with sessions() as session, session.begin():
        await session.execute(table.delete())
    yield
    async with sessions() as session, session.begin():
        await session.execute(table.delete())


async def seed(sessions: Any, n: int) -> None:
    async with sessions() as session, session.begin():
        for _ in range(n):
            await session.execute(
                table.insert().values(event_id=new_ulid(), stream="s", payload="{}", created_at=datetime.now(UTC))
            )


async def rows(sessions: Any) -> list[Any]:
    async with sessions() as session:
        return list((await session.execute(select(table))).all())


async def test_a_full_batch_of_refused_rows_gets_one_attempt_each_per_sweep(sessions: Any) -> None:
    n = outbox.BATCH + 50
    await seed(sessions, n)
    fake = Fake(ResponseError("NOPERM"))
    assert await outbox.flush(sessions, fake) == 0
    found = await rows(sessions)
    assert fake.calls == n and {r.attempts for r in found} == {1} and not any(r.dead_at for r in found)


async def test_redis_down_stops_the_sweep_and_counts_nothing(sessions: Any) -> None:
    await seed(sessions, outbox.BATCH + 5)
    fake = Fake(RedisConnectionError("down"))
    assert await outbox.flush(sessions, fake) == 0
    assert fake.calls == 1  # the first failure ends the sweep
    assert all((r.attempts or 0) == 0 and r.dead_at is None for r in await rows(sessions))


async def test_the_request_path_never_counts_an_attempt(sessions: Any) -> None:
    await seed(sessions, 3)
    await outbox.flush(sessions, Fake(ResponseError("NOPERM")), count_attempts=False)
    assert all((r.attempts or 0) == 0 and r.last_error for r in await rows(sessions))


async def test_a_row_refused_in_every_sweep_is_left_behind_after_max_attempts(sessions: Any) -> None:
    await seed(sessions, 1)
    for _ in range(outbox.MAX_ATTEMPTS):
        await outbox.flush(sessions, Fake(ResponseError("NOPERM")))
    (row,) = await rows(sessions)
    assert row.attempts == outbox.MAX_ATTEMPTS and row.dead_at is not None
    assert await outbox.waiting(sessions) == 0


async def test_published_rows_are_marked_and_counted(sessions: Any) -> None:
    await seed(sessions, 3)
    assert await outbox.flush(sessions, Fake()) == 3
    assert all(r.published_at is not None for r in await rows(sessions))


@pytest.mark.parametrize("error", [ValueError("bad"), TypeError("bad")])
async def test_a_payload_fault_is_a_row_fault(sessions: Any, error: Exception) -> None:
    await seed(sessions, 1)
    await outbox.flush(sessions, Fake(error))
    (row,) = await rows(sessions)
    assert row.attempts == 1
