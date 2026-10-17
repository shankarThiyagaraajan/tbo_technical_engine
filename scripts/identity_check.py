"""Prove that Numba changes nothing (TE7): the same synthetic sessions give the same candidates, indicator series,
trades and statistics with the JIT on and with `NUMBA_DISABLE_JIT=1`.

    uv run python -m scripts.identity_check                 # run both ways and compare (exit 1 on a difference)
    uv run python -m scripts.identity_check --digests       # print this process's digests only

Three digests, each a SHA-256 over exact text (candidates as JSON, indicator values as `repr` of each float, so a
difference in the last bit shows): `candidates` (replay of every stock-session), `indicators` (the batch form of every
suite on 5-minute, 15-minute and daily candles) and `study` (trades, metrics, walk-forward and the vectorbt return
statistics of one strategy with its parameter grid). Numba is only used by vectorbt today (see the ADR); the check is
what keeps it true if a kernel of ours is ever compiled.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory

from tbo_costs import default_book

from tbo_technical_engine.backtest.replay import replay_stock
from tbo_technical_engine.backtest.study import run_study
from tbo_technical_engine.indicators.bars import Bars
from tbo_technical_engine.schemas.synthetic import SyntheticSpec
from tbo_technical_engine.services.backtests import load_settings
from tbo_technical_engine.services.indicator_engine import default_suites
from tbo_technical_engine.services.strategy_loader import load_strategies
from tbo_technical_engine.store.research_cache import ResearchCache
from tbo_technical_engine.synthetic.cache import fill_cache
from tbo_technical_engine.synthetic.generate import stock_candles, weekdays_ending

ROOT = Path(__file__).resolve().parents[1]
STUDY_STRATEGY = "vwap_pivot"
"""The strategy whose whole study is digested: the one with trades on the synthetic market in every size we use."""


def digests(stocks: int = 4, sessions: int = 5, warmup: int = 12, seed: int = 21) -> dict[str, str]:
    """The three digests for a synthetic market of `stocks` x `sessions` measured stock-sessions."""
    from datetime import date

    file = load_strategies(ROOT / "config" / "strategies.yaml")
    spec = SyntheticSpec(stocks=stocks, sessions=sessions + warmup, end=date(2026, 10, 15), seed=seed)
    days = weekdays_ending(spec.end, spec.sessions)
    candidates = hashlib.sha256()
    indicators = hashlib.sha256()
    count = 0
    for index in range(stocks):
        data = stock_candles(spec, index)
        for found in replay_stock(file, data.five, data.fifteen, data.daily, days[warmup], days[-1]):
            candidates.update(found.model_dump_json().encode())
            count += 1
        for timeframe, series in (("5m", data.five), ("15m", data.fifteen), ("1d", data.daily)):
            bars = Bars.from_candles(series)
            for name, values in default_suites()[timeframe].batch(bars).items():
                indicators.update(f"{index}/{timeframe}/{name}:".encode())
                indicators.update(",".join(repr(float(v)) for v in values).encode())
    with TemporaryDirectory() as directory:
        isins = fill_cache(spec, Path(directory))
        cache = ResearchCache(Path(directory))
        settings = load_settings(ROOT / "config" / "backtest.yaml")
        study = run_study(
            file, STUDY_STRATEGY, [], None, isins, cache.stock, {i: cache.session_dates(i, "5m") for i in isins},
            days[warmup], days[-1], settings, default_book(), None,
            monotonic=time.monotonic, deadline=float("inf"),
        )  # fmt: skip
        text = json.dumps(
            {
                "trades": [asdict(t) for t in study.trades],
                "metrics": repr(study.metrics),
                "walk_forward": repr(study.walk_forward),
                "statistics": {k: repr(v) for k, v in study.statistics.items()},
            },
            sort_keys=True,
            default=str,
        )
    return {
        "candidates": candidates.hexdigest(),
        "indicators": indicators.hexdigest(),
        "study": hashlib.sha256(text.encode()).hexdigest(),
        "candidate_count": str(count),
        "study_trades": str(len(study.trades)),
    }


def run_with_jit(enabled: bool, args: list[str]) -> dict[str, str]:
    """This script's digests in a fresh interpreter with the JIT on or off (the variable is read when Numba loads)."""
    env = {**os.environ, "NUMBA_DISABLE_JIT": "0" if enabled else "1"}
    done = subprocess.run(  # noqa: S603  (our own interpreter and module, fixed arguments)
        [sys.executable, "-m", "scripts.identity_check", "--digests", *args],
        cwd=ROOT, env=env, capture_output=True, text=True, check=False,
    )  # fmt: skip
    if done.returncode != 0:
        raise RuntimeError(f"digests failed (JIT {'on' if enabled else 'off'}): {done.stderr[-800:]}")
    result: dict[str, str] = json.loads(done.stdout.strip().splitlines()[-1])
    return result


def compare(stocks: int = 4, sessions: int = 5) -> tuple[dict[str, str], dict[str, str]]:
    """Digests with the JIT on and off."""
    args = ["--stocks", str(stocks), "--sessions", str(sessions)]
    return run_with_jit(True, args), run_with_jit(False, args)


def main(argv: list[str] | None = None) -> int:
    """Print this process's digests (`--digests`) or compare the JIT on and off; the exit code is 1 on a difference."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--digests", action="store_true")
    ap.add_argument("--stocks", type=int, default=4)
    ap.add_argument("--sessions", type=int, default=5)
    args = ap.parse_args(argv)
    if args.digests:
        sys.stdout.write(json.dumps(digests(args.stocks, args.sessions)) + "\n")
        return 0
    on, off = compare(args.stocks, args.sessions)
    for key in on:
        same = "same" if on[key] == off[key] else "DIFFERENT"
        sys.stdout.write(f"{key:16} {same}  on={on[key][:16]} off={off[key][:16]}\n")
    return 0 if on == off else 1


if __name__ == "__main__":
    raise SystemExit(main())
