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
TESTS = sorted((ROOT / "tests").rglob("*.py"))  # this file included: its own literal tables are `SELF_TABLES`
SCRIPTS = sorted((ROOT / "scripts").rglob("*.py"))
SRC = sorted((ROOT / "src").rglob("*.py"))

MARKER = "tbo_real_mode"
# Tests that read `TBO_MODE` like a service (the marker `tbo_real_mode`): each is named here with its reason.
REAL_MODE_TESTS = {
    "tests/services/test_platform_signals.py::test_a_mode_that_is_not_paper_or_live_stops_the_signals_at_start": (
        "a mode that is not paper or live stops the signals at start"
    ),
    "tests/services/test_platform_signals.py::test_the_level_follows_the_mode_and_the_environment": (
        "the signal level of paper and live, read as the service reads the mode"
    ),
    "tests/test_scripts.py::test_live_refuses_a_run_that_is_not_on_the_test_profile": (
        "a plain `python -m scripts.benchmark live` with the mode paper (no profile) must stop"
    ),
    "tests/test_scripts.py::test_live_refuses_a_set_mode_beside_the_installed_profile": (
        "the runner's profile installed but `TBO_MODE` set: `benchmark live` must stop"
    ),
}
# (file, function) that give a child process a `TBO_MODE` that is not a literal: each refuses paper and live itself.
CHILD_PROCESS_MODE_ALLOW_LIST = {
    ("tests/test_test_profile.py", "test_an_entry_point_refuses_a_mode_that_is_not_paper_or_live"): (
        "the value comes from BAD_MODES, which holds no paper or live "
        "(test_the_entry_point_modes_are_never_paper_or_live)"
    ),
}
# Files that may name `tbo_platform_kit.testing` under src/ (P28): none.
SRC_TESTING_ALLOW_LIST: set[str] = set()
# Destructive code that sits behind no guard call: none.
DESTRUCTIVE_ALLOW_LIST: set[str] = set()

