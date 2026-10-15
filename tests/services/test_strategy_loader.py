from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from tbo_technical_engine.schemas.strategy import Params
from tbo_technical_engine.services.strategy_loader import load_strategies
from tbo_technical_engine.strategies import REGISTRY

SHIPPED = Path(__file__).resolve().parents[2] / "config" / "strategies.yaml"


def test_the_shipped_file_names_exactly_the_strategies_in_the_code() -> None:
    config = load_strategies(SHIPPED)
    assert set(config.strategies) == set(REGISTRY)
    assert config.params_version == "2026.10.1"


def test_the_shipped_file_has_every_parameter_its_strategy_reads() -> None:
    """A parameter a strategy reads but the file lacks would make it fail on its first candle; catch it here."""
    import re

    config = load_strategies(SHIPPED)
    sources = {
        "ema_9_21_crossover": "ema_cross",
        "vwap_breakout": "vwap_breakout",
        "orb_15m": "orb",
        "pivot_supertrend": "pivot_supertrend",
        "vwap_pivot": "vwap_pivot",
    }
    src = Path(__file__).resolve().parents[2] / "src" / "tbo_technical_engine" / "strategies"
    common = (src / "pricing.py").read_text()
    for name, module in sources.items():
        text = (src / f"{module}.py").read_text() + common.split("def finish")[0] * 0
        read = set(re.findall(r'p\.(?:number|integer|flag|clock)\("([a-z0-9_]+)"\)', text))
        read |= {"entry_zone_atr", "entry_from", "entry_until", "cooldown_minutes"}  # finish() and the evaluator
        if name in {"vwap_breakout", "vwap_pivot"}:
            read.add("target_2_min_gap_r")
            read.add("target_2_r")
        missing = read - set(config.strategies[name].params)
        assert not missing, f"{name}: {missing}"


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "s.yaml"
    path.write_text(text)
    return path


def test_an_unknown_key_is_an_error(tmp_path: Path) -> None:
    text = SHIPPED.read_text().replace("is_active: true", "is_actve: true", 1)
    with pytest.raises(ValidationError):
        load_strategies(write(tmp_path, text))


def test_a_strategy_the_code_does_not_have_is_an_error(tmp_path: Path) -> None:
    text = SHIPPED.read_text() + "  moon_phase:\n    title: x\n    timeframe: 5m\n    params: {}\n"
    with pytest.raises(ValueError, match="moon_phase"):
        load_strategies(write(tmp_path, text))


def test_params_are_typed_and_never_defaulted() -> None:
    p = Params({"a": "1.5", "b": 3, "c": True, "t": "09:30"})
    assert p.number("a") == 1.5 and p.integer("b") == 3 and p.flag("c") and p.clock("t").minute == 30
    with pytest.raises(KeyError):
        p.number("missing")
    with pytest.raises(ValueError):
        p.integer("a")
    with pytest.raises(ValueError):
        p.number("c")
    with pytest.raises(ValueError):
        p.flag("b")
