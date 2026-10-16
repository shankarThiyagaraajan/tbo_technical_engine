"""The central rule: a recorded session gives the same candidates live, in replay and in the backtester."""

from __future__ import annotations

from typing import Any

from tbo_technical_engine.backtest.engine import DayRules, RunOutcome, run_strategy
from tbo_technical_engine.backtest.overrides import for_backtest
from tbo_technical_engine.backtest.replay import day_of, replay_stock
from tbo_technical_engine.schemas.backtest import StockData
from tbo_technical_engine.schemas.signal import SignalCandidate
from tests.backtest.builders import COSTS, SETTINGS
from tests.strategies.session import CONFIG, History, drive, fresh
from tests.support import ISIN_A

HISTORY = History(days=24, seed=12)
FIRST, LAST = day_of(HISTORY.sessions[14][0]), day_of(HISTORY.sessions[16][0])


def key(c: SignalCandidate) -> tuple[Any, ...]:
    return tuple(v for k, v in c.model_dump().items() if k != "created_at")


def live() -> list[SignalCandidate]:
    """What the worker does: history in the buffer, then each candle of each session in the feed's order."""
    out: list[SignalCandidate] = []
    buffer, evaluator = fresh(HISTORY.before(14))
    for d in (14, 15, 16):
        # The worker's 09:00 warm-up brings in yesterday's daily candle; today's does not exist yet.
        buffer.add(HISTORY.daily[d - 1])
        out += drive(buffer, evaluator, HISTORY.events(d))
    return out


def test_the_backtest_replay_gives_exactly_the_candidates_of_the_live_worker() -> None:
    expected = live()
    assert len(expected) >= 10  # the synthetic sessions must produce enough for this comparison to mean something
    found = replay_stock(CONFIG, HISTORY.five, HISTORY.fifteen, HISTORY.daily, FIRST, LAST)
    assert [key(c) for c in found] == [key(c) for c in expected]


def test_candles_before_the_start_only_warm_the_indicators_up() -> None:
    found = replay_stock(CONFIG, HISTORY.five, HISTORY.fifteen, HISTORY.daily, FIRST, LAST)
    assert found and all(FIRST <= day_of_candidate(c) <= LAST for c in found)


def day_of_candidate(c: SignalCandidate) -> Any:
    from tbo_platform_kit.calendar import IST

    return c.candle_start_at.astimezone(IST).date()


def test_the_requested_strategy_is_the_only_one_that_runs() -> None:
    only = for_backtest(CONFIG, "vwap_breakout", [], None)
    found = replay_stock(only, HISTORY.five, HISTORY.fifteen, HISTORY.daily, FIRST, LAST)
    assert found and {c.strategy for c in found} == {"vwap_breakout"}


def load(_: str) -> StockData:
    return StockData(HISTORY.five, HISTORY.fifteen, HISTORY.daily)


def test_every_candidate_becomes_a_trade_or_is_counted_as_skipped() -> None:
    config = for_backtest(CONFIG, "vwap_pivot", [], None)
    out = run_strategy(config, "vwap_pivot", [ISIN_A], load, FIRST, LAST, SETTINGS.execution, COSTS, None)
    assert out.candidates == len(out.trades) + out.unfilled_or_invalid + out.skipped_busy + out.gated_out
    assert out.trades


def test_one_position_per_stock_skips_a_signal_while_a_trade_is_open() -> None:
    config = for_backtest(CONFIG, "vwap_pivot", [], None)
    one = run_strategy(config, "vwap_pivot", [ISIN_A], load, FIRST, LAST, SETTINGS.execution, COSTS, None)
    many = run_strategy(
        config,
        "vwap_pivot",
        [ISIN_A],
        load,
        FIRST,
        LAST,
        SETTINGS.execution.model_copy(update={"one_position_per_stock": False}),
        COSTS,
        None,
    )
    assert one.skipped_busy > 0 and many.skipped_busy == 0
    assert len(many.trades) > len(one.trades)
    for a, b in zip(one.trades, one.trades[1:], strict=False):  # no two trades of one stock overlap
        assert a.exit_at <= b.entry_at


def test_the_gate_decides_which_sides_trade_and_a_stock_outside_the_universe_does_not() -> None:
    config = for_backtest(CONFIG, "vwap_pivot", [], None)
    days = {day_of(c) for c in HISTORY.five if FIRST <= day_of(c) <= LAST}
    # Several positions at once, so that dropping one side cannot change which trades of the other side are skipped.
    execution = SETTINGS.execution.model_copy(update={"one_position_per_stock": False})

    def run(status: str) -> RunOutcome:
        rules = DayRules({d: ({ISIN_A: status} if status else {}) for d in days})
        return run_strategy(config, "vwap_pivot", [ISIN_A], load, FIRST, LAST, execution, COSTS, rules)

    both, long_only, short_only, blocked, outside = (
        run("both"),
        run("long_only"),
        run("short_only"),
        run("block"),
        run(""),
    )
    assert {t.side for t in long_only.trades} <= {"buy"} and {t.side for t in short_only.trades} <= {"sell"}
    assert len(long_only.trades) + len(short_only.trades) == len(both.trades)
    assert not blocked.trades and not outside.trades
    assert blocked.gated_out == outside.gated_out == blocked.candidates > 0


def test_a_day_without_rules_has_no_trades() -> None:
    config = for_backtest(CONFIG, "vwap_pivot", [], None)
    out = run_strategy(config, "vwap_pivot", [ISIN_A], load, FIRST, LAST, SETTINGS.execution, COSTS, DayRules({}))
    assert out.candidates > 0 and not out.trades
