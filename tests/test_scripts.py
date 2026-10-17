"""The TE7 scripts: the benchmark runs end to end on a tiny market, and the JIT on and off give identical digests."""

from __future__ import annotations

import json
from pathlib import Path

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
    assert identity_check.digests(2, 3) == identity_check.digests(2, 3)


def test_the_jit_on_and_off_give_identical_digests_on_twenty_stock_sessions() -> None:
    on, off = identity_check.compare(stocks=5, sessions=4)
    assert on == off and int(on["candidate_count"]) > 0
