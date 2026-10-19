"""The evaluator: windows, cooldowns, ids, and that a recorded session always gives the same candidates."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from tbo_platform_kit.config.calendar import IST

from tbo_technical_engine.schemas.signal import SignalCandidate
from tbo_technical_engine.schemas.strategy import StrategyFile
from tbo_technical_engine.services.evaluator import StrategyEvaluator
from tbo_technical_engine.strategies.base import Strategy
from tests.strategies.session import CONFIG, History, drive, fresh

HISTORY = History(days=24, seed=12)
DAYS = [14, 15, 16]


def key(c: SignalCandidate) -> tuple[Any, ...]:
    """A candidate without the time it was made."""
    return tuple(v for k, v in c.model_dump().items() if k != "created_at")


def run_days(days: list[int], history: History = HISTORY) -> list[SignalCandidate]:
    out: list[SignalCandidate] = []
    buffer, evaluator = fresh(history.before(days[0]))
    for d in days:
        out += drive(buffer, evaluator, history.events(d))
    return out


@pytest.fixture(scope="module")
def baseline() -> list[SignalCandidate]:
    found = run_days(DAYS)
    assert len(found) >= 10, (
        "the synthetic sessions should produce candidates for the comparisons below to mean anything"
    )
    return found


def test_a_replay_gives_identical_candidates(baseline: list[SignalCandidate]) -> None:
    assert [key(c) for c in run_days(DAYS)] == [key(c) for c in baseline]


def test_the_order_the_15_minute_candle_arrives_in_changes_nothing(baseline: list[SignalCandidate]) -> None:
    """Live, the 15-minute candle closing with a 5-minute one may arrive before it. Strategies read the 15-minute
    series as of the entry candle's start, so the result is the same."""
    per15 = 25

    def early(day: int) -> Any:
        fifteen = HISTORY.fifteen[day * per15 : (day + 1) * per15]
        closing = {c.start_at + timedelta(minutes=15): c for c in fifteen}
        for c5 in HISTORY.sessions[day]:
            done = closing.get(c5.start_at + timedelta(minutes=5))
            if done is not None:
                yield done
            yield c5

    buffer, evaluator = fresh(HISTORY.before(DAYS[0]))
    found: list[SignalCandidate] = []
    for d in DAYS:
        found += drive(buffer, evaluator, early(d))
    assert [key(c) for c in found] == [key(c) for c in baseline]


def test_a_restart_mid_session_gives_the_same_candidates_as_no_restart(baseline: list[SignalCandidate]) -> None:
    """Cut the third session in the middle; the second process is built from the buffer and the saved cooldowns."""
    day = DAYS[-1]
    cut = 40
    events = list(HISTORY.events(day))
    first_part = events[:cut]
    buffer, evaluator = fresh(HISTORY.before(DAYS[0]))
    found: list[SignalCandidate] = []
    for d in DAYS[:-1]:
        found += drive(buffer, evaluator, HISTORY.events(d))
    found += drive(buffer, evaluator, first_part)
    saved = dict(evaluator.fired)  # what Redis holds
    buffer2, evaluator2 = fresh(
        [c for tf in ("5m", "15m", "1d") for c in buffer.series(HISTORY.sessions[0][0].isin, tf)]
    )
    evaluator2.fired.update(saved)
    found += drive(buffer2, evaluator2, events[cut:])
    assert [key(c) for c in found] == [key(c) for c in baseline]


def test_the_ids_are_the_same_for_the_same_candle_and_valid_ulids(baseline: list[SignalCandidate]) -> None:
    import re

    ids = [c.signal_id for c in baseline]
    assert len(set(ids)) == len(ids)
    assert all(re.fullmatch(r"[0-7][0-9A-HJKMNP-TV-Z]{25}", i) for i in ids)


def test_every_candidate_is_a_valid_contract_object(baseline: list[SignalCandidate]) -> None:
    from tbo_contracts import validate

    from tbo_technical_engine.api.shapes import candidate_dict

    for c in baseline[:25]:
        validate("technical/signal_candidate.v1.json", candidate_dict(c))


def test_entries_only_inside_the_windows(baseline: list[SignalCandidate]) -> None:
    for c in baseline:
        closes = (c.candle_start_at + timedelta(minutes=5)).astimezone(IST)
        minute = closes.hour * 60 + closes.minute
        assert 9 * 60 + 30 <= minute <= 14 * 60 + 30
        if c.strategy == "orb_15m":
            assert minute <= 11 * 60 + 30


