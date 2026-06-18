"""Tests for P2-T3: allowedTools per-task grant consumed in _resolve_turn_toolset.

Coverage (TDD — written RED first):
- T1: Flag OFF + metadata allowedTools=("FileRead",) → toolset UNCHANGED.
- T2: Flag ON + allowedTools=("FileRead","Glob") on full-profile runner →
      toolset == only {FileRead, Glob}; Bash dropped.
- T3: Flag ON + no allowedTools key → unchanged (profile/2B.3/spawn_cap only).
- T4: INTEGRATION — Flag ON, full profile, allowedTools=("FileRead","Glob","Bash")
      AND spawn_cap=("FileRead","Glob") → final toolset == {FileRead,Glob}.
      Proves profile ∩ allowedTools ∩ spawn_cap composition.
- T5: Order/precedence — allowedTools applied before spawn_cap; final set is
      the 3-way intersection.
"""

from __future__ import annotations

import pytest

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
    "MAGI_SPAWN_RECIPE_CAP_ENABLED",
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path) -> None:
    """Hermetic: clear provider keys, tighten and spawn-cap flags."""
    for name in _PROVIDER_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "absent.toml"))


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


class _NamedTool:
    def __init__(self, name: str) -> None:
        self.name = name


_FULL_TOOLS = [
    _NamedTool("FileRead"),
    _NamedTool("Glob"),
    _NamedTool("Grep"),
    _NamedTool("GitDiff"),
    _NamedTool("FileWrite"),
    _NamedTool("Bash"),
    _NamedTool("Edit"),
]


def _patch_full_tools(monkeypatch) -> None:
    def _fake_build_tools(**kwargs: object) -> list[_NamedTool]:
        return list(_FULL_TOOLS)

    monkeypatch.setattr(tool_runtime_mod, "build_cli_adk_tools", _fake_build_tools)


def _provider_config(api_key: str = "sk-test") -> object:
    from magi_agent.cli.providers import ProviderConfig

    return ProviderConfig(provider="anthropic", model="claude-sonnet-4-6", api_key=api_key)


def _request(**overrides: object) -> ChildTaskRequest:
    data: dict[str, object] = {
        "parentExecutionId": "parent-exec-allowed",
        "turnId": "turn-allowed-1",
        "taskId": "task-allowed-1",
        "objective": "Complete delegated subtask.",
        "role": "research",
        "delivery": "return",
    }
    data.update(overrides)
    return ChildTaskRequest(**data)


# ---------------------------------------------------------------------------
# T1: Flag OFF + allowedTools set → toolset UNCHANGED (byte-identical)
# ---------------------------------------------------------------------------


def test_allowed_tools_flag_off_toolset_unchanged(monkeypatch) -> None:
    """When MAGI_SPAWN_RECIPE_CAP_ENABLED is OFF, allowedTools has no effect."""
    _patch_full_tools(monkeypatch)
    monkeypatch.delenv("MAGI_SPAWN_RECIPE_CAP_ENABLED", raising=False)

    req = _request(metadata={"allowedTools": ("FileRead",)})
    runner = RealLocalChildRunner(
        provider_config=_provider_config(),
        toolset_profile="full",
    )

    tools, _collector = runner._resolve_turn_toolset("sess-at1", request=req)
    tool_names = {t.name for t in tools}

    # allowedTools ignored when flag OFF — full profile returned.
    assert tool_names == {t.name for t in _FULL_TOOLS}
    assert "Bash" in tool_names
    assert "FileWrite" in tool_names


# ---------------------------------------------------------------------------
# T2: Flag ON + allowedTools → only matching tools survive
# ---------------------------------------------------------------------------


