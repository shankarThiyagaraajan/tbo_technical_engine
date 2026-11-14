"""The TE7 scripts: the benchmark runs end to end on a tiny market, and the JIT on and off give identical digests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts import benchmark, identity_check


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> dict[str, object]:
    assert benchmark.main([*argv, "--json"]) == 0
    result: dict[str, object] = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    return result


def test_the_replay_benchmark_reports_timings_and_candidates(capsys: pytest.CaptureFixture[str]) -> None:
    result = run(capsys, "replay", "--stocks", "2", "--sessions", "4", "--seed", "3")
    assert result["mode"] == "replay" and result["stocks"] == 2 and "machine" in result
    assert float(str(result["seconds"])) > 0


def test_the_sweep_benchmark_runs_a_grid_over_a_synthetic_cache(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    result = run(capsys, "sweep", "--stocks", "2", "--sessions", "20", "--cache", str(tmp_path / "cache"))
    assert result["mode"] == "sweep" and float(str(result["seconds"])) > 0


def test_the_digests_are_repeatable() -> None:
    first = identity_check.digests(2, 3)
    second = identity_check.digests(2, 3)
    assert first == second


def test_the_jit_on_and_off_give_identical_digests_on_twenty_stock_sessions() -> None:
    on, off = identity_check.compare(stocks=5, sessions=4)
    assert on == off and int(on["candidate_count"]) > 0


# ---- `benchmark live` writes and deletes rows and flushes Redis: it runs only against a test database and a test Redis
# (remove-lab-mode P22). It reads TBO_TEST_DATABASE_URL and TBO_TEST_REDIS_URL, never TBO_TECHNICAL_*, which a shell
# that has a real stack's settings exported would otherwise hand it.

UNREACHABLE_DB = "postgresql+asyncpg://tbo@127.0.0.1:1/{name}"
LIVE = ("live", "--stocks", "2", "--boundaries", "2", "--json")


def test_live_refuses_a_run_that_is_not_on_the_test_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run as `TBO_MODE=paper uv run python -m scripts.benchmark live`, it must stop before any connection."""
    from tbo_platform_kit import mode as kit_mode

    previous = kit_mode.installed_profile()
    kit_mode._install(None)  # what a plain `python -m scripts.benchmark` sees: no installed profile
    try:
        monkeypatch.setenv("TBO_MODE", "paper")
        monkeypatch.setenv("TBO_TEST_DATABASE_URL", UNREACHABLE_DB.format(name="tbo_test_dev"))
        monkeypatch.setenv("TBO_TEST_REDIS_URL", "redis://127.0.0.1:1/0")
        with pytest.raises(SystemExit, match=r"tbo_platform_kit\.testing\.run"):
            benchmark.main(list(LIVE))
    finally:
        kit_mode._install(previous)


@pytest.mark.parametrize("name", ["tbo_paper", "tbo_live", "tbo_dev", "tbo_test", "technical"])
def test_live_refuses_a_database_that_is_not_a_test_database(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    from tbo_platform_kit.guards import NotATestTarget

    monkeypatch.setenv("TBO_TEST_DATABASE_URL", UNREACHABLE_DB.format(name=name))
    monkeypatch.setenv("TBO_TEST_REDIS_URL", "redis://127.0.0.1:1/0")
    with pytest.raises(NotATestTarget):  # refused by name, before a connection (the port is closed)
        benchmark.main(list(LIVE))


def test_live_refuses_a_remote_or_query_string_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    from tbo_platform_kit.guards import NotATestTarget

    monkeypatch.setenv("TBO_TEST_REDIS_URL", "redis://127.0.0.1:1/0")
    for url in (
        "postgresql+asyncpg://tbo@db.example.invalid/tbo_test_dev",
        "postgresql+asyncpg://tbo@127.0.0.1:1/tbo_test_dev?host=db.example.invalid",
    ):
        monkeypatch.setenv("TBO_TEST_DATABASE_URL", url)
        with pytest.raises(NotATestTarget):
            benchmark.main(list(LIVE))


def test_live_does_not_read_the_services_own_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TBO_TEST_DATABASE_URL", raising=False)
    monkeypatch.delenv("TBO_TEST_REDIS_URL", raising=False)
    monkeypatch.setenv("TBO_TECHNICAL_DATABASE_URL", UNREACHABLE_DB.format(name="tbo_test_dev"))
    monkeypatch.setenv("TBO_TECHNICAL_REDIS_URL", "redis://127.0.0.1:1/0")
    with pytest.raises(SystemExit, match="TBO_TEST_DATABASE_URL"):
        benchmark.main(list(LIVE))


async def test_live_leaves_a_redis_that_holds_a_real_modes_key_alone(
    monkeypatch: pytest.MonkeyPatch, database_url: str, redis_url: str, redis: Any
) -> None:
    import asyncio

    from tbo_platform_kit.guards import NotATestTarget

    monkeypatch.setenv("TBO_TEST_DATABASE_URL", database_url)
    monkeypatch.setenv("TBO_TEST_REDIS_URL", redis_url)
    await redis.set("paper.not_ours", "1")
    try:
        with pytest.raises(NotATestTarget):
            await asyncio.to_thread(benchmark.main, list(LIVE))
        assert await redis.get("paper.not_ours") == b"1"
    finally:
        await redis.delete("paper.not_ours")


def test_live_runs_on_a_test_database_and_a_test_redis(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, database_url: str, redis_url: str
) -> None:
    monkeypatch.setenv("TBO_TEST_DATABASE_URL", database_url)
    monkeypatch.setenv("TBO_TEST_REDIS_URL", redis_url)
    result = run(capsys, *LIVE[:-1])
    assert result["mode"] == "live" and result["stocks"] == 2