def test_a_cooldown_separates_signals_of_one_strategy_stock_and_side(baseline: list[SignalCandidate]) -> None:
    last: dict[tuple[str, str, str], Any] = {}
    for c in sorted(baseline, key=lambda c: c.candle_start_at):
        k = (c.isin, c.strategy, c.side)
        if k in last:
            assert c.candle_start_at - last[k] >= timedelta(minutes=30)
            if c.strategy == "orb_15m":
                assert c.candle_start_at.astimezone(IST).date() != last[k].astimezone(IST).date()
        last[k] = c.candle_start_at


def test_a_rejected_write_does_not_start_the_cooldown() -> None:
    """`evaluate` alone moves nothing; only `accept` does."""
    buffer, evaluator = fresh(HISTORY.before(14))
    events = list(HISTORY.events(14))
    found = []
    for c in events:
        buffer.add(c)
        if c.timeframe == "5m":
            found = evaluator.evaluate(c, c.start_at + timedelta(minutes=5))
            if found:
                again = evaluator.evaluate(c, c.start_at + timedelta(minutes=5))
                assert [key(x) for x in again] == [key(x) for x in found]
                assert evaluator.fired == {}
                evaluator.accept(found[0])
                assert (found[0].isin, found[0].strategy, found[0].side) in evaluator.fired
                return
    pytest.fail("no candidate to test with")


def test_a_candle_that_is_no_longer_the_newest_is_not_evaluated() -> None:
    buffer, evaluator = fresh(HISTORY.before(14))
    session = HISTORY.sessions[14]
    buffer.add_many(session[:30])
    assert evaluator.evaluate(session[10], session[10].start_at + timedelta(minutes=5)) == []


def test_a_strategy_that_raises_does_not_stop_the_others() -> None:
    class Broken(Strategy):
        name = "ema_9_21_crossover"

        def evaluate(self, ctx: Any, p: Any, side: Any) -> Any:
            raise RuntimeError("bug")

    from tbo_technical_engine.strategies import REGISTRY

    buffer = fresh(HISTORY.before(14))[0]
    evaluator = StrategyEvaluator(
        fresh(HISTORY.before(14))[1].engine, buffer, CONFIG, {**REGISTRY, "ema_9_21_crossover": Broken()}
    )
    found = drive(buffer, evaluator, HISTORY.events(14))
    assert all(c.strategy != "ema_9_21_crossover" for c in found)


def test_an_inactive_strategy_is_silent(baseline: list[SignalCandidate]) -> None:
    spec = CONFIG.strategies["vwap_pivot"].model_copy(update={"is_active": False})
    config = StrategyFile(
        params_version=CONFIG.params_version,
        active_from_at=CONFIG.active_from_at,
        strategies={**CONFIG.strategies, "vwap_pivot": spec},
    )
    buffer, evaluator = fresh(HISTORY.before(14), config)
    found = drive(buffer, evaluator, HISTORY.events(14))
    assert found and all(c.strategy != "vwap_pivot" for c in found)


def test_candidates_carry_the_params_version(baseline: list[SignalCandidate]) -> None:
    assert {c.params_version for c in baseline} == {CONFIG.params_version}


def _halved(history: History, day: int) -> list[Any]:
    """The session of `day` with every price halved: a 1:2 split on its ex-date (intraday candles are unadjusted)."""
    half = {"open", "high", "low", "close"}
    return [c.model_copy(update={k: getattr(c, k) / 2 for k in half}) for c in history.events(day)]


def test_a_stock_on_its_split_date_is_not_evaluated() -> None:
    """9.5: the previous session's pivots and gap are on the old price scale, so no candidate that day."""
    assert run_days([14, 15]), "the control days do produce candidates"
    buffer, evaluator = fresh(HISTORY.before(15))
    assert drive(buffer, evaluator, _halved(HISTORY, 15)) == []


def test_a_special_evening_session_is_not_the_base_of_the_gap() -> None:
    """9.7: after a Muhurat hour, prev_close is the last regular close, not the evening's."""
    last_regular = HISTORY.sessions[14][-1]
    evening = [
        last_regular.model_copy(
            update={"start_at": last_regular.start_at + timedelta(hours=3) + timedelta(minutes=5 * i), "close": 1}
        )
        for i in range(12)
    ]
    buffer, evaluator = fresh([*HISTORY.before(15), *evening])
    first = HISTORY.sessions[15][0]
    buffer.add(first)
    ctx = evaluator.context(first)
    assert ctx is not None and ctx.prev_close == float(last_regular.close)
