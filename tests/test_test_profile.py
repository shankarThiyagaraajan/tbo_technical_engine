"""The tests run on the kit's test profile and nothing else (remove-lab-mode D2-5; plan D2, P18, P19, P22, P27, P28).

What lab switched off in this repository's tests, and what switches it off now (the reach check of the PR):

- polling, Kite and model calls: this service has no source poller, no Kite and no model call; the profile fields
  `polling_enabled`, `kite_enabled` and `model_calls_enabled` are off in the test profile and no test turns one on
  (`test_no_test_turns_a_reach_field_on`);
- signals: `signals_default` is "off" in the test profile, read through `resolve_level(default=)`; no variable does it;
- research blackout: `research_blackout` is None, so no test depends on the clock;
- streams and database: prefix `test.`, a `tbo_test_<x>` database that carries the comment `tbo-test`.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from tbo_platform_kit.mode import Mode, current_profile
from tbo_platform_kit.testing import REACH_FIELDS, TEST_PROFILE

ROOT = Path(__file__).resolve().parents[1]
THIS = Path(__file__).resolve()
TESTS = sorted(p for p in (ROOT / "tests").rglob("*.py") if p.resolve() != THIS)
SCRIPTS = sorted((ROOT / "scripts").rglob("*.py"))
SRC = sorted((ROOT / "src").rglob("*.py"))

# Tests that read `TBO_MODE` like a service (the marker `tbo_real_mode`): each is named here with its reason.
REAL_MODE_TESTS = {
    "tests/services/test_platform_signals.py": "a mode that is not paper or live stops the signals at start",
}
# Tests that start a child process with a set TBO_MODE of paper or live: none (P19).
CHILD_PROCESS_MODE_ALLOW_LIST: set[str] = set()
# Files that may name `tbo_platform_kit.testing` under src/ (P28): none.
SRC_TESTING_ALLOW_LIST: set[str] = set()
GUARDS = {
    "require_test_database",
    "require_test_database_sync",
    "require_test_database_url",
    "require_test_redis",
    "flush_test_redis",
    "check_test_database_url",
}
DESTRUCTIVE_STRING = re.compile(r"\b(TRUNCATE|DROP|DELETE FROM)\b")
DESTRUCTIVE_CALLS = {"flushdb", "flushall", "downgrade"}
# Destructive code that sits behind no guard call: none.
DESTRUCTIVE_ALLOW_LIST: set[str] = set()


def rel(path: Path) -> str:
    return path.resolve().relative_to(ROOT).as_posix()


# ---- the profile and the lock (P1, P18)


def test_the_tests_run_on_the_kit_test_profile() -> None:
    assert current_profile() is TEST_PROFILE
    assert TEST_PROFILE.mode is Mode.PAPER and TEST_PROFILE.stream_prefix == "test."
    assert TEST_PROFILE.database_name.startswith("tbo_test_")
    assert TEST_PROFILE.research_blackout is None
    assert TEST_PROFILE.signals_default == "off"
    assert [f for f in REACH_FIELDS if getattr(TEST_PROFILE, f)] == []


def test_the_conftest_stops_a_run_without_the_test_profile() -> None:
    text = (ROOT / "tests" / "conftest.py").read_text()
    assert 'pytest.exit("the kit test profile is not installed", 2)' in text
    assert "current_profile() is not TEST_PROFILE" in text
    for name in ("pyproject.toml", "pytest.ini", "tox.ini", "setup.cfg"):
        path = ROOT / name
        if path.exists():
            assert "pytest_plugin" not in path.read_text(), f"{name} names the kit's plugin (P18)"
    assert not [p for p in TESTS if re.search(r"^pytest_plugins\s*=", p.read_text(), re.M)]


def test_a_run_without_the_plugin_stops_before_any_test() -> None:
    """The plugin disabled and no mode set (never paper or live in a child, P19): the conftest refuses, no test runs."""
    env = {k: v for k, v in os.environ.items() if k != "TBO_MODE"}
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:tbo_platform_kit.testing.pytest_plugin",
            "-q",
            "--no-cov",
            "tests/test_ids.py",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode != 0
    assert " passed" not in done.stdout and "TBO_MODE is not set" in done.stdout + done.stderr


def used_reach_calls(path: Path) -> list[str]:
    """`use_profile(...)` calls under `path` that turn a reach field on (or hide it behind `**`)."""
    found = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", "")) == "use_profile":
            for kw in node.keywords:
                if kw.arg is None or (
                    kw.arg in REACH_FIELDS and not (isinstance(kw.value, ast.Constant) and not kw.value.value)
                ):
                    found.append(f"{rel(path)}:{node.lineno}")
    return found


def test_no_test_turns_a_reach_field_on() -> None:
    """The reach check: every `use_profile` call that turns `polling_enabled`, `kite_enabled` or `model_calls_enabled`
    on is listed here; this repository has none, and its real-mode tests are exactly `REAL_MODE_TESTS`."""
    assert [call for p in TESTS for call in used_reach_calls(p)] == []
    marked = {rel(p) for p in TESTS if "pytest.mark.tbo_real_mode" in p.read_text()}
    assert marked == set(REAL_MODE_TESTS)


# ---- no child process of a test gets a mode (P19)


def test_no_python_child_process_of_the_tests_gets_paper_or_live() -> None:
    bad = []
    for path in TESTS:
        text = path.read_text()
        spawns = re.search(r"\bsubprocess\b|\bPopen\b|create_subprocess|os\.system|os\.exec", text)
        if (
            spawns
            and re.search(r"TBO_MODE\W{1,6}(paper|live)\b", text)
            and rel(path) not in CHILD_PROCESS_MODE_ALLOW_LIST
        ):
            bad.append(rel(path))
    assert bad == []


# ---- no service module imports the test helpers (P28)


def test_no_module_under_src_imports_the_kit_test_helpers() -> None:
    bad = []
    for path in SRC:
        for node in ast.walk(ast.parse(path.read_text())):
            names = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else []
            )
            if (
                any(n == "tbo_platform_kit.testing" or n.startswith("tbo_platform_kit.testing.") for n in names)
                and rel(path) not in SRC_TESTING_ALLOW_LIST
            ):
                bad.append(rel(path))
    assert bad == []


# ---- destructive test code sits behind a guard (P22)


def destructive_lines(tree: ast.AST) -> list[tuple[int, ast.AST | None]]:
    """(line, enclosing function) of each destructive string or call."""
    found: list[tuple[int, ast.AST | None]] = []

    def walk(node: ast.AST, scope: ast.AST | None) -> None:
        here = node if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) else scope
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and DESTRUCTIVE_STRING.search(node.value):
            found.append((node.lineno, here))
        if isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", "")) in DESTRUCTIVE_CALLS:
            found.append((node.lineno, here))
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and re.search(r"\bdowngrade\b", node.value):
            found.append((node.lineno, here))
        for child in ast.iter_child_nodes(node):
            walk(child, here)

    walk(tree, None)
    return found


def calls_a_guard(scope: ast.AST | None) -> bool:
    if scope is None:
        return False
    return any(
        isinstance(n, ast.Call) and getattr(n.func, "attr", getattr(n.func, "id", "")) in GUARDS
        for n in ast.walk(scope)
    )


def test_every_truncate_drop_flush_and_downgrade_sits_behind_a_guard() -> None:
    """Each one is in a function that calls a kit guard (`require_test_database`, `flush_test_redis`, ...), or in a
    named allow-list. `tests/` and `scripts/` both."""
    unguarded = []
    for path in [*TESTS, *SCRIPTS]:
        if rel(path) in DESTRUCTIVE_ALLOW_LIST:
            continue
        tree = ast.parse(path.read_text())
        for line, scope in destructive_lines(tree):
            if not calls_a_guard(scope):
                unguarded.append(f"{rel(path)}:{line}")
    assert unguarded == []


def test_the_static_scan_sees_an_unguarded_statement() -> None:
    """Red-first proof that the scan is not blind: a function with a TRUNCATE and no guard is reported."""
    bad = ast.parse('async def f(s):\n    await s.execute(text("TRUNCATE technical.outbox"))\n')
    good = ast.parse('async def f(s):\n    await require_test_database(s)\n    await s.execute(text("TRUNCATE x"))\n')
    flush = ast.parse("async def f(r):\n    await r.flushdb()\n")
    assert [calls_a_guard(scope) for _, scope in destructive_lines(bad)] == [False]
    assert [calls_a_guard(scope) for _, scope in destructive_lines(good)] == [True]
    assert [calls_a_guard(scope) for _, scope in destructive_lines(flush)] == [False]


# ---- the dev database (P27)


def test_the_dev_postgres_is_a_test_database_with_the_test_comment() -> None:
    compose = (ROOT / "compose.dev.yml").read_text()
    assert "POSTGRES_DB: tbo_test_dev" in compose
    assert "-d tbo_test_dev" in compose  # the health check names it
    assert "/docker-entrypoint-initdb.d" in compose
    assert "tbo-test" in compose  # the comment, named where a reader looks
    assert "tbo_test_dev" in compose.split("export TBO_TEST_DATABASE_URL=")[1].splitlines()[0]
    init = ROOT / "compose.dev" / "init" / "10-test-database.sql"
    sql = init.read_text()
    assert "COMMENT ON DATABASE tbo_test_dev IS 'tbo-test'" in sql
    assert "CREATE DATABASE tbo_dev" in sql
    readme = (ROOT / "README.md").read_text()
    assert "tbo_test_dev" in readme and "down -v" in readme


# ---- an unset, empty, lab or unknown mode refuses at every entry point (D1, P2)

WORDS = {
    None: "TBO_MODE is not set; set it to paper or live",
    "": "TBO_MODE is not set; set it to paper or live",
    "lab": "TBO_MODE is lab: lab mode was removed; use paper",
    "papr": "TBO_MODE must be paper or live, not 'papr'",
}
DATABASE = "postgresql+asyncpg://tbo@127.0.0.1:1/tbo_test_dev"  # a closed port: a refusal comes before any connection
ENTRY_POINTS = {
    "api": ["-m", "tbo_technical_engine.main"],
    "worker": ["-m", "tbo_technical_engine.apps.signal_worker"],
    "celery": [
        "-c",
        "import tbo_technical_engine.tasks",
    ],  # what `celery -A tbo_technical_engine.tasks` imports (worker, beat)
    "alembic": ["-m", "alembic", "upgrade", "head"],
}


@pytest.mark.parametrize("entry", sorted(ENTRY_POINTS))
@pytest.mark.parametrize("value", [None, "", "lab", "papr"], ids=["unset", "empty", "lab", "typo"])
def test_an_entry_point_refuses_a_mode_that_is_not_paper_or_live(entry: str, value: str | None) -> None:
    """A child with no mode, an empty one, `lab` or a typo (never paper or live, P19) stops at start with the plan's
    words; the process is a plain Python one, so the test profile is not installed in it."""
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("TBO_MODE", "TBO_TECHNICAL_DATABASE_URL", "TBO_TECHNICAL_REDIS_URL")
    }
    env["TBO_TECHNICAL_DATABASE_URL"] = DATABASE
    if value is not None:
        env["TBO_MODE"] = value
    done = subprocess.run(
        [sys.executable, *ENTRY_POINTS[entry]], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120
    )
    assert done.returncode != 0
    assert WORDS[value] in done.stderr, done.stderr[-600:]


def test_alembic_on_the_test_profile_refuses_a_database_that_is_not_a_test_database(database_url: str) -> None:
    """Under the test profile (the kit's runner) Alembic calls the guard on its own connection (P22): the server's
    `postgres` database is neither named `tbo_test_<x>` nor commented `tbo-test`, so nothing is migrated there."""
    other = re.sub(r"/[^/?]+$", "/postgres", database_url)
    assert other != database_url
    env = {k: v for k, v in os.environ.items() if k != "TBO_MODE"} | {"TBO_TECHNICAL_DATABASE_URL": other}
    done = subprocess.run(
        [sys.executable, "-m", "tbo_platform_kit.testing.run", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode != 0
    assert "NotATestTarget" in done.stderr or "tbo_test" in done.stderr, done.stderr[-600:]
