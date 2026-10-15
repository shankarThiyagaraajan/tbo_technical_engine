"""Reads `config/strategies.yaml`."""

from __future__ import annotations

from pathlib import Path

import yaml

from tbo_technical_engine.schemas.strategy import StrategyFile
from tbo_technical_engine.strategies import REGISTRY


def load_strategies(path: Path) -> StrategyFile:
    """Parse and check the file. A strategy named in it that the code does not have is an error, and so is an unknown
    key anywhere: a typo must stop the worker at start, not change a trade rule silently."""
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    parsed = StrategyFile.model_validate(document)
    unknown = sorted(set(parsed.strategies) - set(REGISTRY))
    if unknown:
        raise ValueError(f"{path}: no such strategy in the code: {', '.join(unknown)}")
    return parsed
