"""Time the engine on a synthetic market (TE7). Nothing here needs the market feed, Kite or a recorded session.

    uv run python -m scripts.benchmark replay --stocks 20 --sessions 60
    uv run python -m scripts.benchmark sweep  --stocks 10 --sessions 150
    uv run python -m scripts.benchmark live   --stocks 500 --boundaries 12     # needs PostgreSQL and Redis, see below

`replay`: seconds per stock-session for one parameter set (the tape plus one replay).
`sweep`: a full study of every active strategy with its parameter grid; the projection to a year of 500 stocks is a
straight-line extrapolation from the measured stock-sessions, and says so.
`live`: the worker's own path (`CandleIngest.handle` with the real indicator recorder, strategy runner, cooldown store
and outbox) for a universe of `--stocks`, the candles of one boundary delivered back to back as the feed would publish
them.
Latency of a stock = time from the boundary until its evaluation finished (the whole batch queues behind one consumer),
so the p95 is the figure the plan's "candle close to candidate, p95 under 2 s" is about. It excludes the feed's own time
to build and publish the candle and the network, which only a real session can measure. Needs
TBO_TECHNICAL_DATABASE_URL and TBO_TECHNICAL_REDIS_URL of a database migrated with `alembic upgrade head`; it writes
(and deletes at the start) rows in the `technical` schema, so point it at a scratch database.

`--json` prints one JSON object (what `docs/adr/0001-profiling-and-numba.md` quotes).
"""

from __future__ import annotations

import argparse
import asyncio
import cProfile
import json
import os
import platform
import pstats
import statistics
import sys
import tempfile
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from tbo_costs import default_book
from tbo_platform_kit.calendar import TradingCalendar
from tbo_platform_kit.config.calendar import IST

from tbo_technical_engine.backtest.replay import replay_stock
from tbo_technical_engine.backtest.study import run_study
from tbo_technical_engine.schemas.candle import Candle
from tbo_technical_engine.schemas.synthetic import SyntheticSpec, isin_of
from tbo_technical_engine.services.backtests import load_settings
from tbo_technical_engine.services.strategy_loader import load_strategies
from tbo_technical_engine.store.research_cache import ResearchCache
from tbo_technical_engine.synthetic.cache import fill_cache
from tbo_technical_engine.synthetic.generate import StockCandles, session_open_utc, stock_candles, weekdays_ending
from tbo_technical_engine.utils.timefmt import z
from tbo_technical_engine.utils.timeframes import length

ROOT = Path(__file__).resolve().parents[1]
END = date(2026, 10, 15)


def machine() -> dict[str, Any]:
    """Where the numbers came from: timings mean nothing without the machine."""
    model = "unknown"
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.exists():
        for line in cpuinfo.read_text().splitlines():
            if line.startswith("model name"):
                model = line.split(":", 1)[1].strip()
                break
    return {"cpu": model, "cores": os.cpu_count(), "python": platform.python_version(), "numba": _numba()}


def _numba() -> str:
    try:
        import numba

        return f"{numba.__version__} (JIT {'off' if os.environ.get('NUMBA_DISABLE_JIT') == '1' else 'on'})"
    except ImportError:  # numba comes with vectorbt; a missing one is reported, not fatal
        return "not installed"


def replay_mode(args: argparse.Namespace) -> dict[str, Any]:
    """One parameter set over `stocks` stocks, the warm-up sessions not counted."""
    file = load_strategies(ROOT / "config" / "strategies.yaml")
    spec = SyntheticSpec(stocks=args.stocks, sessions=args.sessions + args.warmup, end=END, seed=args.seed)
    days = weekdays_ending(END, spec.sessions)
    seconds = 0.0
    candidates = 0
    for index in range(args.stocks):
        data = stock_candles(spec, index)
        began = time.perf_counter()
        candidates += len(replay_stock(file, data.five, data.fifteen, data.daily, days[args.warmup], days[-1]))
        seconds += time.perf_counter() - began
    stock_sessions = args.stocks * args.sessions
    return {
        "mode": "replay",
        "stocks": args.stocks,
        "sessions": args.sessions,
        "seconds": round(seconds, 2),
        "seconds_per_stock_session": round(seconds / stock_sessions, 5),
        "candidates": candidates,
    }


