from __future__ import annotations

from tbo_technical_engine.schemas.candle import UniverseSnapshot
from tbo_technical_engine.services.universe import UniverseHolder
from tests.support import ISIN_A, ISIN_B


def snap(day: str, version: int, *isins: str) -> UniverseSnapshot:
    return UniverseSnapshot(trade_date=day, universe_version=version, isins=isins)


def test_newer_replaces_older_and_never_the_other_way() -> None:
    u = UniverseHolder()
    assert u.offer(snap("2026-10-15", 1, ISIN_A)) is True
    assert u.offer(snap("2026-10-15", 2, ISIN_A, ISIN_B)) is True
    assert u.offer(snap("2026-10-15", 1, ISIN_A)) is False  # a late, older event
    assert u.offer(snap("2026-10-15", 2, ISIN_A)) is False  # a redelivery
    assert u.isins == {ISIN_A, ISIN_B}


def test_a_later_date_wins_even_with_version_one() -> None:
    u = UniverseHolder()
    u.offer(snap("2026-10-14", 5, ISIN_A))
    assert u.offer(snap("2026-10-15", 1, ISIN_B)) is True
    assert ISIN_B in u and ISIN_A not in u


def test_empty_until_offered() -> None:
    u = UniverseHolder()
    assert not u.isins and "INE000000000" not in u
