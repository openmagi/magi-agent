"""Tests for Task 2B.3: tighten-only child toolset = profile ∩ parent cap.

When MAGI_SUBAGENT_TOOL_TIGHTEN_ONLY_ENABLED=1 AND parentToolNames is non-empty,
_resolve_turn_toolset (via the caller) returns only tools whose name is in parent_cap.
When the flag is OFF or parentToolNames is absent/empty, the full profile is returned
unchanged (byte-identical to pre-2B.3).

Hermetic: no real model / no provider key. Object-form monkeypatching for
magi_agent.runtime submodule attrs (avoids PEP 562 __getattr__ restriction).
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

import pytest

import magi_agent.runtime.child_runner_live as child_runner_live_mod
import magi_agent.cli.tool_runtime as tool_runtime_mod
from magi_agent.runtime.child_runner_boundary import ChildTaskRequest
from magi_agent.runtime.child_runner_live import RealLocalChildRunner

# ---------------------------------------------------------------------------
# Env isolation
# ---------------------------------------------------------------------------

_PROVIDER_ENV = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "FIREWORKS_API_KEY",
    "MAGI_PROVIDER",
    "MAGI_MODEL",
    "MAGI_SUBAGENT_TOOL_TIGHTEN_ONLY_ENABLED",
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path) -> None:
    """Hermetic: no real key / config / tighten flag."""
    for name in _PROVIDER_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "absent.toml"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _request(**overrides: object) -> ChildTaskRequest:
    data: dict[str, object] = {
        "parentExecutionId": "parent-exec-tighten",
        "turnId": "turn-tighten-1",
        "taskId": "task-tighten-1",
        "objective": "Complete delegated subtask.",
        "role": "research",
        "delivery": "return",
    }
    data.update(overrides)
    return ChildTaskRequest(**data)


def _provider_config(api_key: str = "sk-test") -> object:
    from magi_agent.cli.providers import ProviderConfig

    return ProviderConfig(provider="anthropic", model="claude-sonnet-4-6", api_key=api_key)


class _NamedTool:
    def __init__(self, name: str) -> None:
        self.name = name


# A "full" profile toolset: mix of readonly + mutating tools
_FULL_TOOLS = [
    _NamedTool("FileRead"),
    _NamedTool("Glob"),
    _NamedTool("Grep"),
    _NamedTool("GitDiff"),
    _NamedTool("FileWrite"),
    _NamedTool("Bash"),
    _NamedTool("Edit"),
]

# Readonly subset: what the parent would pass as parentToolNames when it only has
# read-only tools itself.
_READONLY_NAMES = frozenset({"FileRead", "Glob", "Grep", "GitDiff"})


class _FakePart:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeContent:
    def __init__(self, text: str) -> None:
        self.parts = [_FakePart(text)]


class _FakeEvent:
    def __init__(self, text: str) -> None:
        self.content = _FakeContent(text)


class _FakeRunner:
    async def run_async(self, **kwargs: object) -> AsyncGenerator[object, None]:
        yield _FakeEvent("ANSWER: tighten ran")


def _patch_full_tools(monkeypatch) -> None:
    """Patch build_cli_adk_tools to return the full mixed toolset."""
    def _fake_build_tools(**kwargs: object) -> list[_NamedTool]:
        return list(_FULL_TOOLS)

    monkeypatch.setattr(tool_runtime_mod, "build_cli_adk_tools", _fake_build_tools)


def _patch_build_runner(monkeypatch) -> None:
    """Patch build_cli_model_runner to return a fake runner (no network)."""
    import magi_agent.cli.real_runner as real_runner_mod

    monkeypatch.setattr(
        real_runner_mod,
        "build_cli_model_runner",
        lambda config, **kw: _FakeRunner(),
    )


# ---------------------------------------------------------------------------
# Test 1: Flag ON + non-empty parentToolNames → intersection only
# ---------------------------------------------------------------------------


def test_tighten_only_flag_on_returns_intersection(monkeypatch) -> None:
    """Flag ON + parentToolNames = readonly subset → only intersection returned
    (no mutating tools like Bash/FileWrite/Edit)."""
    _patch_full_tools(monkeypatch)
    _patch_build_runner(monkeypatch)
    monkeypatch.setenv("MAGI_SUBAGENT_TOOL_TIGHTEN_ONLY_ENABLED", "1")

    req = _request(metadata={"parentToolNames": tuple(_READONLY_NAMES)})
    runner = RealLocalChildRunner(
        provider_config=_provider_config(),
        toolset_profile="full",
        runner=_FakeRunner(),  # injected → we call _resolve_turn_toolset directly
    )

    tools, collector = runner._resolve_turn_toolset("session-tighten-1", request=req)
    tool_names = {t.name for t in tools}

    # Only readonly tools survive the intersection
    assert tool_names == _READONLY_NAMES
    # Mutating tools are excluded
    assert "FileWrite" not in tool_names
    assert "Bash" not in tool_names
    assert "Edit" not in tool_names


# ---------------------------------------------------------------------------
# Test 2: Flag OFF → full profile returned unchanged (even with parentToolNames)
# ---------------------------------------------------------------------------


def test_tighten_only_flag_off_returns_full_profile(monkeypatch) -> None:
    """Flag OFF → full profile returned unchanged even when parentToolNames is set."""
    _patch_full_tools(monkeypatch)
    monkeypatch.delenv("MAGI_SUBAGENT_TOOL_TIGHTEN_ONLY_ENABLED", raising=False)

    req = _request(metadata={"parentToolNames": tuple(_READONLY_NAMES)})
    runner = RealLocalChildRunner(
        provider_config=_provider_config(),
        toolset_profile="full",
        runner=_FakeRunner(),
    )

    tools, _collector = runner._resolve_turn_toolset("session-tighten-off", request=req)
    tool_names = {t.name for t in tools}

    # Full profile: all 7 tools returned unchanged
    assert tool_names == {t.name for t in _FULL_TOOLS}


# ---------------------------------------------------------------------------
# Test 3: Flag ON + empty parentToolNames → full profile unchanged (fail-open)
# ---------------------------------------------------------------------------


def test_tighten_only_flag_on_empty_parent_cap_returns_full_profile(monkeypatch) -> None:
    """Flag ON + empty parentToolNames → no narrowing (fail-open no-op)."""
    _patch_full_tools(monkeypatch)
    monkeypatch.setenv("MAGI_SUBAGENT_TOOL_TIGHTEN_ONLY_ENABLED", "1")

    req = _request(metadata={"parentToolNames": ()})
    runner = RealLocalChildRunner(
        provider_config=_provider_config(),
        toolset_profile="full",
        runner=_FakeRunner(),
    )

    tools, _collector = runner._resolve_turn_toolset("session-tighten-empty", request=req)
    tool_names = {t.name for t in tools}

    # Empty cap → no-op, all tools returned
    assert tool_names == {t.name for t in _FULL_TOOLS}


# ---------------------------------------------------------------------------
# Test 4: Flag ON + absent parentToolNames (no metadata key) → full profile
# ---------------------------------------------------------------------------


def test_tighten_only_flag_on_absent_parent_names_returns_full_profile(monkeypatch) -> None:
    """Flag ON + parentToolNames absent → no narrowing (fail-open no-op)."""
    _patch_full_tools(monkeypatch)
    monkeypatch.setenv("MAGI_SUBAGENT_TOOL_TIGHTEN_ONLY_ENABLED", "1")

    req = _request()  # no metadata
    runner = RealLocalChildRunner(
        provider_config=_provider_config(),
        toolset_profile="full",
        runner=_FakeRunner(),
    )

    tools, _collector = runner._resolve_turn_toolset("session-tighten-absent", request=req)
    tool_names = {t.name for t in tools}

    assert tool_names == {t.name for t in _FULL_TOOLS}


# ---------------------------------------------------------------------------
# Test 5: Flag ON + parentToolNames — readonly profile, intersects with cap
# ---------------------------------------------------------------------------


def test_tighten_only_readonly_profile_intersects_with_cap(monkeypatch) -> None:
    """Tighten-only with readonly profile + cap that is a sub-subset of readonly."""
    from magi_agent.runtime.child_toolset import READONLY_TOOL_NAMES

    # Simulate a parent that only has FileRead and Glob
    small_cap = frozenset({"FileRead", "Glob"})

    def _fake_build_tools(**kwargs: object) -> list[_NamedTool]:
        # readonly profile builds these tools
        return [_NamedTool(n) for n in READONLY_TOOL_NAMES]

    _patch_build_runner(monkeypatch)
    monkeypatch.setattr(tool_runtime_mod, "build_cli_adk_tools", _fake_build_tools)
    monkeypatch.setenv("MAGI_SUBAGENT_TOOL_TIGHTEN_ONLY_ENABLED", "1")

    req = _request(metadata={"parentToolNames": tuple(small_cap)})
    runner = RealLocalChildRunner(
        provider_config=_provider_config(),
        toolset_profile="readonly",
        runner=_FakeRunner(),
    )

    tools, _collector = runner._resolve_turn_toolset("session-tighten-ro", request=req)
    tool_names = {t.name for t in tools}

    # Only the intersection of readonly profile AND small_cap
    assert tool_names == small_cap
    assert "Grep" not in tool_names
    assert "GitDiff" not in tool_names
