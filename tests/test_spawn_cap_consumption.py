"""Tests for Seam 4: spawn_cap ceiling consumed in _resolve_turn_toolset.

Coverage (TDD — written RED first):
- T1: Flag OFF + spawn_cap=("FileRead","Glob") → toolset UNCHANGED vs no-cap baseline.
- T2: Flag ON + spawn_cap=("FileRead","Glob") on full-profile runner →
      returned toolset contains ONLY tools whose name ∈ {FileRead,Glob}; Bash dropped.
- T3: Flag ON + spawn_cap=None → unchanged (no cap to apply).
- T4: Flag ON + spawn_cap intersection composes with profile (readonly profile ∩
      spawn_cap) — result is the intersection, order preserved.
"""

from __future__ import annotations

import pytest

import magi_agent.cli.tool_runtime as tool_runtime_mod
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
    "MAGI_SPAWN_RECIPE_CAP_ENABLED",
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path) -> None:
    """Hermetic: clear provider keys, tighten and spawn-cap flags."""
    for name in _PROVIDER_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "absent.toml"))


# ---------------------------------------------------------------------------
# Shared named-tool stub
# ---------------------------------------------------------------------------


class _NamedTool:
    def __init__(self, name: str) -> None:
        self.name = name


# A "full" profile toolset with both readonly and mutating tools.
_FULL_TOOLS = [
    _NamedTool("FileRead"),
    _NamedTool("Glob"),
    _NamedTool("Grep"),
    _NamedTool("GitDiff"),
    _NamedTool("FileWrite"),
    _NamedTool("Bash"),
    _NamedTool("Edit"),
]

# Readonly names subset.
_READONLY_NAMES = frozenset({"FileRead", "Glob", "Grep", "GitDiff"})


def _patch_full_tools(monkeypatch) -> None:
    """Patch build_cli_adk_tools to return the full mixed toolset."""

    def _fake_build_tools(**kwargs: object) -> list[_NamedTool]:
        return list(_FULL_TOOLS)

    monkeypatch.setattr(tool_runtime_mod, "build_cli_adk_tools", _fake_build_tools)


def _provider_config(api_key: str = "sk-test") -> object:
    from magi_agent.cli.providers import ProviderConfig

    return ProviderConfig(provider="anthropic", model="claude-sonnet-4-6", api_key=api_key)


# ---------------------------------------------------------------------------
# T1: Flag OFF + spawn_cap set → toolset UNCHANGED (byte-identical)
# ---------------------------------------------------------------------------


def test_spawn_cap_flag_off_toolset_unchanged(monkeypatch) -> None:
    """When MAGI_SPAWN_RECIPE_CAP_ENABLED is OFF, spawn_cap has no effect.

    The toolset returned must be byte-identical to the no-cap baseline: all
    full-profile tools are forwarded regardless of spawn_cap content.
    """
    _patch_full_tools(monkeypatch)
    monkeypatch.delenv("MAGI_SPAWN_RECIPE_CAP_ENABLED", raising=False)

    runner = RealLocalChildRunner(
        provider_config=_provider_config(),
        toolset_profile="full",
        spawn_cap=("FileRead", "Glob"),
    )

    tools, collector = runner._resolve_turn_toolset("sess-t1")
    tool_names = {t.name for t in tools}

    # Cap is ignored when the flag is OFF — full profile returned.
    assert tool_names == {t.name for t in _FULL_TOOLS}
    assert "Bash" in tool_names
    assert "FileWrite" in tool_names


# ---------------------------------------------------------------------------
# T2: Flag ON + spawn_cap → only matching tools survive
# ---------------------------------------------------------------------------


def test_spawn_cap_flag_on_filters_to_cap(monkeypatch) -> None:
    """When flag is ON and spawn_cap=("FileRead","Glob"), only those two tools
    are returned from a full-profile runner; Bash and others are dropped.
    """
    _patch_full_tools(monkeypatch)
    monkeypatch.setenv("MAGI_SPAWN_RECIPE_CAP_ENABLED", "1")

    runner = RealLocalChildRunner(
        provider_config=_provider_config(),
        toolset_profile="full",
        spawn_cap=("FileRead", "Glob"),
    )

    tools, collector = runner._resolve_turn_toolset("sess-t2")
    tool_names = {t.name for t in tools}

    assert tool_names == {"FileRead", "Glob"}
    assert "Bash" not in tool_names
    assert "FileWrite" not in tool_names
    assert "Edit" not in tool_names
    assert "Grep" not in tool_names


# ---------------------------------------------------------------------------
# T3: Flag ON + spawn_cap=None → toolset UNCHANGED
# ---------------------------------------------------------------------------


def test_spawn_cap_flag_on_none_cap_toolset_unchanged(monkeypatch) -> None:
    """When spawn_cap is None and flag is ON, no intersection is applied."""
    _patch_full_tools(monkeypatch)
    monkeypatch.setenv("MAGI_SPAWN_RECIPE_CAP_ENABLED", "1")

    runner = RealLocalChildRunner(
        provider_config=_provider_config(),
        toolset_profile="full",
        spawn_cap=None,
    )

    tools, collector = runner._resolve_turn_toolset("sess-t3")
    tool_names = {t.name for t in tools}

    # None spawn_cap → no-op, all full-profile tools returned.
    assert tool_names == {t.name for t in _FULL_TOOLS}


# ---------------------------------------------------------------------------
# T4: Flag ON + readonly profile ∩ spawn_cap → intersection, order preserved
# ---------------------------------------------------------------------------


def test_spawn_cap_flag_on_composes_with_readonly_profile(monkeypatch) -> None:
    """spawn_cap intersection composes with profile filtering.

    readonly profile → {FileRead, Glob, Grep, GitDiff}
    spawn_cap        → ("FileRead", "Glob", "Bash")   (Bash not in readonly)
    result           → {FileRead, Glob}               (intersection)

    Order is preserved: FileRead comes before Glob in the tool list.
    """
    from magi_agent.runtime.child_toolset import READONLY_TOOL_NAMES

    def _fake_build_tools(**kwargs: object) -> list[_NamedTool]:
        # Return all readonly tools in a deterministic order.
        return [_NamedTool(n) for n in READONLY_TOOL_NAMES]

    monkeypatch.setattr(tool_runtime_mod, "build_cli_adk_tools", _fake_build_tools)
    monkeypatch.setenv("MAGI_SPAWN_RECIPE_CAP_ENABLED", "1")

    # Cap includes Bash (not in readonly), so the intersection excludes it.
    runner = RealLocalChildRunner(
        provider_config=_provider_config(),
        toolset_profile="readonly",
        spawn_cap=("FileRead", "Glob", "Bash"),
    )

    tools, collector = runner._resolve_turn_toolset("sess-t4")
    tool_names = [t.name for t in tools]

    # Result is intersection of readonly profile AND spawn_cap (no Bash/Grep/GitDiff).
    assert set(tool_names) == {"FileRead", "Glob"}
    # Order preserved: FileRead before Glob (as they appear in READONLY_TOOL_NAMES).
    assert tool_names.index("FileRead") < tool_names.index("Glob")
    assert "Grep" not in tool_names
    assert "GitDiff" not in tool_names
    assert "Bash" not in tool_names
