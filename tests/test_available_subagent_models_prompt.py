"""Available-subagent-models system-prompt block + shared route helper."""
from __future__ import annotations

from datetime import datetime

from magi_agent.runtime import message_builder as builder
from magi_agent.runtime.model_tiers import available_child_model_routes


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_available_child_model_routes_unions_registry_and_allowlist() -> None:
    from magi_agent.config.env import _ALLOWED_MODEL_ROUTES_ENV

    routes = available_child_model_routes(
        {_ALLOWED_MODEL_ROUTES_ENV: "anthropic:claude-opus-4-8"}
    )
    joined = "\n".join(routes)

    assert "anthropic:claude-sonnet-4-6 (sota)" in joined  # built-in registry
    assert "openai:gpt-5.5 (sota)" in joined  # built-in registry
    assert "anthropic:claude-opus-4-8" in joined  # operator allowlist (no tier)


def test_available_child_model_routes_empty_env_is_registry_only() -> None:
    routes = available_child_model_routes({})
    assert any("claude-sonnet-4-6" in route for route in routes)


def _enable_serve_subagents(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_CHILD_RUNNER_LIVE_ENABLED", "1")
    monkeypatch.delenv("MAGI_CHILD_RUNNER_LIVE_KILL_SWITCH", raising=False)
    monkeypatch.setenv("MAGI_GATE5B_LIVE_SUBAGENTS_ENABLED", "1")


def test_prompt_lists_subagent_models_when_serve_subagents_enabled(monkeypatch) -> None:
    _enable_serve_subagents(monkeypatch)

    out = builder.build_system_prompt(
        session_key="s",
        turn_id="t",
        identity={},
        user_message={},
        now=_utc("2026-06-16T00:00:00.000Z"),
    )

    assert "<available_subagent_models>" in out
    assert "anthropic:claude-sonnet-4-6" in out
    assert "SpawnAgent" in out


def test_prompt_omits_subagent_models_when_disabled(monkeypatch) -> None:
    monkeypatch.delenv("MAGI_GATE5B_LIVE_SUBAGENTS_ENABLED", raising=False)
    monkeypatch.delenv("MAGI_CHILD_RUNNER_LIVE_ENABLED", raising=False)

    out = builder.build_system_prompt(
        session_key="s",
        turn_id="t",
        identity={},
        user_message={},
        now=_utc("2026-06-16T00:00:00.000Z"),
    )

    assert "<available_subagent_models>" not in out
