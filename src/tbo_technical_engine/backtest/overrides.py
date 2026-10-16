"""Request overrides applied to a copy of `config/strategies.yaml`: the requested strategy active, all others off.

Parameters are data, never code (plan, security notes): an override is a name and a string, coerced to the type the file
already has for that parameter. A name the strategy does not have is an error, so a typo cannot silently test the
default.
"""

from __future__ import annotations

from collections.abc import Sequence

from tbo_technical_engine.schemas.backtest import ParamOverride
from tbo_technical_engine.schemas.strategy import ParamValue, StrategyFile, StrategySpec


class OverrideError(ValueError):
    """A parameter override that names an unknown parameter or breaks the strategy's own limits."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field
        self.message = message


def coerce(name: str, current: ParamValue, text: str) -> ParamValue:
    """`text` as the type of `current`: "45" for an integer parameter is 45, "true" for a flag is True."""
    if isinstance(current, bool):
        if text in ("true", "false"):
            return text == "true"
        raise OverrideError(f"params_overrides.{name}", f"{name} is a flag: use true or false")
    if isinstance(current, int):
        try:
            return int(text)
        except ValueError:
            raise OverrideError(f"params_overrides.{name}", f"{name} is a whole number") from None
    return text


def for_backtest(
    file: StrategyFile, strategy: str, overrides: Sequence[ParamOverride], params_version: str | None
) -> StrategyFile:
    """A copy of the strategy file with the overrides applied, so a study never touches the live parameters."""
    if strategy not in file.strategies:
        raise OverrideError("strategy", f"no such strategy: {strategy}")
    if params_version is not None and params_version != file.params_version:
        # The history of the parameter file is git's; only the version in force can be run.
        raise OverrideError("params_version", f"only the current version ({file.params_version}) can be backtested")
    spec = file.strategies[strategy]
    params = dict(spec.params)
    for item in overrides:
        if item.name not in params:
            raise OverrideError(f"params_overrides.{item.name}", f"{strategy} has no parameter {item.name}")
        params[item.name] = coerce(item.name, params[item.name], item.value)
    only = {
        name: (
            StrategySpec(title=s.title, timeframe=s.timeframe, is_active=True, params=params)
            if name == strategy
            else s.model_copy(update={"is_active": False})
        )
        for name, s in file.strategies.items()
    }
    return file.model_copy(update={"strategies": only})
