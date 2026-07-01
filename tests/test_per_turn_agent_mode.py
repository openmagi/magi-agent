from __future__ import annotations

from pathlib import Path

import pytest

from magi_agent.customize.modes import AgentMode, set_active_mode, upsert_mode
from magi_agent.runtime.message_builder import _agent_mode_block
from magi_agent.runtime.per_turn_agent_mode_context import (
    current_per_turn_agent_mode,
    reset_per_turn_agent_mode,
    set_per_turn_agent_mode,
)


@pytest.fixture
def customize_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))


def _mode(mode_id: str, name: str, prompt: str) -> AgentMode:
    return AgentMode.model_validate(
        {"id": mode_id, "displayName": name, "systemPrompt": prompt}
    )


def test_contextvar_set_get_reset() -> None:
    assert current_per_turn_agent_mode() is None
    token = set_per_turn_agent_mode("coding")
    try:
        assert current_per_turn_agent_mode() == "coding"
    finally:
        reset_per_turn_agent_mode(token)
    assert current_per_turn_agent_mode() is None


def test_set_falsy_clears() -> None:
    token = set_per_turn_agent_mode("")
    try:
        assert current_per_turn_agent_mode() is None
    finally:
        reset_per_turn_agent_mode(token)


def test_per_turn_wins_over_stored_active(customize_env: None) -> None:
    upsert_mode(_mode("coding", "Coding", "CODING PROMPT"))
    upsert_mode(_mode("research", "Research", "RESEARCH PROMPT"))
    set_active_mode("coding")  # stored sticky default = coding
    token = set_per_turn_agent_mode("research")  # request overrides -> research
    try:
        block = _agent_mode_block()
        assert "RESEARCH PROMPT" in block
        assert "CODING PROMPT" not in block
    finally:
        reset_per_turn_agent_mode(token)


def test_falls_back_to_stored_active_when_unset(customize_env: None) -> None:
    upsert_mode(_mode("coding", "Coding", "CODING PROMPT"))
    set_active_mode("coding")
    # no per-turn override -> PR-4b behavior (stored active)
    assert current_per_turn_agent_mode() is None
    assert "CODING PROMPT" in _agent_mode_block()


def test_per_turn_unknown_mode_is_empty(customize_env: None) -> None:
    upsert_mode(_mode("coding", "Coding", "CODING PROMPT"))
    set_active_mode("coding")
    # explicit request selection wins; if it does not exist -> fail-soft empty,
    # do NOT silently substitute the stored active mode.
    token = set_per_turn_agent_mode("nonexistent")
    try:
        assert _agent_mode_block() == ""
    finally:
        reset_per_turn_agent_mode(token)