# The red-first sources the scanners below must report (or pass): the only literal table of this file the scans skip.
SAMPLES = {
    # P19: a child process gets paper or live
    "p19_subscript": 'import subprocess\nenv["TBO_MODE"]  =  "paper"\nsubprocess.run(["x"], env=env)\n',
    "p19_environ": 'import subprocess\nimport os\nos.environ["TBO_MODE"] = "live"\nsubprocess.run(["x"])\n',
    "p19_dict_call": 'import subprocess\nsubprocess.run(["x"], env=dict(TBO_MODE="live"))\n',
    "p19_dict_spread": 'import os, subprocess\nsubprocess.run(["x"], env={**os.environ, "TBO_MODE": "paper"})\n',
    "p19_update": 'import subprocess\nenv.update({"TBO_MODE": "paper"})\nsubprocess.run(["x"], env=env)\n',
    "p19_update_kw": "import subprocess\nenv.update(TBO_MODE='live')\nsubprocess.run(['x'], env=env)\n",
    "p19_update_pairs": 'import subprocess\nenv.update([("TBO_MODE", "paper")])\nsubprocess.run(["x"], env=env)\n',
    "p19_setdefault": 'import os, subprocess\nos.environ.setdefault("TBO_MODE", "live")\nsubprocess.run(["x"])\n',
    "p19_enum": 'import subprocess\nenv["TBO_MODE"] = Mode.PAPER.value\nsubprocess.run(["x"], env=env)\n',
    "p19_constant": 'import subprocess\nMODE = "paper"\nenv["TBO_MODE"] = MODE\nsubprocess.run(["x"], env=env)\n',
    "p19_shell": 'import os\nos.system("TBO_MODE=live python -m x")\n',
    "p19_upper": 'import subprocess\nsubprocess.run(["x"], env={"TBO_MODE": " PAPER "})\n',
    "p19_child_ok": 'import subprocess\nsubprocess.run(["x"], env={"TBO_MODE": "lab"})\n',
    # P19: an in-process mode outside a `tbo_real_mode` test
    "p19_setenv": 'def test_x(monkeypatch):\n    monkeypatch.setenv("TBO_MODE", "papr")\n',
    "p19_setenv_real": (
        "import pytest\n@pytest.mark.tbo_real_mode\n"
        'def test_x(monkeypatch):\n    monkeypatch.setenv("TBO_MODE", "lab")\n'
    ),
    "p19_setenv_alias": (
        "import pytest as p\n@p.mark.tbo_real_mode\n"
        'def test_x(monkeypatch):\n    monkeypatch.setenv("TBO_MODE", "lab")\n'
    ),
    "real_getattr": 'import pytest\nREAL = getattr(pytest.mark, "tbo_real_mode")\n',
    # P28: a src module imports the kit's test helpers
    "p28_import": "import tbo_platform_kit.testing\n",
    "p28_import_as": "import tbo_platform_kit.testing as kt\n",
    "p28_from_testing": "from tbo_platform_kit.testing import TEST_PROFILE\n",
    "p28_from_kit": "from tbo_platform_kit import testing\n",
    "p28_from_kit_as": "def f():\n    from tbo_platform_kit import mode, testing as kt\n",
    "p28_from_profile": "from tbo_platform_kit.testing.profile import install_test_profile\n",
    "p28_importlib": 'import importlib\nimportlib.import_module("tbo_platform_kit.testing")\n',
    "p28_ok": "from tbo_platform_kit import mode\nimport tbo_platform_kit.guards\n",
    # P22: a destructive statement and its guard
    "p22_none": 'async def f(s):\n    await s.execute(text("TRUNCATE technical.outbox"))\n',
    "p22_ok": 'async def f(s):\n    await require_test_database(s)\n    await s.execute(text("TRUNCATE x"))\n',
    "p22_ok_session": (
        "async def f(sessions):\n    async with sessions() as session, session.begin():\n"
        "        await require_test_database(await session.connection())\n"
        '        await session.execute(text("TRUNCATE x"))\n'
    ),
    "p22_ok_outer": (
        "async def f(c):\n    await require_test_database(c)\n"
        '    for t in T:\n        await c.execute(text("DROP TABLE " + t))\n'
    ),
    "p22_redis_guard": 'async def f(s, r):\n    await flush_test_redis(r)\n    await s.execute(text("TRUNCATE x"))\n',
    "p22_url_only": 'async def f(s, u):\n    check_test_database_url(u)\n    await s.execute(text("TRUNCATE x"))\n',
    "p22_after": 'async def f(s):\n    await s.execute(text("TRUNCATE x"))\n    await require_test_database(s)\n',
    "p22_dead": (
        "async def f(s):\n    if False:\n        await require_test_database(s)\n"
        '    await s.execute(text("TRUNCATE x"))\n'
    ),
    "p22_lower": 'async def f(s):\n    await s.execute(text("truncate technical.outbox"))\n',
    "p22_other_conn": (
        'async def f(s, t):\n    await require_test_database(t)\n    await s.execute(text("DELETE FROM x"))\n'
    ),
    "p22_comment": 'async def f(s):\n    # await require_test_database(s)\n    await s.execute(text("TRUNCATE x"))\n',
    "p22_flush": "async def f(r):\n    await r.flushdb()\n",
    "p22_flush_db_guard": "async def f(r):\n    await require_test_database(r)\n    await r.flushall()\n",
    "p22_flush_ok": "async def f(r):\n    await require_test_redis(r)\n    await r.flushdb()\n",
    "p22_downgrade": 'def f():\n    subprocess.run(["alembic", "downgrade", "base"])\n',
    "p22_downgrade_ok": (
        "async def f(u):\n    await require_test_database_url(u)\n"
        '    subprocess.run(["alembic", "downgrade", "base"])\n'
    ),
    "p22_module": 'DELETE = "DELETE FROM technical.outbox"\n',
    "p22_docstring": '"""The schema drop and the Redis flush."""\n',
}
# Module-level names of this file whose literals the scans skip: the samples and the scanners' own patterns.
SELF_TABLES = {"SAMPLES", "MARKER", "SQL", "DOWNGRADE_TEXT", "DOWNGRADE_CALLS"}


