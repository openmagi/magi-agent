# tests/benchmarks/taubench/test_cli.py
from __future__ import annotations

import os

import pytest

from magi_agent.benchmarks.taubench.cli import (
    DEFAULT_AGENT_MODEL,
    GateDisabledError,
    _apply_flags,
    ensure_enabled,
)
from magi_agent.benchmarks.taubench.config import FULL_CAPABILITY_FLAGS
from magi_agent.cli.providers import _DEFAULT_MODEL


def test_gate_blocks_when_unset(monkeypatch) -> None:
    monkeypatch.delenv("MAGI_TAUBENCH_ENABLED", raising=False)
    with pytest.raises(GateDisabledError):
        ensure_enabled()


def test_gate_allows_when_set(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_TAUBENCH_ENABLED", "1")
    ensure_enabled()


def test_vanilla_apply_flags_forces_capability_flags_off_then_restores(
    monkeypatch,
) -> None:
    for key in FULL_CAPABILITY_FLAGS:
        monkeypatch.setenv(key, "1")

    with _apply_flags("vanilla"):
        assert {key: os.environ.get(key) for key in FULL_CAPABILITY_FLAGS} == {
            key: "0" for key in FULL_CAPABILITY_FLAGS
        }

    assert {key: os.environ.get(key) for key in FULL_CAPABILITY_FLAGS} == {
        key: "1" for key in FULL_CAPABILITY_FLAGS
    }


def test_vanilla_apply_flags_restores_unset_capability_flags(monkeypatch) -> None:
    for key in FULL_CAPABILITY_FLAGS:
        monkeypatch.delenv(key, raising=False)

    with _apply_flags("vanilla"):
        assert {key: os.environ.get(key) for key in FULL_CAPABILITY_FLAGS} == {
            key: "0" for key in FULL_CAPABILITY_FLAGS
        }

    assert {key: os.environ.get(key) for key in FULL_CAPABILITY_FLAGS} == {
        key: None for key in FULL_CAPABILITY_FLAGS
    }


def test_default_agent_model_tracks_anthropic_provider_default() -> None:
    assert DEFAULT_AGENT_MODEL == _DEFAULT_MODEL["anthropic"]


def test_main_gate_blocks_before_live_imports(monkeypatch, capsys) -> None:
    from magi_agent.benchmarks.taubench import cli

    monkeypatch.delenv("MAGI_TAUBENCH_ENABLED", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--domain", "airline", "--max-tasks", "1"])

    assert exc_info.value.code == 1
    assert "MAGI_TAUBENCH_ENABLED" in capsys.readouterr().err


def test_main_parses_args_and_forwards_profile(monkeypatch) -> None:
    from magi_agent.benchmarks.taubench import cli

    captured: dict[str, object] = {}

    def fake_run_eval(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setenv("MAGI_TAUBENCH_ENABLED", "1")
    monkeypatch.setattr(cli, "run_eval", fake_run_eval)

    code = cli.main(
        [
            "--domain",
            "retail",
            "--max-tasks",
            "2",
            "--trials",
            "3",
            "--config",
            "vanilla",
            "--profile",
            "research",
        ]
    )

    assert code == 0
    assert captured == {
        "domain": "retail",
        "max_tasks": 2,
        "trials": 3,
        "config": "vanilla",
        "profile": "research",
    }
