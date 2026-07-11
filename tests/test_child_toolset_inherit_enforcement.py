"""Tests for the ``inherit`` profile enforcement path in ``_resolve_turn_toolset``.

The ``inherit`` profile (PR: child-toolset-inherit-default) is now the default
when ``MAGI_CHILD_RUNNER_TOOLSET`` is unset.  It intersects the core toolset
with the parent's forwarded ``parentToolNames`` so the child never exceeds the
parent's capability.

Key invariants tested:
- A parent that advertises WebSearch/WebFetch forwards those to the child.
- A parent that has ONLY readonly tools produces a readonly-floor child.
- A parent with mutating tools (Bash, FileWrite) passes them through.
- Empty ``parentToolNames`` -> readonly floor (never full, never none).
- Absent ``parentToolNames`` (no metadata key) -> readonly floor.
- Inherit is orthogonal to the tighten-only flag (both can be active; the
  inherit intersection fires first, then tighten-only fires on the result).

Hermetic: no real model / no provider key. Same autouse env-isolation pattern
as ``test_subagent_tighten_only.py``.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest

import magi_agent.cli.tool_runtime as tool_runtime_mod
from magi_agent.runtime.child_runner_boundary import ChildTaskRequest
from magi_agent.runtime.child_runner_live import RealLocalChildRunner
from magi_agent.runtime.child_toolset import READONLY_TOOL_NAMES

# ---------------------------------------------------------------------------
# Env isolation (autouse)
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
    "MAGI_CHILD_RUNNER_TOOLSET",
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path) -> None:
    """Hermetic: no real key / config / toolset env."""
    for name in _PROVIDER_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "absent.toml"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _request(**overrides: object) -> ChildTaskRequest:
    data: dict[str, object] = {
        "parentExecutionId": "parent-exec-inherit",
        "turnId": "turn-inherit-1",
        "taskId": "task-inherit-1",
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


class _FakeRunner:
    async def run_async(self, **kwargs: object) -> AsyncGenerator[object, None]:
        yield object()  # never iterated in these unit tests


# Simulate a parent that has both readonly and web-research tools.
_PARENT_WITH_WEB = (
    "FileRead",
    "Glob",
    "Grep",
    "WebSearch",
    "WebFetch",
)

# Simulate a parent with mutating tools.
_PARENT_WITH_MUTATING = (
    "FileRead",
    "Glob",
    "FileWrite",
    "Bash",
    "Edit",
)

# Core toolset available to the child runner (superset of all tool names).
_CORE_TOOLS = [
    _NamedTool("FileRead"),
    _NamedTool("Glob"),
    _NamedTool("Grep"),
    _NamedTool("GitDiff"),
    _NamedTool("WebSearch"),
    _NamedTool("WebFetch"),
    _NamedTool("FileWrite"),
    _NamedTool("Bash"),
    _NamedTool("Edit"),
    _NamedTool("Calculation"),
]


def _patch_core_tools(monkeypatch) -> None:
    """Patch build_cli_adk_tools to return the full mixed core toolset."""

    def _fake_build_tools(**kwargs: object) -> list[_NamedTool]:
        return list(_CORE_TOOLS)

    monkeypatch.setattr(tool_runtime_mod, "build_cli_adk_tools", _fake_build_tools)


def _runner_inherit(tmp_path) -> RealLocalChildRunner:
    return RealLocalChildRunner(
        provider_config=_provider_config(),
        toolset_profile="inherit",
        workspace_root=str(tmp_path),
        runner=_FakeRunner(),
    )


# ---------------------------------------------------------------------------
# Test 1: Primary regression - parent with WebSearch/WebFetch forwards them
# ---------------------------------------------------------------------------


def test_inherit_parent_websearch_reaches_child(monkeypatch, tmp_path) -> None:
    """Inherit profile: a parent that has WebSearch/WebFetch passes those tools
    to the child (primary regression guard for the inherit-default PR).

    Before this PR the default was ``none`` (empty toolset); the inherit profile
    now propagates whatever the parent had. If the parent is a web-research agent
    its children should inherit the same web-research surface.
    """
    _patch_core_tools(monkeypatch)

    req = _request(metadata={"parentToolNames": _PARENT_WITH_WEB})
    runner = _runner_inherit(tmp_path)

    tools, collector = runner._resolve_turn_toolset("session-inherit-web", request=req)
    tool_names = {t.name for t in tools}

    # Web-research tools the parent had must reach the child.
    assert "WebSearch" in tool_names, (
        f"WebSearch missing from inherit child; got {sorted(tool_names)!r}"
    )
    assert "WebFetch" in tool_names
    # Readonly source-inspection tools also pass through (subset of parent cap).
    assert "FileRead" in tool_names
    assert "Glob" in tool_names
    assert "Grep" in tool_names
    # Mutating tools NOT in parent cap must be excluded.
    assert "FileWrite" not in tool_names
    assert "Bash" not in tool_names
    assert "Edit" not in tool_names
    # Evidence collector is constructed for non-none profiles.
    assert collector is not None


# ---------------------------------------------------------------------------
# Test 2: Readonly-only parent -> readonly floor (no mutating tools)
# ---------------------------------------------------------------------------


def test_inherit_readonly_only_parent_produces_readonly_child(monkeypatch, tmp_path) -> None:
    """Inherit profile: when the parent only has readonly tools the child
    receives exactly those tools (no mutating escalation)."""
    _patch_core_tools(monkeypatch)

    readonly_parent = ("FileRead", "Glob", "Grep", "GitDiff", "Calculation")
    req = _request(metadata={"parentToolNames": readonly_parent})
    runner = _runner_inherit(tmp_path)

    tools, _collector = runner._resolve_turn_toolset("session-inherit-ro", request=req)
    tool_names = {t.name for t in tools}

    # Only readonly tools.
    assert "FileRead" in tool_names
    assert "Calculation" in tool_names
    # Mutating tools absent.
    assert "FileWrite" not in tool_names
    assert "Bash" not in tool_names


# ---------------------------------------------------------------------------
# Test 3: Parent with mutating tools passes them through
# ---------------------------------------------------------------------------


def test_inherit_mutating_parent_passes_mutating_tools(monkeypatch, tmp_path) -> None:
    """Inherit profile: when the parent has FileWrite/Bash/Edit the child
    receives those tools too (capability parity, not arbitrary restriction)."""
    _patch_core_tools(monkeypatch)

    req = _request(metadata={"parentToolNames": _PARENT_WITH_MUTATING})
    runner = _runner_inherit(tmp_path)

    tools, _collector = runner._resolve_turn_toolset("session-inherit-mut", request=req)
    tool_names = {t.name for t in tools}

    assert "FileWrite" in tool_names
    assert "Bash" in tool_names
    assert "Edit" in tool_names
    # Web tools NOT in parent cap are absent.
    assert "WebSearch" not in tool_names
    assert "WebFetch" not in tool_names


# ---------------------------------------------------------------------------
# Test 4: Empty parentToolNames -> readonly floor
# ---------------------------------------------------------------------------


def test_inherit_empty_parent_cap_applies_readonly_floor(monkeypatch, tmp_path) -> None:
    """Inherit profile: empty ``parentToolNames`` triggers the readonly floor
    (D2 safety fallback - never silently over-privileged)."""
    _patch_core_tools(monkeypatch)

    req = _request(metadata={"parentToolNames": ()})
    runner = _runner_inherit(tmp_path)

    tools, _collector = runner._resolve_turn_toolset("session-inherit-empty", request=req)
    tool_names = {t.name for t in tools}

    # Readonly floor: only READONLY_TOOL_NAMES pass.
    readonly_set = set(READONLY_TOOL_NAMES)
    assert tool_names <= readonly_set, (
        f"Expected only readonly tools; extra: {tool_names - readonly_set!r}"
    )
    # Mutating tools are excluded.
    assert "FileWrite" not in tool_names
    assert "Bash" not in tool_names


# ---------------------------------------------------------------------------
# Test 5: Absent parentToolNames (no metadata key) -> readonly floor
# ---------------------------------------------------------------------------


def test_inherit_absent_parent_names_applies_readonly_floor(monkeypatch, tmp_path) -> None:
    """Inherit profile: absent ``parentToolNames`` (no metadata at all) triggers
    the readonly floor (same as empty - D2 safety fallback)."""
    _patch_core_tools(monkeypatch)

    req = _request()  # no metadata -> parentToolNames absent
    runner = _runner_inherit(tmp_path)

    tools, _collector = runner._resolve_turn_toolset("session-inherit-absent", request=req)
    tool_names = {t.name for t in tools}

    readonly_set = set(READONLY_TOOL_NAMES)
    assert tool_names <= readonly_set, (
        f"Expected only readonly tools; extra: {tool_names - readonly_set!r}"
    )
    assert "FileWrite" not in tool_names
    assert "Bash" not in tool_names


# ---------------------------------------------------------------------------
# Test 6: Inherit + tighten-only flag - both apply, inherit fires first
# ---------------------------------------------------------------------------


def test_inherit_with_tighten_only_applies_both_narrowing_layers(
    monkeypatch, tmp_path
) -> None:
    """Inherit profile + tighten-only flag ON: inherit fires first (intersection
    with parentToolNames), then tighten-only fires on the result.

    In this case the request metadata has ``parentToolNames`` = web-research set.
    The inherit intersection keeps WebSearch/WebFetch/FileRead/Glob/Grep.
    Tighten-only then intersects with the same cap (no further narrowing).
    Net result: WebSearch/WebFetch/FileRead/Glob/Grep remain; mutating tools gone.
    """
    _patch_core_tools(monkeypatch)
    monkeypatch.setenv("MAGI_SUBAGENT_TOOL_TIGHTEN_ONLY_ENABLED", "1")

    req = _request(metadata={"parentToolNames": _PARENT_WITH_WEB})
    runner = _runner_inherit(tmp_path)

    tools, _collector = runner._resolve_turn_toolset("session-inherit-tighten", request=req)
    tool_names = {t.name for t in tools}

    assert "WebSearch" in tool_names
    assert "WebFetch" in tool_names
    assert "FileRead" in tool_names
    # Mutating tools absent (not in parent cap).
    assert "FileWrite" not in tool_names
    assert "Bash" not in tool_names