def sweep_mode(args: argparse.Namespace) -> dict[str, Any]:
    """A full study (base run plus the parameter grid and walk-forward) of every active strategy."""
    file = load_strategies(ROOT / "config" / "strategies.yaml")
    settings = load_settings(ROOT / "config" / "backtest.yaml")
    costs = default_book()
    spec = SyntheticSpec(stocks=args.stocks, sessions=args.sessions + args.warmup, end=END, seed=args.seed)
    days = weekdays_ending(END, spec.sessions)
    cache_dir = Path(args.cache)
    isins = fill_cache(spec, cache_dir)
    cache = ResearchCache(cache_dir)
    sessions = {i: cache.session_dates(i, "5m") for i in isins}
    per_strategy: dict[str, dict[str, Any]] = {}
    total = 0.0
    for name, strategy in file.strategies.items():
        if not strategy.is_active:
            continue
        began = time.perf_counter()
        study = run_study(
            file, name, [], None, isins, cache.stock, sessions, days[args.warmup], days[-1], settings, costs, None,
            monotonic=time.monotonic, deadline=float("inf"),
        )  # fmt: skip
        took = time.perf_counter() - began
        total += took
        sets = max(1, len(study.grid) and _grid_size(study.grid))
        per_strategy[name] = {"seconds": round(took, 2), "parameter_sets": sets, "trades": len(study.trades)}
    stock_sessions = args.stocks * args.sessions
    year = 500 * 250
    return {
        "mode": "sweep",
        "stocks": args.stocks,
        "sessions": args.sessions,
        "seconds": round(total, 2),
        "per_strategy": per_strategy,
        "projection_500_stocks_250_sessions_hours": round(total / stock_sessions * year / 3600, 2),
        "projection_note": "straight-line extrapolation of the measured stock-sessions, one core, this machine",
    }


def _grid_size(grid: dict[str, list[str]]) -> int:
    size = 1
    for values in grid.values():
        size *= len(values)
    return size


def _warm_worker(
    args: argparse.Namespace, settings: Any, redis: Any, sessions: Any
) -> tuple[Any, list[StockCandles], date]:
    """A signal worker with a universe of `--stocks`, warm history for all of them and the session's first candles."""
    from tbo_technical_engine.apps.signal_worker import SignalWorker
    from tbo_technical_engine.schemas.candle import FeedStatus, UniverseSnapshot
    from tbo_technical_engine.services.warmup import is_warm

    # The feed and fundamental clients are never called here (the supervisor does not run); the calendar is only a
    # placeholder because the kit's holiday file ships empty.
    worker = SignalWorker(
        settings,
        redis,
        sessions,
        cast(Any, None),
        cast(Any, None),
        calendar=TradingCalendar.from_document({"years": {"2026": {"holidays": []}}}),
    )
    history_sessions, live_sessions = 20, 1
    spec = SyntheticSpec(stocks=args.stocks, sessions=history_sessions + live_sessions, end=END, seed=args.seed)
    worker.universe.offer(
        UniverseSnapshot(
            trade_date=END.isoformat(), universe_version=1, isins=tuple(isin_of(i) for i in range(args.stocks))
        )
    )
    data: list[StockCandles] = [stock_candles(spec, i) for i in range(args.stocks)]
    today = weekdays_ending(END, 1)[0]
    for stock in data:
        worker.buffer.add_many(c for c in (*stock.five, *stock.fifteen, *stock.daily) if _day(c) < today)
    worker.readiness.on_feed_status(FeedStatus(state="healthy", changed_at=datetime.now(UTC)))
    worker.readiness.mark_warm(worker.readiness.generation)
    cold = [s.isin for s in data if not is_warm(worker.buffer, s.isin, worker.spec)]
    if cold:
        raise SystemExit(f"{len(cold)} stocks are not warm: the history is too short")
    for stock in data:  # the session's first candles (before the first measured boundary)
        for c in (c for c in stock.five if _day(c) == today):
            if c.start_at < session_open_utc(today) + timedelta(minutes=15):
                worker.buffer.add(c)
    if not args.no_prime:
        for stock in data:
            worker.engine.prime(stock.isin)
    return worker, data, today


async def _deliver(
    args: argparse.Namespace, worker: Any, data: list[StockCandles], today: date, clock: dict[str, datetime]
) -> tuple[list[float], list[float]]:
    """Deliver `--boundaries` boundaries to the ingest; returns each stock's latency and each boundary's total time."""
    todays = {s.isin: [c for c in s.five if _day(c) == today] for s in data}
    fifteen = {s.isin: [c for c in s.fifteen if _day(c) == today] for s in data}
    latencies: list[float] = []
    per_boundary_total: list[float] = []
    first = 3
    for k in range(args.boundaries):
        index = first + k
        batch: list[Candle] = []
        for stock in data:
            batch.append(todays[stock.isin][index])
            batch += [
                c
                for c in fifteen[stock.isin]
                if c.start_at + length("15m") == todays[stock.isin][index].start_at + length("5m")
            ]
        close_at = todays[data[0].isin][index].start_at + length("5m")
        clock["now"] = close_at + timedelta(seconds=1)
        began = time.perf_counter()
        for c in batch:
            await worker.ingest.handle({"data": c.model_dump(mode="json")}, None)
            if args.sync_recorder:
                await worker.recorder.flush()
            if c.timeframe == "5m":
                latencies.append(time.perf_counter() - began)
        per_boundary_total.append(time.perf_counter() - began)
    return latencies, per_boundary_total


