"""One price scale for every timeframe (review 9.23). The feed adjusts daily candles for splits and bonuses
(`adjusted=true`) but not intraday ones, so before an ex-date the daily ATR is on the new scale and the 5- and
15-minute candles on the old: an ORB stop and target sized by the daily ATR were too tight by the split ratio, and a
volume filter compared adjusted daily volume with raw intraday volume.

Pure, except `rebase_cache`, which deletes a stock's cache files. The factor of a session is its adjusted daily close
over its raw daily close (`0.5` for every session before a 1:2 split, nothing on and after the ex-date); an intraday
candle of that session is scaled by it, prices rounded back to their own decimals and volume divided by it, which is
what the feed's adjustment does to the daily candle.

    factors = session_factors(raw_daily, adjusted_daily)     # {date(2026, 10, 14): Decimal("0.5"), ...}
    five = adjust(five, factors)
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal

from tbo_platform_kit.config.calendar import IST
from tbo_platform_kit.logging import get_logger

from tbo_technical_engine.config.corporate_actions import FACTOR_NOISE, FACTOR_PLACES
from tbo_technical_engine.schemas.candle import Candle
from tbo_technical_engine.store.research_cache import ResearchCache

log = get_logger(__name__)

_FACTOR_STEP = Decimal(1).scaleb(-FACTOR_PLACES)


def session_of(candle: Candle) -> date:
    """The IST session date a candle belongs to (a daily candle starts at the session's midnight IST)."""
    return candle.start_at.astimezone(IST).date()


def session_factors(raw_daily: Iterable[Candle], adjusted_daily: Iterable[Candle]) -> dict[date, Decimal]:
    """Adjusted over raw close for each session where they differ beyond rounding; a session without both is left
    out (its intraday candles stay as they are)."""
    raw = {session_of(c): c.close for c in raw_daily}
    out: dict[date, Decimal] = {}
    for c in adjusted_daily:
        base = raw.get(session_of(c))
        if base is None or base <= 0:
            continue
        factor = c.close / base
        if abs(factor - 1) > Decimal(str(FACTOR_NOISE)):
            out[session_of(c)] = factor.quantize(_FACTOR_STEP, rounding=ROUND_HALF_EVEN)
    return out


def _scaled(price: Decimal, factor: Decimal) -> Decimal:
    """`price * factor` with the price's own decimals: `1001.35 * 0.5` is `500.68` (half to even)."""
    exponent = price.as_tuple().exponent
    step = Decimal(1).scaleb(exponent if isinstance(exponent, int) else 0)
    return (price * factor).quantize(step, rounding=ROUND_HALF_EVEN)


def adjust(candles: Iterable[Candle], factors: Mapping[date, Decimal]) -> list[Candle]:
    """The candles on the adjusted scale: those of a session with a factor rescaled, the rest returned as they are
    (the very objects, so the common case of no corporate action copies nothing)."""
    if not factors:
        return list(candles)
    out: list[Candle] = []
    for c in candles:
        factor = factors.get(session_of(c))
        if factor is None:
            out.append(c)
            continue
        out.append(
            c.model_copy(
                update={
                    "open": _scaled(c.open, factor),
                    "high": _scaled(c.high, factor),
                    "low": _scaled(c.low, factor),
                    "close": _scaled(c.close, factor),
                    "volume": int((Decimal(c.volume) / factor).to_integral_value(rounding=ROUND_HALF_EVEN)),
                }
            )
        )
    return out


def basis_changed(cached_daily: Mapping[date, float], adjusted_daily: Iterable[Candle]) -> bool:
    """True when a newly read adjusted daily close disagrees with the cached close of the same session beyond rounding:
    a corporate action happened since the cache was written, so its older rows are on another scale."""
    for c in adjusted_daily:
        old = cached_daily.get(session_of(c))
        if old is not None and old > 0 and abs(float(c.close) / old - 1) > FACTOR_NOISE:
            return True
    return False


def rebase_cache(cache: ResearchCache, isin: str, adjusted_daily: Iterable[Candle]) -> bool:
    """Drop a stock's cached candles of every timeframe when its adjustment basis changed (a split since they were
    written), before the new ones are written: a cache never holds two price scales for one stock, and a backtest
    never replays across one. The fill tool (`apps.fill_research_cache`) puts the history back on the new scale.
    True when the stock was dropped."""
    if not basis_changed(cache.daily_closes(isin), adjusted_daily):
        return False
    log.warning("research_cache_rebased", isin=isin, dropped=cache.drop(isin))
    return True
