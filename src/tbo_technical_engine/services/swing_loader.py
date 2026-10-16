"""Reads `config/swing.yaml`."""

from __future__ import annotations

from pathlib import Path

import yaml

from tbo_technical_engine.schemas.swing import SwingParams


def load_swing(path: Path) -> SwingParams:
    """Parse and check the file. An unknown key anywhere is an error: a typo must stop the job, not change a rule."""
    return SwingParams.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
