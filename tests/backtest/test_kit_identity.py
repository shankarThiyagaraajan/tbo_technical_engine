"""The backtest result does not change when costs and fills come from the platform kit (R2c).

`GOLDEN` was taken from TE5's own `costs.py` and `simulate.py` before they were replaced, over the same inputs. The
trades of a whole replayed run and of a grid of hand-built price paths are hashed; any change to a fill, a quantity or a
charge by even a paisa changes the digest.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any

from tbo_technical_engine.backtest.engine import run_strategy
from tbo_technical_engine.backtest.overrides import for_backtest
from tbo_technical_engine.backtest.replay import day_of
from tbo_technical_engine.backtest.simulate import simulate
from tbo_technical_engine.schemas.backtest import StockData
from tests.backtest.builders import COSTS, SETTINGS, bar, candidate, execution, flat
from tests.strategies.session import CONFIG, History
from tests.support import ISIN_A

GOLDEN = "3b44d3667d3df67a"

HISTORY = History(days=24, seed=12)
FIRST, LAST = day_of(HISTORY.sessions[14][0]), day_of(HISTORY.sessions[16][0])


def _load(_: str) -> StockData:
    return StockData(HISTORY.five, HISTORY.fifteen, HISTORY.daily)


def _paths() -> list[tuple[str, list[Any]]]:
    """Each way a trade can end, for both sides, with slippage."""
    buy_paths = {
        "t2": [
            bar(11, "100.00", "100.50", "99.50", "100.20"),
            bar(12, "100.20", "101.60", "100.10", "101.50"),
            bar(13, "101.50", "102.60", "101.40", "102.50"),
        ],
        "stop": [bar(11, "100.00", "100.30", "98.90", "99.10")],
        "gap_stop": [bar(11, "100.00", "100.30", "99.50", "100.10"), bar(12, "98.50", "98.60", "98.40", "98.50")],
        "stop_at_entry": [
            bar(11, "100.00", "100.30", "99.50", "100.10"),
            bar(12, "100.10", "101.60", "100.10", "101.40"),
            bar(13, "101.40", "101.50", "99.90", "100.00"),
        ],
        "time_exit": [
            bar(11, "100.00", "100.30", "99.50", "100.10"),
            *flat(12, 71),
            bar(72, "100.40", "100.50", "100.30", "100.40"),
        ],
        "end_of_data": [bar(11, "100.00", "100.30", "99.50", "100.10"), *flat(12, 20)],
        "never": [bar(11, "101.00", "101.50", "100.50", "101.20")],
        "beyond_stop": [bar(11, "98.50", "98.60", "98.40", "98.50")],
    }
    sell_paths = {
        "t2": [
            bar(11, "100.00", "100.50", "99.50", "99.80"),
            bar(12, "99.80", "99.90", "98.40", "98.50"),
            bar(13, "98.50", "98.60", "97.40", "97.50"),
        ],
        "stop": [bar(11, "100.00", "101.10", "99.70", "100.90")],
        "end_of_data": [bar(11, "100.00", "100.30", "99.50", "99.90"), *flat(12, 20, "99.90")],
    }
    out: list[tuple[str, list[Any]]] = [("buy", p) for p in buy_paths.values()]
    out += [("sell", p) for p in sell_paths.values()]
    return out


def _sell_candidate() -> Any:
    return candidate("sell", low="99.80", high="100.00", stop="101.00", t1="98.50", t2="97.50")


def _plain(trade: Any) -> Any:
    return None if trade is None else {k: str(v) for k, v in vars(trade).items()}


def _digest() -> str:
    trades: list[Any] = []
    for slip in ("0", "2.5"):
        for side, after in _paths():
            cand = candidate() if side == "buy" else _sell_candidate()
            for fraction in ("0.5", "0.33"):
                done = simulate(
                    cand, after, execution(slippage_bps=Decimal(slip), t1_fraction=Decimal(fraction)), COSTS
                )
                trades.append(_plain(done))
    for name in ("vwap_pivot", "vwap_breakout"):
        config = for_backtest(CONFIG, name, [], None)
        out = run_strategy(config, name, [ISIN_A], _load, FIRST, LAST, SETTINGS.execution, COSTS, None)
        trades.append([_plain(t) for t in out.trades])
    return hashlib.sha256(json.dumps(trades, sort_keys=True).encode()).hexdigest()[:16]


def test_the_trades_are_identical_to_those_before_the_kit() -> None:
    assert _digest() == GOLDEN