def docstrings(tree: ast.AST) -> set[int]:
    """The ids of every docstring or other bare string statement: text, never executed."""
    return {
        id(n.value)
        for n in ast.walk(tree)
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)
    }


def rel(path: Path) -> str:
    return path.resolve().relative_to(ROOT).as_posix()


def parse(path: Path) -> tuple[ast.Module, set[int]]:
    """The file's tree and the ids of the nodes the scans skip: this file's own `SELF_TABLES`, and every docstring."""
    tree = ast.parse(path.read_text())
    skip = docstrings(tree)
    if path.resolve() == THIS:
        for node in tree.body:
            targets = (
                node.targets
                if isinstance(node, ast.Assign)
                else [node.target]
                if isinstance(node, ast.AnnAssign)
                else []
            )
            if any(isinstance(t, ast.Name) and t.id in SELF_TABLES for t in targets):
                skip |= {id(n) for n in ast.walk(node)}
    return tree, skip


def parents(tree: ast.AST) -> dict[int, ast.AST]:
    return {id(child): node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}


def call_name(node: ast.Call) -> str:
    return getattr(node.func, "attr", getattr(node.func, "id", ""))


def root_name(node: ast.AST | None) -> str | None:
    """The variable an expression starts from: `await session.connection()` and `session.execute(...)` -> session."""
    while node is not None:
        if isinstance(node, ast.Name):
            return node.id
        node = getattr(node, "value", None) if not isinstance(node, ast.Call) else node.func
    return None


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
    # the schema is migrated by plain Alembic on the kit's runner, the path the Alembic refusal test below runs
    assert '"tbo_platform_kit.testing.run", "alembic", "upgrade", "head"' in text
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
        if isinstance(node, ast.Call) and call_name(node) == "use_profile":
            for kw in node.keywords:
                if kw.arg is None or (
                    kw.arg in REACH_FIELDS and not (isinstance(kw.value, ast.Constant) and not kw.value.value)
                ):
                    found.append(f"{rel(path)}:{node.lineno}")
    return found


def names_the_marker(node: ast.AST) -> bool:
    return any(
        (isinstance(n, ast.Attribute) and n.attr == MARKER) or (isinstance(n, ast.Constant) and n.value == MARKER)
        for n in ast.walk(node)
    )


def real_mode_tests(tree: ast.Module, skip: set[int]) -> tuple[set[str], list[int]]:
    """The functions marked `tbo_real_mode` (by a decorator in any spelling, on the function or its class, or by a
    module `pytestmark`), and the lines that name the marker anywhere else (a form this scan does not understand)."""
    marked: set[str] = set()
    understood: set[int] = set()
    for statement in tree.body:
        pytestmark = isinstance(statement, ast.Assign) and any(
            getattr(t, "id", "") == "pytestmark" for t in statement.targets
        )
        if pytestmark and names_the_marker(statement):
            understood |= {id(n) for n in ast.walk(statement)}
            marked |= {f.name for f in ast.walk(tree) if isinstance(f, ast.FunctionDef | ast.AsyncFunctionDef)}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            for decorator in node.decorator_list:
                if names_the_marker(decorator):
                    understood |= {id(n) for n in ast.walk(decorator)}
                    marked |= {f.name for f in ast.walk(node) if isinstance(f, ast.FunctionDef | ast.AsyncFunctionDef)}
    stray = [
        n.lineno
        for n in ast.walk(tree)
        if id(n) not in understood
        and id(n) not in skip
        and ((isinstance(n, ast.Attribute) and n.attr == MARKER) or (isinstance(n, ast.Constant) and n.value == MARKER))
    ]
    return marked, stray