async def live_mode(args: argparse.Namespace) -> dict[str, Any]:
    """The worker's candle path for a whole universe, boundary after boundary, against a real PostgreSQL and Redis."""
    import redis.asyncio as aioredis
    from sqlalchemy import text
    from tbo_platform_kit.db import create_engine, session_factory

    from tbo_technical_engine.config.settings import Settings

    settings = Settings(research_cache_dir="/nonexistent")
    settings.require("database_url", "redis_url")
    assert settings.redis_url is not None
    redis = aioredis.Redis.from_url(settings.redis_url.get_secret_value())
    engine = create_engine(settings)
    sessions = session_factory(engine)
    async with sessions() as session, session.begin():
        await session.execute(
            text("TRUNCATE technical.indicator_snapshot, technical.signal_candidate, technical.outbox")
        )
    await redis.flushdb()
    worker, data, today = _warm_worker(args, settings, redis, sessions)
    clock = {"now": datetime.now(UTC)}
    worker.ingest.clock = lambda: clock["now"]
    candidates_before = await _count(sessions)
    stop = asyncio.Event()
    writer = asyncio.create_task(worker.recorder.run(stop))  # the audit rows are written in the background, as live
    profile = cProfile.Profile() if args.profile else None
    if profile is not None:
        profile.enable()
    latencies, per_boundary_total = await _deliver(args, worker, data, today, clock)
    if profile is not None:
        profile.disable()
        pstats.Stats(profile, stream=sys.stderr).sort_stats("tottime").print_stats(30)
    stop.set()
    await writer
    stored = await _count(sessions) - candidates_before
    await engine.dispose()
    await redis.aclose()
    latencies.sort()
    q = statistics.quantiles(latencies, n=100)
    return {
        "mode": "live",
        "stocks": args.stocks,
        "boundaries": args.boundaries,
        "evaluations": len(latencies),
        "candidates_stored": stored,
        "latency_seconds": {
            "p50": round(q[49], 3),
            "p95": round(q[94], 3),
            "p99": round(q[98], 3),
            "max": round(latencies[-1], 3),
        },
        "first_boundary_seconds": round(per_boundary_total[0], 3),
        "boundary_total_seconds": {
            "mean": round(statistics.mean(per_boundary_total), 3),
            "max": round(max(per_boundary_total), 3),
        },
        "excluded": "the feed's time to build and publish the candle, the network, and the Redis stream read",
    }


def _day(candle: Candle) -> date:
    return candle.start_at.astimezone(IST).date()


async def _count(sessions: Any) -> int:
    from sqlalchemy import text

    async with sessions() as session:
        return int((await session.execute(text("SELECT count(*) FROM technical.signal_candidate"))).scalar_one())


def main(argv: list[str] | None = None) -> int:
    """Run one mode and print the result."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["replay", "sweep", "live"])
    ap.add_argument("--stocks", type=int, default=20)
    ap.add_argument("--sessions", type=int, default=60)
    ap.add_argument("--warmup", type=int, default=15, help="sessions before the measured range (indicator warm-up)")
    ap.add_argument("--boundaries", type=int, default=12, help="live: how many 5-minute boundaries to deliver")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument(
        "--cache",
        default=str(Path(tempfile.gettempdir()) / "tbo-benchmark-cache"),
        help="sweep: where the synthetic candles are written",
    )
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--profile", action="store_true", help="live: print where the measured boundaries spent their time")
    ap.add_argument(
        "--sync-recorder", action="store_true", help="live: write the audit rows in the candle's path (TE6)"
    )
    ap.add_argument("--no-prime", action="store_true", help="live: skip the indicator priming the warm-up does")
    args = ap.parse_args(argv)
    if args.mode == "replay":
        result = replay_mode(args)
    elif args.mode == "sweep":
        result = sweep_mode(args)
    else:
        result = asyncio.run(live_mode(args))
    result["machine"] = machine()
    result["started_at"] = z(datetime.now(UTC))
    sys.stdout.write(json.dumps(result, indent=None if args.json else 2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
