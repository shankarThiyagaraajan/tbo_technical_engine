"""The Celery entry for the daily swing run: builds the clients from the settings and runs the service once."""

from __future__ import annotations

from datetime import datetime

from tbo_platform_kit.calendar import now_utc
from tbo_platform_kit.config.calendar import IST
from tbo_platform_kit.db import create_engine, session_factory
from tbo_platform_kit.events import EventPublisher
from tbo_platform_kit.http_client import IssuerTokenProvider, ServiceClient
from tbo_signals import Signals

from tbo_technical_engine.clients import fundamental, market_feed
from tbo_technical_engine.config.settings import REPOSITORY, Settings
from tbo_technical_engine.services.market_calendar import build_calendar
from tbo_technical_engine.services.platform_signals import process_signals
from tbo_technical_engine.services.snapshot_service import Kind, SnapshotService, Summary
from tbo_technical_engine.services.swing_loader import load_swing
from tbo_technical_engine.utils.redis import connect_redis


async def run_job(
    settings: Settings, kind: Kind, now: datetime | None = None, signals: Signals | None = None
) -> Summary:
    """Open the stores and clients, then run one snapshot pass."""
    settings.require("database_url", "redis_url", "market_feed_url", "fundamental_url")
    assert settings.redis_url is not None
    assert settings.market_feed_url is not None
    assert settings.fundamental_url is not None
    params = load_swing(settings.swing_file)
    signals = signals or process_signals(settings)
    redis = connect_redis(settings.redis_url.get_secret_value())
    engine = create_engine(settings)
    tokens = IssuerTokenProvider.from_settings(settings)
    feed_http = ServiceClient(
        str(settings.market_feed_url), audience=market_feed.AUDIENCE, scopes=market_feed.SCOPES, tokens=tokens
    )
    fund_http = ServiceClient(
        str(settings.fundamental_url), audience=fundamental.AUDIENCE, scopes=fundamental.SCOPES, tokens=tokens
    )
    calendar = build_calendar(settings, tokens, signals)
    try:
        await calendar.refresh()
        service = SnapshotService(
            market_feed.MarketFeedClient(feed_http),
            fundamental.FundamentalClient(fund_http),
            session_factory(engine),
            EventPublisher(redis, settings.profile, source=REPOSITORY),
            calendar,
            params,
            now_utc,
        )
        service.signals = signals
        return await service.run(kind, (now or now_utc()).astimezone(IST).date())
    finally:
        if calendar.client is not None:
            calendar.client.close()
        await feed_http.aclose()
        await fund_http.aclose()
        await redis.aclose()
        await engine.dispose()