def test_no_test_turns_a_reach_field_on() -> None:
    """The reach check: every `use_profile` call that turns `polling_enabled`, `kite_enabled` or `model_calls_enabled`
    on is listed here; this repository has none, and its real-mode tests are exactly `REAL_MODE_TESTS`."""
    assert [call for p in TESTS for call in used_reach_calls(p)] == []
    marked, stray = set(), []
    for path in TESTS:
        names, lines = real_mode_tests(*parse(path))
        marked |= {f"{rel(path)}::{name}" for name in names}
        stray += [f"{rel(path)}:{line}" for line in lines]
    assert stray == []
    assert marked == set(REAL_MODE_TESTS)


def test_the_real_mode_inventory_sees_every_spelling_of_the_marker() -> None:
    def marked(name: str) -> set[str]:
        tree = ast.parse(SAMPLES[name])
        return real_mode_tests(tree, set())[0]

    assert marked("p19_setenv_real") == {"test_x"}
    assert marked("p19_setenv_alias") == {"test_x"}  # `import pytest as p`
    assert marked("p19_setenv") == set()
    assert real_mode_tests(ast.parse(SAMPLES["real_getattr"]), set())[1] == [2]


# ---- no child process of a test gets a mode, and no test sets one in-process without the marker (P19)

MODE_KEY = "TBO_MODE"
SPAWNS = re.compile(r"\bsubprocess\b|\bPopen\b|create_subprocess|os\.system|os\.exec|os\.spawn|os\.posix_spawn")
SHELL_MODE = re.compile(r"\bTBO_MODE=(\S*)")
SETTERS = {"setenv", "setdefault", "putenv", "__setitem__"}


def is_mode_key(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Constant) and node.value == MODE_KEY


def harmless(value: ast.AST | str) -> bool:
    """A literal that is neither paper nor live in any case or spacing (a child then refuses it); anything else is not
    known here and counts as paper or live."""
    if isinstance(value, ast.Constant) and (value.value is None or isinstance(value.value, str)):
        value = value.value or ""
    if not isinstance(value, str):
        return False
    return value.strip("'\" ").lower() not in {"paper", "live"}


def environ_call(node: ast.AST, up: dict[int, ast.AST]) -> bool:
    """Whether the nearest call around `node` changes this process's environment (`os.environ...`, `setenv`)."""
    while id(node) in up:
        node = up[id(node)]
        if isinstance(node, ast.Call):
            return "environ" in ast.unparse(node.func) or call_name(node) in ("setenv", "putenv")
    return False


def assigned_modes(node: ast.Assign | ast.AugAssign | ast.AnnAssign) -> list[tuple[ast.AST | str, bool]]:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return [
        (node.value, "environ" in ast.unparse(t.value))
        for t in targets
        if node.value is not None and isinstance(t, ast.Subscript) and is_mode_key(t.slice)
    ]


def called_modes(node: ast.Call, up: dict[int, ast.AST]) -> list[tuple[ast.AST | str, bool]]:
    found: list[tuple[ast.AST | str, bool]] = [
        (kw.value, environ_call(kw, up)) for kw in node.keywords if kw.arg == MODE_KEY
    ]
    if call_name(node) in SETTERS and len(node.args) >= 2 and is_mode_key(node.args[0]):
        found.append((node.args[1], call_name(node) in ("setenv", "putenv") or "environ" in ast.unparse(node.func)))
    return found


def mode_values(node: ast.AST, up: dict[int, ast.AST]) -> list[tuple[ast.AST | str, bool]]:
    """The `TBO_MODE` values `node` gives, each with whether it is given to this process."""
    if isinstance(node, ast.Assign | ast.AugAssign | ast.AnnAssign):
        return assigned_modes(node)
    if isinstance(node, ast.Dict):
        return [(v, environ_call(node, up)) for k, v in zip(node.keys, node.values, strict=True) if is_mode_key(k)]
    if isinstance(node, ast.Tuple) and len(node.elts) == 2 and is_mode_key(node.elts[0]):
        return [(node.elts[1], environ_call(node, up))]
    if isinstance(node, ast.Call):
        return called_modes(node, up)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [(m.group(1), False) for m in SHELL_MODE.finditer(node.value)]  # a shell line: `TBO_MODE=live cmd`
    return []


