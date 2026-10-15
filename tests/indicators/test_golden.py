from __future__ import annotations

import json

import pytest

from tests.indicators.golden import AT, PATH, compute


def test_indicator_values_have_not_drifted() -> None:
    expected = json.loads(PATH.read_text())
    assert expected["at"] == list(AT)
    got = compute()
    assert got.keys() == expected["values"].keys()
    for name, values in expected["values"].items():
        for want, have in zip(values, got[name], strict=True):
            assert (want is None and have is None) or have == pytest.approx(want, abs=1e-6), name
