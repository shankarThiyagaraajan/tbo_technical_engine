"""The cooldown checkpoint in Redis, so a restart does not let a strategy fire again inside its cooldown.

One hash per stock: field `<strategy>:<side>`, value the close time of the entry candle that fired. It expires after a
day and a half (the longest cooldown is a session). The evaluator's in-memory map is the working copy; this is loaded
once per stock after a start and written when a candidate is emitted.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from tbo_platform_kit.mode import ModeProfile

from tbo_technical_engine.schemas.signal import SignalCandidate
from tbo_technical_engine.services.evaluator import FiredKey
from tbo_technical_engine.settings import SHORT_NAME
from tbo_technical_engine.utils.timefmt import parse_z, z
from tbo_technical_engine.utils.timeframes import length

TTL = timedelta(hours=36)


class CooldownStore:
    def __init__(self, redis: Any, profile: ModeProfile) -> None:
        self.redis = redis
        self.prefix = f"{profile.stream_prefix}{SHORT_NAME}.strategy_state:"
        self._loaded: set[str] = set()

    def _key(self, isin: str) -> str:
        return f"{self.prefix}{isin}"

    async def load(self, isin: str, into: dict[FiredKey, datetime]) -> None:
        """Merge what Redis holds for `isin` into `into`, once per stock per process. Redis being down is not fatal:
        the process then starts with no cooldowns, which can only repeat a signal, never lose one."""
        if isin in self._loaded:
            return
        try:
            stored = await self.redis.hgetall(self._key(isin))
        except Exception:
            return
        self._loaded.add(isin)
        for field, value in stored.items():
            strategy, _, side = field.decode().rpartition(":")
            at = parse_z(value.decode())
            key = (isin, strategy, side)
            if key not in into or into[key] < at:
                into[key] = at

    async def mark(self, candidate: SignalCandidate) -> None:
        try:
            key = self._key(candidate.isin)
            await self.redis.hset(
                key, f"{candidate.strategy}:{candidate.side}", z(candidate.candle_start_at + length("5m"))
            )
            await self.redis.expire(key, int(TTL.total_seconds()))
        except Exception:
            return  # the in-memory cooldown still holds for this process
