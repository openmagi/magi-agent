from __future__ import annotations

from pathlib import Path

from magi_agent.observability.config import ObservabilityConfig


def test_from_env_defaults_disabled(monkeypatch):
    monkeypatch.delenv("MAGI_OBSERVABILITY_ENABLED", raising=False)
    cfg = ObservabilityConfig.from_env(home=Path("/tmp/x"))
    assert cfg.enabled is False
    assert cfg.db_path == Path("/tmp/x/observability.db")
    assert cfg.retention_days == 7
    assert cfg.max_events == 200_000


def test_from_env_enabled_truthy(monkeypatch):
    monkeypatch.setenv("MAGI_OBSERVABILITY_ENABLED", "true")
    monkeypatch.setenv("MAGI_OBS_RETENTION_DAYS", "3")
    monkeypatch.setenv("MAGI_OBS_MAX_EVENTS", "500")
    cfg = ObservabilityConfig.from_env(home=Path("/tmp/x"))
    assert cfg.enabled is True
    assert cfg.retention_days == 3
    assert cfg.max_events == 500


def test_int_env_falls_back_on_non_numeric(monkeypatch):
    from magi_agent.observability.config import _int_env

    monkeypatch.setenv("SOME_INT", "not-a-number")
    assert _int_env("SOME_INT", 42) == 42
    monkeypatch.setenv("SOME_INT", "")
    assert _int_env("SOME_INT", 7) == 7
    monkeypatch.delenv("SOME_INT", raising=False)
    assert _int_env("SOME_INT", 99) == 99