def test_allowed_tools_flag_on_filters_to_grant(monkeypatch) -> None:
    """Flag ON + allowedTools=("FileRead","Glob") → only those two returned.

    Bash and other tools from the full profile are dropped.
    """
    _patch_full_tools(monkeypatch)
    monkeypatch.setenv("MAGI_SPAWN_RECIPE_CAP_ENABLED", "1")

    req = _request(metadata={"allowedTools": ("FileRead", "Glob")})
    runner = RealLocalChildRunner(
        provider_config=_provider_config(),
        toolset_profile="full",
    )

    tools, _collector = runner._resolve_turn_toolset("sess-at2", request=req)
    tool_names = {t.name for t in tools}

    assert tool_names == {"FileRead", "Glob"}
    assert "Bash" not in tool_names
    assert "FileWrite" not in tool_names
    assert "Edit" not in tool_names
    assert "Grep" not in tool_names


# ---------------------------------------------------------------------------
# T3: Flag ON + no allowedTools key → toolset UNCHANGED
# ---------------------------------------------------------------------------


def test_allowed_tools_absent_key_toolset_unchanged(monkeypatch) -> None:
    """Flag ON + no allowedTools in metadata → no-op (profile only)."""
    _patch_full_tools(monkeypatch)
    monkeypatch.setenv("MAGI_SPAWN_RECIPE_CAP_ENABLED", "1")

    req = _request()  # no metadata
    runner = RealLocalChildRunner(
        provider_config=_provider_config(),
        toolset_profile="full",
    )

    tools, _collector = runner._resolve_turn_toolset("sess-at3", request=req)
    tool_names = {t.name for t in tools}

    assert tool_names == {t.name for t in _FULL_TOOLS}


# ---------------------------------------------------------------------------
# T4: INTEGRATION — profile ∩ allowedTools ∩ spawn_cap
# ---------------------------------------------------------------------------


def test_allowed_tools_integration_three_way_intersection(monkeypatch) -> None:
    """Integration: Flag ON, full profile, allowedTools=("FileRead","Glob","Bash"),
    spawn_cap=("FileRead","Glob") → final == {FileRead, Glob}.

    Bash is allowed by the per-task grant but cut by the spawn_cap ceiling.
    Proves the composition: profile ∩ allowedTools ∩ spawn_cap.
    """
    _patch_full_tools(monkeypatch)
    monkeypatch.setenv("MAGI_SPAWN_RECIPE_CAP_ENABLED", "1")

    req = _request(metadata={"allowedTools": ("FileRead", "Glob", "Bash")})
    runner = RealLocalChildRunner(
        provider_config=_provider_config(),
        toolset_profile="full",
        spawn_cap=("FileRead", "Glob"),
    )

    tools, _collector = runner._resolve_turn_toolset("sess-at4", request=req)
    tool_names = {t.name for t in tools}

    # Bash is in allowedTools but NOT in spawn_cap → dropped by ceiling.
    assert tool_names == {"FileRead", "Glob"}
    assert "Bash" not in tool_names
    assert "FileWrite" not in tool_names
    assert "Edit" not in tool_names


# ---------------------------------------------------------------------------
# T5: Ordering — allowedTools before spawn_cap; result is 3-way intersection
# ---------------------------------------------------------------------------


def test_allowed_tools_ordering_before_spawn_cap(monkeypatch) -> None:
    """Ordering proof: allowedTools applied before spawn_cap.

    allowedTools=("FileRead","Bash") AND spawn_cap=("FileRead","Glob")
    → intermediate after allowedTools = {FileRead, Bash}
    → final after spawn_cap = {FileRead}  (Bash not in cap; Glob not in grant)

    This set equals the 3-way intersection: profile ∩ {FileRead,Bash} ∩ {FileRead,Glob}.
    """
    _patch_full_tools(monkeypatch)
    monkeypatch.setenv("MAGI_SPAWN_RECIPE_CAP_ENABLED", "1")

    req = _request(metadata={"allowedTools": ("FileRead", "Bash")})
    runner = RealLocalChildRunner(
        provider_config=_provider_config(),
        toolset_profile="full",
        spawn_cap=("FileRead", "Glob"),
    )

    tools, _collector = runner._resolve_turn_toolset("sess-at5", request=req)
    tool_names = {t.name for t in tools}

    # Only FileRead survives both gates.
    assert tool_names == {"FileRead"}
    assert "Bash" not in tool_names  # in grant but not in cap
    assert "Glob" not in tool_names  # in cap but not in grant
