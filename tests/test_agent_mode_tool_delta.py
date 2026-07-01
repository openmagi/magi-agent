from __future__ import annotations

from pathlib import Path

import pytest

from magi_agent.cli.wiring import _agent_mode_excluded_tool_names
from magi_agent.customize.modes import AgentMode, set_active_mode, upsert_mode
from magi_agent.runtime.per_turn_agent_mode_context import (
    reset_per_turn_agent_mode,
    set_per_turn_agent_mode,
)


@pytest.fixture
def customize_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))


def _mode(mode_id: str, exclude: list[str]) -> AgentMode:
    return AgentMode.model_validate(
        {
            "id": mode_id,
            "displayName": mode_id.title(),
            "toolDelta": {"exclude": exclude, "include": []},
        }
    )


def test_no_mode_no_exclusions(customize_env: None) -> None:
    assert _agent_mode_excluded_tool_names() == frozenset()


def test_active_mode_exclusions(customize_env: None) -> None:
    upsert_mode(_mode("review", ["FileEdit", "Bash"]))
    set_active_mode("review")
    assert _agent_mode_excluded_tool_names() == frozenset({"FileEdit", "Bash"})


def test_per_turn_override_wins(customize_env: None) -> None:
    upsert_mode(_mode("review", ["FileEdit"]))
    upsert_mode(_mode("coding", []))
    set_active_mode("coding")  # stored active = coding (no exclusions)
    token = set_per_turn_agent_mode("review")  # per-turn override -> review
    try:
        assert _agent_mode_excluded_tool_names() == frozenset({"FileEdit"})
    finally:
        reset_per_turn_agent_mode(token)


def test_unknown_mode_empty(customize_env: None) -> None:
    upsert_mode(_mode("review", ["FileEdit"]))
    set_active_mode("review")
    token = set_per_turn_agent_mode("nonexistent")
    try:
        assert _agent_mode_excluded_tool_names() == frozenset()
    finally:
        reset_per_turn_agent_mode(token)


def test_include_is_deferred_and_inert(customize_env: None) -> None:
    # PR-4d is exclude-only: a mode with `include` set must NOT contribute to the
    # excluded set and enables nothing here (include apply is deferred pending the
    # universal hard-safety cap).
    upsert_mode(
        AgentMode.model_validate(
            {
                "id": "grant",
                "displayName": "Grant",
                "toolDelta": {"exclude": [], "include": ["SomeDefaultOffTool"]},
            }
        )
    )
    set_active_mode("grant")
    assert _agent_mode_excluded_tool_names() == frozenset()