def function_of(node: ast.AST, up: dict[int, ast.AST]) -> str | None:
    while id(node) in up:
        node = up[id(node)]
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            return node.name
    return None


def mode_settings(tree: ast.Module, skip: set[int]) -> list[tuple[int, ast.AST | str, bool, str | None]]:
    """Every place a `TBO_MODE` value is given: (line, value, in this process, enclosing function)."""
    up = parents(tree)
    return [
        (getattr(node, "lineno", 0), value, inside, function_of(node, up))
        for node in ast.walk(tree)
        if id(node) not in skip
        for value, inside in mode_values(node, up)
    ]


def mode_findings(path: Path, tree: ast.Module, skip: set[int]) -> list[str]:
    spawns = SPAWNS.search(ast.unparse(tree)) is not None
    real, _ = real_mode_tests(tree, skip)
    bad = []
    for line, value, inside, function in mode_settings(tree, skip):
        if spawns and not harmless(value) and (rel(path), function) not in CHILD_PROCESS_MODE_ALLOW_LIST:
            bad.append(f"{rel(path)}:{line}: a child process may get paper or live")
        if inside and function not in real:
            bad.append(f"{rel(path)}:{line}: TBO_MODE set in-process outside a tbo_real_mode test")
    return bad


def test_no_python_child_process_of_the_tests_gets_paper_or_live() -> None:
    """P19, and the lesson "every test that sets TBO_MODE is tbo_real_mode": in a file that starts a process, every
    `TBO_MODE` value is a literal other than paper or live (or named in the allow-list); in any file, a test that sets
    it in its own process carries the marker."""
    assert [finding for p in TESTS for finding in mode_findings(p, *parse(p))] == []


@pytest.mark.parametrize(
    "sample",
    [
        "p19_subscript",
        "p19_environ",
        "p19_dict_call",
        "p19_dict_spread",
        "p19_update",
        "p19_update_kw",
        "p19_update_pairs",
        "p19_setdefault",
        "p19_enum",
        "p19_constant",
        "p19_shell",
        "p19_upper",
        "p19_setenv",
    ],
)
def test_the_mode_scan_sees_each_form(sample: str) -> None:
    assert mode_findings(THIS, ast.parse(SAMPLES[sample]), set()) != []


@pytest.mark.parametrize("sample", ["p19_child_ok", "p19_setenv_real", "p19_setenv_alias"])
def test_the_mode_scan_passes_a_harmless_child_and_a_real_mode_test(sample: str) -> None:
    assert mode_findings(THIS, ast.parse(SAMPLES[sample]), set()) == []


