"""`config/strategies.yaml` as typed records, and the parameter accessor strategies read from."""

from __future__ import annotations

from datetime import datetime, time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ParamValue = str | int | bool


class StrategySpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str = Field(min_length=1, max_length=100)
    timeframe: Literal["5m"]
    is_active: bool = True
    params: dict[str, ParamValue]


class StrategyFile(BaseModel):
    """The whole file. An unknown key is an error: a misspelled parameter must not silently fall back to a default."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    params_version: str = Field(min_length=1, max_length=40)
    active_from_at: datetime
    strategies: dict[str, StrategySpec]

    @field_validator("strategies")
    @classmethod
    def _names(cls, value: dict[str, StrategySpec]) -> dict[str, StrategySpec]:
        import re

        for name in value:
            if not re.fullmatch(r"[a-z][a-z0-9]*(_[a-z0-9]+)*", name) or len(name) > 60:
                raise ValueError(f"not a strategy name: {name!r}")
        return value


class Params:
    """Typed reads of one strategy's parameters. A missing or mistyped value raises KeyError/ValueError at the first
    evaluation, which the runner logs; it never defaults."""

    def __init__(self, values: dict[str, ParamValue]) -> None:
        self._values = values

    def number(self, name: str) -> float:
        value = self._values[name]
        if isinstance(value, bool):
            raise ValueError(f"{name} is not a number")
        return float(value)

    def integer(self, name: str) -> int:
        value = self._values[name]
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{name} is not an integer")
        return value

    def flag(self, name: str) -> bool:
        value = self._values[name]
        if not isinstance(value, bool):
            raise ValueError(f"{name} is not a boolean")
        return value

    def clock(self, name: str) -> time:
        value = self._values[name]
        if not isinstance(value, str):
            raise ValueError(f"{name} is not a time")
        return time.fromisoformat(value)
