"""TE0 stubs: every contract operation exists and is protected, but answers 501 until its phase lands."""

from __future__ import annotations

from tbo_platform_kit.problems import Problem


def not_implemented(phase: str) -> Problem:
    """The answer of an operation whose phase is not built yet (`phase` names it, e.g. "TE6"): the catalogue's
    `not_implemented` problem (501).

    The auth dependency runs first, so an unauthenticated caller still gets 401, never a hint that this is a stub.
    Why 501 and not 404: the route is in the contract, so "not found" would tell a client the contract is wrong.
    """
    return Problem("not_implemented", f"Not implemented yet (phase {phase}).")
