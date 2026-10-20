"""What the image carries (lessons 12 and 29, review 9.21): the files the processes read and the tools the runbooks
name, each reachable as `python -m` inside the container."""

from __future__ import annotations

import fnmatch
import importlib
import re

from tests.support import ROOT

OPERATING_TOOLS = ("session_report", "fill_research_cache", "backtest_report")
"""The tools `docs/runbooks.md` and `docs/monitoring.md` tell an operator to run in the deployed stack."""


def _allowed(path: str, rules: list[str]) -> bool:
    """`.dockerignore` as Docker reads it: the last matching rule wins, `!` lets a path through."""
    allowed = True
    for rule in rules:
        negate = rule.startswith("!")
        pattern = rule[1:] if negate else rule
        if fnmatch.fnmatch(path, pattern.rstrip("/")) or fnmatch.fnmatch(path, pattern):
            allowed = negate
    return allowed


def test_every_copy_source_in_the_dockerfile_is_let_through_by_dockerignore() -> None:
    rules = [r.strip() for r in (ROOT / ".dockerignore").read_text().splitlines() if r.strip() and r[0] != "#"]
    sources = []
    for line in (ROOT / "Dockerfile").read_text().splitlines():
        if line.startswith("COPY ") and "--from=" not in line:
            sources += line.split()[1:-1]
    assert sources and [s for s in sources if not _allowed(s.rstrip("/"), rules)] == []


def test_the_package_is_installed_into_the_image_and_carries_the_operating_tools() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "COPY src src" in dockerfile and "--no-editable" in dockerfile
    for tool in OPERATING_TOOLS:
        module = importlib.import_module(f"tbo_technical_engine.apps.{tool}")
        assert callable(module.main)


def test_the_operating_docs_name_the_tools_by_their_module_not_a_script_path() -> None:
    for doc in ("docs/runbooks.md", "docs/monitoring.md"):
        text = (ROOT / doc).read_text()
        for tool in OPERATING_TOOLS:
            assert not re.search(rf"scripts[./]{tool}", text), f"{doc} names scripts/{tool}, which is not in the image"
