"""Run the active strategies over the research cache and write the report (the TE5 exit-criterion report).

    uv run python scripts/backtest_report.py --from 2025-10-01 --to 2026-09-30 --out report.md
    uv run python scripts/backtest_report.py --cache .dev/research_cache --isins INE002A01018,INE040A01034 ...

Without --isins it uses every stock that has 5-minute candles in the cache. The point-in-time gate (who was tradable
each day, and on which side) is applied whenever TBO_TECHNICAL_FUNDAMENTAL_URL is set, like the API job does; --no-gate
turns it off and the report then says so at the top: such a report counts trades the live system could never take
(shorts on long-only stocks, results days, ex-dates) and must not be used for the go-live decision. Without the gate
it reads only the cache, so it works with no database and no network. It says how much of the requested period the
candles really cover. Fill the cache first with scripts/fill_research_cache.py.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import date
from pathlib import Path

from tbo_platform_kit.http_client import IssuerTokenProvider, ServiceClient

from tbo_technical_engine.backtest.costs import load_costs
from tbo_technical_engine.backtest.engine import DayRules
from tbo_technical_engine.backtest.index_side import PRIMARY_INDEX, index_sides
from tbo_technical_engine.backtest.study import Study, run_study
from tbo_technical_engine.backtest.summary import render
from tbo_technical_engine.clients import fundamental
from tbo_technical_engine.config.settings import Settings
from tbo_technical_engine.services.backtests import load_settings
from tbo_technical_engine.services.point_in_time import load_rules
from tbo_technical_engine.services.strategy_loader import load_strategies
from tbo_technical_engine.store.research_cache import ResearchCache

ROOT = Path(__file__).resolve().parents[1]
UNGATED_BANNER = (
    "NOT GATED: no point-in-time gate was applied, so this counts trades the live system could never take "
    "(shorts on long-only stocks, results days, ex-dates). Do not use it for the go-live decision."
)


async def gate_rules(settings: Settings, start: date, end: date) -> DayRules:
    """The fundamental engine's past gates for `start`..`end`, as the API backtest job reads them."""
    assert settings.fundamental_url is not None
    http = ServiceClient(
        str(settings.fundamental_url),
        audience=fundamental.AUDIENCE,
        scopes=fundamental.SCOPES,
        tokens=IssuerTokenProvider.from_settings(settings),
    )
    try:
        return await load_rules(fundamental.FundamentalClient(http), start, end)
    finally:
        await http.aclose()


def main(argv: list[str] | None = None) -> int:
    """Write the Markdown evidence report from the research cache; returns the exit code."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="start", type=date.fromisoformat, required=True)
    ap.add_argument("--to", dest="end", type=date.fromisoformat, required=True)
    ap.add_argument("--cache", type=Path, help="default: TBO_TECHNICAL_RESEARCH_CACHE_DIR")
    ap.add_argument("--isins", help="comma-separated; default: every stock in the cache")
    ap.add_argument("--strategies", help="comma-separated; default: every active strategy")
    ap.add_argument("--out", type=Path, help="write here instead of printing")
    ap.add_argument(
        "--gate",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="apply the point-in-time gate (default: yes when TBO_TECHNICAL_FUNDAMENTAL_URL is set)",
    )
    ap.add_argument("--title", default="Backtest report")
    ap.add_argument("--banner", help="a warning printed at the top (for example: synthetic data)")
    args = ap.parse_args(argv)

    file = load_strategies(ROOT / "config" / "strategies.yaml")
    settings = load_settings(ROOT / "config" / "backtest.yaml")
    costs = load_costs(ROOT / "config" / "costs.yaml")
    settings_env = Settings()
    gated = settings_env.fundamental_url is not None if args.gate is None else args.gate
    if gated and settings_env.fundamental_url is None:
        print("--gate needs TBO_TECHNICAL_FUNDAMENTAL_URL", file=sys.stderr)
        return 2
    rules = asyncio.run(gate_rules(settings_env, args.start, args.end)) if gated else None
    banner = args.banner if gated else "\n\n".join(b for b in (args.banner, UNGATED_BANNER) if b)
    cache_dir: Path = args.cache or settings_env.research_cache_dir
    cache = ResearchCache(cache_dir)
    isins = args.isins.split(",") if args.isins else sorted(p.stem for p in (cache_dir / "5m").glob("*.parquet"))
    names = args.strategies.split(",") if args.strategies else [n for n, s in file.strategies.items() if s.is_active]
    sessions = {i: cache.session_dates(i, "5m") for i in isins}

    index_side = index_sides(cache.indices.candles(PRIMARY_INDEX, "5m"))
    studies: dict[str, Study] = {}
    for name in names:
        began = time.monotonic()
        studies[name] = run_study(
            file, name, [], None, isins, cache.stock, sessions, args.start, args.end, settings, costs, rules,
            monotonic=time.monotonic, deadline=float("inf"), index_side=index_side,
        )  # fmt: skip
        print(f"{name}: {len(studies[name].trades)} trades in {time.monotonic() - began:.0f} s", file=sys.stderr)
    text = render(
        studies, start=args.start, end=args.end, settings=settings, costs=costs, title=args.title, banner=banner
    )
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
