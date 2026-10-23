"""The API process as the image starts it: host and port from the settings, loopback unless set."""

from __future__ import annotations

from pathlib import Path

import pytest

from tbo_technical_engine import main as main_module
from tbo_technical_engine.config.settings import Settings

ROOT = Path(__file__).resolve().parent.parent


def test_main_serves_on_the_configured_host_and_port(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, object] = {}
    configured = settings.model_copy(update={"bind_host": "10.1.2.3", "bind_port": 8123})
    monkeypatch.setattr("tbo_technical_engine.main.Settings", lambda: configured)
    # logging stays as the suite set it: reconfiguring it here would hide later tests' log lines from capsys
    monkeypatch.setattr("tbo_technical_engine.main.configure_logging", lambda **_kwargs: None)
    monkeypatch.setattr("tbo_technical_engine.main.build_app", lambda _settings: "app")
    monkeypatch.setattr("tbo_technical_engine.main.uvicorn.run", lambda app, **kwargs: calls.update(app=app, **kwargs))
    main_module.main()
    assert calls["app"] == "app"
    assert calls["host"] == "10.1.2.3"
    assert calls["port"] == 8123


def test_the_bind_host_is_loopback_unless_set() -> None:
    assert Settings.model_fields["bind_host"].default == "127.0.0.1"


def test_the_image_listens_on_all_interfaces_inside_its_container() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "ENV TBO_TECHNICAL_BIND_HOST=0.0.0.0" in dockerfile
