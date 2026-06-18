"""Tests for MAGI_MAIN_AGENT_PROFILE flag and orchestrator toolset filter.

TDD: written RED first, then GREEN via magi_agent/config/env.py +
magi_agent/runtime/main_agent_profile.py.
"""
from __future__ import annotations

import pytest

from magi_agent.config.env import main_agent_profile
from magi_agent.runtime.child_toolset import READONLY_TOOL_NAMES
from magi_agent.runtime.main_agent_profile import (
    ORCHESTRATOR_PROFILE,
    apply_orchestrator_filter,
    orchestrator_tool_names,
)


class TestMainAgentProfileFlag:
    def test_empty_env_returns_empty_string(self) -> None:
        assert main_agent_profile({}) == ""

    def test_orchestrator_value_returns_orchestrator(self) -> None:
        assert main_agent_profile({"MAGI_MAIN_AGENT_PROFILE": "orchestrator"}) == "orchestrator"

    def test_orchestrator_case_insensitive(self) -> None:
        assert main_agent_profile({"MAGI_MAIN_AGENT_PROFILE": "Orchestrator"}) == "orchestrator"

    def test_orchestrator_strips_whitespace(self) -> None:
        assert main_agent_profile({"MAGI_MAIN_AGENT_PROFILE": "  orchestrator  "}) == "orchestrator"

    def test_unknown_value_returns_empty_string(self) -> None:
        assert main_agent_profile({"MAGI_MAIN_AGENT_PROFILE": "supervisor"}) == ""

    def test_empty_string_value_returns_empty_string(self) -> None:
        assert main_agent_profile({"MAGI_MAIN_AGENT_PROFILE": ""}) == ""

    def test_unset_returns_empty_string(self) -> None:
        assert main_agent_profile({"SOME_OTHER_ENV": "foo"}) == ""


class TestOrchestratorToolNames:
    def test_constant_value(self) -> None:
        assert ORCHESTRATOR_PROFILE == "orchestrator"

    def test_readonly_names_are_subset(self) -> None:
        tool_names = orchestrator_tool_names()
        for name in READONLY_TOOL_NAMES:
            assert name in tool_names, f"{name!r} missing from orchestrator_tool_names()"

    def test_spawn_agent_included(self) -> None:
        assert "SpawnAgent" in orchestrator_tool_names()

    def test_bash_excluded(self) -> None:
        assert "Bash" not in orchestrator_tool_names()

    def test_web_search_excluded(self) -> None:
        assert "WebSearch" not in orchestrator_tool_names()

    def test_returns_tuple(self) -> None:
        result = orchestrator_tool_names()
        assert isinstance(result, tuple)


class TestApplyOrchestratorFilter:
    def test_empty_input_returns_empty_pair(self) -> None:
        restricted, cap = apply_orchestrator_filter(())
        assert restricted == ()
        assert cap == ()

    def test_spawn_cap_equals_full_input(self) -> None:
        full = ("FileRead", "Bash", "WebSearch", "SpawnAgent", "Glob")
        _, cap = apply_orchestrator_filter(full)
        assert cap == full

    def test_restricted_excludes_bash(self) -> None:
        full = ("FileRead", "Bash", "WebSearch", "SpawnAgent", "Glob")
        restricted, _ = apply_orchestrator_filter(full)
        assert "Bash" not in restricted

    def test_restricted_excludes_web_search(self) -> None:
        full = ("FileRead", "Bash", "WebSearch", "SpawnAgent", "Glob")
        restricted, _ = apply_orchestrator_filter(full)
        assert "WebSearch" not in restricted

    def test_restricted_includes_file_read(self) -> None:
        full = ("FileRead", "Bash", "WebSearch", "SpawnAgent", "Glob")
        restricted, _ = apply_orchestrator_filter(full)
        assert "FileRead" in restricted

    def test_restricted_includes_glob(self) -> None:
        full = ("FileRead", "Bash", "WebSearch", "SpawnAgent", "Glob")
        restricted, _ = apply_orchestrator_filter(full)
        assert "Glob" in restricted

    def test_restricted_includes_spawn_agent(self) -> None:
        full = ("FileRead", "Bash", "WebSearch", "SpawnAgent", "Glob")
        restricted, _ = apply_orchestrator_filter(full)
        assert "SpawnAgent" in restricted

    def test_restricted_preserves_order(self) -> None:
        full = ("FileRead", "Bash", "SpawnAgent", "Glob")
        restricted, _ = apply_orchestrator_filter(full)
        # FileRead must appear before SpawnAgent which must appear before Glob
        assert list(restricted) == ["FileRead", "SpawnAgent", "Glob"]

    def test_returns_tuple_pair(self) -> None:
        restricted, cap = apply_orchestrator_filter(("FileRead",))
        assert isinstance(restricted, tuple)
        assert isinstance(cap, tuple)

    def test_all_orchestrator_tools_absent_from_input(self) -> None:
        full = ("Bash", "WebSearch", "FileWrite")
        restricted, cap = apply_orchestrator_filter(full)
        assert restricted == ()
        assert cap == full
