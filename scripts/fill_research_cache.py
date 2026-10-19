"""Fill the research cache from the market feed's candle history (the report needs months of candles; the live worker's
warm-up only writes the last few sessions).

    uv run python scripts/fill_research_cache.py --from 2025-10-01 --to 2026-09-30
    uv run python scripts/fill_research_cache.py --from 2026-09-01 --to 2026-09-30 --isins INE002A01018 \
        --timeframes 5m,1d

Without --isins it takes the fundamental engine's universe of --to. Needs the TBO_TECHNICAL_* settings and a token
issuer, like the worker; the cache directory is `TBO_TECHNICAL_RESEARCH_CACHE_DIR`. Safe to repeat.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date

from tbo_platform_kit.http_client import IssuerTokenProvider, ServiceClient

from tbo_technical_engine.clients import fundamental, market_feed
from tbo_technical_engine.config.settings import Settings
from tbo_technical_engine.services.cache_fill import DEFAULT_TIMEFRAMES, fill_cache
from tbo_technical_engine.store.research_cache import ResearchCache


async def run(args: argparse.Namespace, settings: Settings) -> int:
    """Open the clients, fill, print a summary; returns the exit code (1 when any stock failed)."""
    settings.require("market_feed_url")
    assert settings.market_feed_url is not None
    tokens = IssuerTokenProvider.from_settings(settings)
    feed_http = ServiceClient(
        str(settings.market_feed_url), audience=market_feed.AUDIENCE, scopes=market_feed.SCOPES, tokens=tokens
    )
    fund_http: ServiceClient | None = None
    try:
        isins: list[str] = args.isins.split(",") if args.isins else []
        if not isins:
            settings.require("fundamental_url")
            assert settings.fundamental_url is not None
            fund_http = ServiceClient(
                str(settings.fundamental_url), audience=fundamental.AUDIENCE, scopes=fundamental.SCOPES, tokens=tokens
            )
            universe = await fundamental.FundamentalClient(fund_http).universe(args.end)
            if universe is None:
                print(f"no universe published for {args.end}", file=sys.stderr)
                return 2
            isins = sorted(universe.isins)
        result = await fill_cache(
            market_feed.MarketFeedClient(feed_http),
            ResearchCache(settings.research_cache_dir),
            isins,
            args.start,
            args.end,
            args.timeframes.split(","),
        )
    finally:
        await feed_http.aclose()
        if fund_http is not None:
            await fund_http.aclose()
    print(
        f"{len(isins)} stocks, {result.candles} candles, {len(result.failed)} failed -> {settings.research_cache_dir}"
    )
    for isin in result.failed:
        print(f"failed: {isin}", file=sys.stderr)
    return 1 if result.failed else 0


def main(argv: list[str] | None = None) -> int:
    """Parse the arguments and run."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="start", type=date.fromisoformat, required=True)
    ap.add_argument("--to", dest="end", type=date.fromisoformat, required=True)
    ap.add_argument("--isins", help="comma-separated; default: the universe of --to")
    ap.add_argument("--timeframes", default=",".join(DEFAULT_TIMEFRAMES))
    return asyncio.run(run(ap.parse_args(argv), Settings()))


if __name__ == "__main__":
    raise SystemExit(main())
