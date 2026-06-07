from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from magi_agent.observability.runtime_sink import get_active_sink, set_active_sink


def _runtime():
    return SimpleNamespace(config=SimpleNamespace(gateway_token="local-dev-token", bot_id="b"))


def test_register_observability_disabled_when_env_unset(monkeypatch, tmp_path):
    monkeypatch.delenv("MAGI_OBSERVABILITY_ENABLED", raising=False)
    from magi_agent.observability.integration import register_observability

    app = FastAPI()
    result = register_observability(app, _runtime())
    assert result is None
    assert get_active_sink() is None


def test_register_observability_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("MAGI_OBSERVABILITY_ENABLED", "true")
    monkeypatch.setenv("MAGI_OBS_HOME", str(tmp_path))
    from magi_agent.observability.integration import register_observability

    app = FastAPI()
    core = None
    try:
        core = register_observability(app, _runtime())
        assert core is not None
        assert get_active_sink() is not None
        route_paths = [r.path for r in app.routes]
        assert any(p == "/api/observability/v1/meta" for p in route_paths)
    finally:
        set_active_sink(None)
        if core is not None:
            core.close()


def test_register_observability_is_idempotent(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from fastapi import FastAPI

    from magi_agent.observability import register_observability
    from magi_agent.observability import runtime_sink

    monkeypatch.setenv("MAGI_OBSERVABILITY_ENABLED", "true")
    monkeypatch.setenv("MAGI_OBS_HOME", str(tmp_path))
    app = FastAPI()
    runtime = SimpleNamespace(config=SimpleNamespace(gateway_token="t", bot_id="b"))
    try:
        core1 = register_observability(app, runtime)
        core2 = register_observability(app, runtime)
        assert core1 is core2  # second call returns the same core, no double-mount
    finally:
        runtime_sink.set_active_sink(None)
        if core1 is not None:
            core1.close()
