"""Run the active strategies over the research cache and write the report (the TE5 exit-criterion report).

    uv run python scripts/backtest_report.py --from 2025-10-01 --to 2026-09-30 --out report.md
    uv run python scripts/backtest_report.py --cache .dev/research_cache --isins INE002A01018,INE040A01034 ...

Without --isins it uses every stock that has 5-minute candles in the cache (no point-in-time gate: that needs the
fundamental engine; the API job does it). It reads only the cache, so it works with no database and no network, and it
says how much of the requested period the candles really cover.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date
from pathlib import Path

from tbo_costs import default_book

from tbo_technical_engine.backtest.study import Study, run_study
from tbo_technical_engine.backtest.summary import render
from tbo_technical_engine.services.backtests import load_settings
from tbo_technical_engine.services.strategy_loader import load_strategies
from tbo_technical_engine.store.research_cache import ResearchCache

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    """Write the Markdown evidence report from the research cache; returns the exit code."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="start", type=date.fromisoformat, required=True)
    ap.add_argument("--to", dest="end", type=date.fromisoformat, required=True)
    ap.add_argument("--cache", type=Path, default=ROOT / ".dev" / "research_cache")
    ap.add_argument("--isins", help="comma-separated; default: every stock in the cache")
    ap.add_argument("--strategies", help="comma-separated; default: every active strategy")
    ap.add_argument("--out", type=Path, help="write here instead of printing")
    ap.add_argument("--title", default="Backtest report")
    ap.add_argument("--banner", help="a warning printed at the top (for example: synthetic data)")
    args = ap.parse_args(argv)

    file = load_strategies(ROOT / "config" / "strategies.yaml")
    settings = load_settings(ROOT / "config" / "backtest.yaml")
    costs = default_book()
    cache = ResearchCache(args.cache)
    isins = args.isins.split(",") if args.isins else sorted(p.stem for p in (args.cache / "5m").glob("*.parquet"))
    names = args.strategies.split(",") if args.strategies else [n for n, s in file.strategies.items() if s.is_active]
    sessions = {i: cache.session_dates(i, "5m") for i in isins}

    studies: dict[str, Study] = {}
    for name in names:
        began = time.monotonic()
        studies[name] = run_study(
            file, name, [], None, isins, cache.stock, sessions, args.start, args.end, settings, costs, None,
            monotonic=time.monotonic, deadline=float("inf"),
        )  # fmt: skip
        print(f"{name}: {len(studies[name].trades)} trades in {time.monotonic() - began:.0f} s", file=sys.stderr)
    text = render(
        studies, start=args.start, end=args.end, settings=settings, costs=costs, title=args.title, banner=args.banner
    )
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