def test_the_scans_skip_only_this_files_own_tables() -> None:
    tree, skip = parse(THIS)
    tables = [n for n in tree.body if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") in SELF_TABLES]
    assert len(tables) == len(SELF_TABLES)
    covered = {id(n) for t in tables for n in ast.walk(t)}
    assert skip == covered | docstrings(tree)


# ---- no service module imports the test helpers (P28)


def imports_of_testing(tree: ast.Module) -> list[int]:
    """Lines under src/ that reach `tbo_platform_kit.testing`: any import form, or the name in a string
    (`importlib.import_module`)."""

    def is_testing(name: str) -> bool:
        return name == "tbo_platform_kit.testing" or name.startswith("tbo_platform_kit.testing.")

    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names = [module, *(f"{module}.{a.name}" for a in node.names)]
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            names = ["tbo_platform_kit.testing"] if "tbo_platform_kit.testing" in node.value else []
        else:
            continue
        if any(is_testing(n) for n in names):
            found.append(getattr(node, "lineno", 0))
    return found


def test_no_module_under_src_imports_the_kit_test_helpers() -> None:
    bad = [rel(p) for p in SRC if imports_of_testing(ast.parse(p.read_text())) and rel(p) not in SRC_TESTING_ALLOW_LIST]
    assert bad == []


@pytest.mark.parametrize(
    "sample",
    [
        "p28_import",
        "p28_import_as",
        "p28_from_testing",
        "p28_from_kit",
        "p28_from_kit_as",
        "p28_from_profile",
        "p28_importlib",
    ],
)
def test_the_import_scan_sees_each_form(sample: str) -> None:
    assert imports_of_testing(ast.parse(SAMPLES[sample])) != []


def test_the_import_scan_passes_other_kit_modules() -> None:
    assert imports_of_testing(ast.parse(SAMPLES["p28_ok"])) == []


# ---- destructive test code sits behind the matching guard, earlier in the same block, on the same connection (P22)

DB_GUARDS = {"require_test_database", "require_test_database_sync"}  # on the connection they are given
URL_GUARDS = {"require_test_database_url"}  # connects to the URL itself: for a statement with no connection here
REDIS_GUARDS = {"require_test_redis", "flush_test_redis"}
# `check_test_database_url` is none of these: it reads a URL and connects to nothing ("never enough on its own").
SQL = re.compile(r"\b(TRUNCATE|DROP|DELETE\s+FROM)\b", re.I)
DOWNGRADE_TEXT = re.compile(r"\bdowngrade\b", re.I)
REDIS_CALLS = {"flushdb", "flushall"}
DOWNGRADE_CALLS = {"downgrade"}
SIMPLE = (ast.Expr, ast.Assign, ast.AnnAssign, ast.AugAssign)  # a guard inside an `if`, `try` or loop does not count


def connection_of(node: ast.AST, up: dict[int, ast.AST]) -> str | None:
    """The variable of the nearest method call around `node` in its statement: `session.execute(text("..."))`."""
    while id(node) in up:
        node = up[id(node)]
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            return root_name(node.func.value)
        if isinstance(node, ast.stmt):
            return None
    return None


def destructive_site(node: ast.AST, up: dict[int, ast.AST]) -> tuple[str, str | None] | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        if SQL.search(node.value):
            connection = connection_of(node, up)
            return ("db" if connection else "url", connection)
        return ("url", None) if DOWNGRADE_TEXT.search(node.value) else None
    if isinstance(node, ast.Call) and call_name(node) in REDIS_CALLS:
        return ("redis", root_name(node.func.value) if isinstance(node.func, ast.Attribute) else None)
    if isinstance(node, ast.Call) and call_name(node) in DOWNGRADE_CALLS:
        return ("url", None)
    return None


def destructive_sites(tree: ast.Module, skip: set[int]) -> list[tuple[ast.AST, str, str | None]]:
    """(node, kind, connection) of each destructive statement: kind `db` (SQL on a connection), `url` (a downgrade,
    or SQL with no connection in reach) or `redis`."""
    up = parents(tree)
    return [
        (node, *site)
        for node in ast.walk(tree)
        if id(node) not in skip and (site := destructive_site(node, up)) is not None
    ]


def guards(statement: ast.stmt, kind: str, connection: str | None) -> bool:
    """Whether a simple statement calls the guard that matches `kind` on `connection`."""
    if not isinstance(statement, SIMPLE):
        return False
    for node in ast.walk(statement):
        if not isinstance(node, ast.Call):
            continue
        name, first = call_name(node), node.args[0] if node.args else None
        if kind == "url" and name in URL_GUARDS:
            return True
        if kind == "db" and name in DB_GUARDS and connection and root_name(first) == connection:
            return True
        if kind == "redis" and name in REDIS_GUARDS and connection and root_name(first) == connection:
            return True
    return False


def unguarded(tree: ast.Module, skip: set[int]) -> list[int]:
    """Lines of destructive statements with no matching guard in a statement before them, in their block or an
    enclosing block of the same function (or the module)."""
    up = parents(tree)
    bad = []
    for node, kind, connection in destructive_sites(tree, skip):
        here: ast.AST = node
        found = False
        while not found and id(here) in up:
            parent = up[id(here)]
            if isinstance(here, ast.stmt):
                for field in ("body", "orelse", "finalbody", "handlers"):
                    block = getattr(parent, field, None)
                    if isinstance(block, list) and here in block:
                        found = any(guards(s, kind, connection) for s in block[: block.index(here)])
                        break
            if isinstance(parent, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.ClassDef):
                break
            here = parent
        if not found:
            bad.append(getattr(node, "lineno", 0))
    return bad


def test_every_truncate_drop_flush_and_downgrade_sits_behind_a_guard() -> None:
    """`tests/` and `scripts/`: each destructive statement has its own kind of guard on its own connection before it
    (`require_test_database` for SQL, `require_test_redis`/`flush_test_redis` for a flush, `require_test_database_url`
    for a downgrade), or is in the named allow-list."""
    found = []
    for path in [*TESTS, *SCRIPTS]:
        if rel(path) not in DESTRUCTIVE_ALLOW_LIST:
            found += [f"{rel(path)}:{line}" for line in unguarded(*parse(path))]
    assert found == []


@pytest.mark.parametrize(
    "sample",
    [
        "p22_none",
        "p22_redis_guard",
        "p22_url_only",
        "p22_after",
        "p22_dead",
        "p22_lower",
        "p22_other_conn",
        "p22_comment",
        "p22_flush",
        "p22_flush_db_guard",
        "p22_downgrade",
        "p22_module",
    ],
)
def test_the_guard_scan_sees_an_unguarded_statement(sample: str) -> None:
    """Red-first proof that the scan is not blind: a wrong kind of guard, a guard after the statement or in dead code,
    a lower-case statement, another connection and a guard in a comment are all reported."""
    assert unguarded(ast.parse(SAMPLES[sample]), set()) != []


def test_removing_the_database_guards_from_the_benchmark_is_seen() -> None:
    """The mutant the review found alive (B-6): `scripts/benchmark.py` without its `require_test_database` calls."""

    class Drop(ast.NodeTransformer):
        def visit_Expr(self, node: ast.Expr) -> ast.AST:
            calls = {call_name(n) for n in ast.walk(node) if isinstance(n, ast.Call)}
            return ast.Pass() if "require_test_database" in calls else node

    path = ROOT / "scripts" / "benchmark.py"
    tree, skip = parse(path)
    assert unguarded(tree, skip) == []
    mutant = Drop().visit(ast.parse(path.read_text()))
    assert unguarded(mutant, docstrings(mutant)) != []


@pytest.mark.parametrize(
    "sample", ["p22_ok", "p22_ok_session", "p22_ok_outer", "p22_flush_ok", "p22_downgrade_ok", "p22_docstring"]
)
def test_the_guard_scan_passes_a_guarded_statement(sample: str) -> None:
    tree = ast.parse(SAMPLES[sample])
    assert unguarded(tree, docstrings(tree)) == []


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
    " paper ": "TBO_MODE must be paper or live, not ' paper '",
}
BAD_MODES = [None, "", "lab", "papr", " paper "]  # never exactly paper or live (P19): each must refuse
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
@pytest.mark.parametrize("value", BAD_MODES, ids=repr)
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


def test_the_entry_point_modes_are_never_paper_or_live() -> None:
    """The one child that gets a non-literal `TBO_MODE` (CHILD_PROCESS_MODE_ALLOW_LIST) takes it from BAD_MODES."""
    assert [v for v in BAD_MODES if v is not None and v in ("paper", "live")] == []
    assert set(BAD_MODES) == set(WORDS)


def test_alembic_on_the_test_profile_refuses_a_database_that_is_not_a_test_database(database_url: str) -> None:
    """Under the test profile (the kit's runner, the path `tests/conftest.py` migrates through) Alembic calls the
    guard on its own connection (P22): the server's `postgres` database is neither named `tbo_test_<x>` nor commented
    `tbo-test`, so nothing is migrated there. (Kit 3.0.1 ends the guard's transaction itself, so migration 0008's
    `autocommit_block` runs through plain `alembic`; no shim.)"""
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
    assert "NotATestTarget" in done.stderr and "refusing" in done.stderr, done.stderr[-600:]
