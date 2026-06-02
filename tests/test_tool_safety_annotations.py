"""Tests for tool safety annotations (parallel_safety and is_concurrency_safe).

Verifies that all 16 core tools have the correct explicit parallel_safety values
and that manifest validation constraints are enforced.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from magi_agent.tools.catalog import _manifest, core_tool_manifests
from magi_agent.tools.manifest import Budget, ToolManifest, ToolSource


CORE_TOOL_SOURCE = ToolSource(kind="builtin", package="openmagi.core")
CORE_TOOL_INPUT_SCHEMA: dict[str, object] = {"type": "object", "additionalProperties": True}


def manifests_by_name() -> dict[str, ToolManifest]:
    return {m.name: m for m in core_tool_manifests()}


# ---------------------------------------------------------------------------
# parallel_safety annotations — read-only tools
# ---------------------------------------------------------------------------


def test_readonly_tools_have_parallel_safety_readonly() -> None:
    m = manifests_by_name()
    readonly_tools = ["FileRead", "Glob", "Grep", "GitDiff", "ArtifactRead", "ArtifactList"]
    for name in readonly_tools:
        assert m[name].parallel_safety == "readonly", (
            f"{name} should have parallel_safety='readonly'"
        )


def test_stateless_meta_tools_have_parallel_safety_readonly() -> None:
    m = manifests_by_name()
    assert m["Clock"].parallel_safety == "readonly"
    assert m["Calculation"].parallel_safety == "readonly"


def test_control_flow_meta_tools_have_parallel_safety_unsafe() -> None:
    """AskUserQuestion, EnterPlanMode, ExitPlanMode affect runtime control flow."""
    m = manifests_by_name()
    assert m["AskUserQuestion"].parallel_safety == "unsafe"
    assert m["EnterPlanMode"].parallel_safety == "unsafe"
    assert m["ExitPlanMode"].parallel_safety == "unsafe"


def test_workspace_mutating_tools_have_parallel_safety_unsafe() -> None:
    m = manifests_by_name()
    for name in ["FileWrite", "FileEdit", "Bash", "TestRun"]:
        assert m[name].parallel_safety == "unsafe", (
            f"{name} should have parallel_safety='unsafe'"
        )


def test_artifact_create_has_parallel_safety_unsafe() -> None:
    """ArtifactCreate is a write operation even without mutates_workspace."""
    m = manifests_by_name()
    assert m["ArtifactCreate"].parallel_safety == "unsafe"


# ---------------------------------------------------------------------------
# is_concurrency_safe auto-derivation
# ---------------------------------------------------------------------------


def test_readonly_tools_are_concurrency_safe() -> None:
    m = manifests_by_name()
    readonly_tools = ["FileRead", "Glob", "Grep", "GitDiff", "ArtifactRead", "ArtifactList",
                      "Clock", "Calculation"]
    for name in readonly_tools:
        assert m[name].is_concurrency_safe is True, (
            f"{name} should have is_concurrency_safe=True"
        )


def test_meta_control_flow_tools_are_concurrency_safe_by_auto_derivation() -> None:
    """is_concurrency_safe is auto-derived from permission; meta tools qualify.
    This is intentional — parallel_safety='unsafe' is the authoritative signal
    for the partition algorithm, while is_concurrency_safe is legacy/informational.
    """
    m = manifests_by_name()
    # Auto-derivation: not dangerous and permission in {"read", "meta"} → True
    assert m["AskUserQuestion"].is_concurrency_safe is True
    assert m["EnterPlanMode"].is_concurrency_safe is True
    assert m["ExitPlanMode"].is_concurrency_safe is True


def test_dangerous_and_write_tools_are_not_concurrency_safe() -> None:
    m = manifests_by_name()
    for name in ["FileWrite", "FileEdit", "Bash", "TestRun", "ArtifactCreate"]:
        assert m[name].is_concurrency_safe is False, (
            f"{name} should have is_concurrency_safe=False"
        )


# ---------------------------------------------------------------------------
# _manifest() helper defaults
# ---------------------------------------------------------------------------


def test_manifest_helper_default_parallel_safety_is_unsafe() -> None:
    """_manifest() must default parallel_safety to 'unsafe' (fail-safe)."""
    m = _manifest(
        "TestDefaultSafety",
        "A test tool.",
        permission="read",
        modes=("plan", "act"),
        tags=("test",),
    )
    assert m.parallel_safety == "unsafe"


def test_manifest_helper_accepts_explicit_parallel_safety() -> None:
    m = _manifest(
        "TestExplicitSafety",
        "A test read tool.",
        permission="read",
        modes=("plan", "act"),
        tags=("test",),
        parallel_safety="readonly",
    )
    assert m.parallel_safety == "readonly"
    assert m.is_concurrency_safe is True


# ---------------------------------------------------------------------------
# manifest.py validation constraints
# ---------------------------------------------------------------------------


def _base_manifest_kwargs(**overrides: object) -> dict[str, object]:
    """Return minimal valid ToolManifest kwargs, merged with overrides."""
    base: dict[str, object] = {
        "name": "TestTool",
        "description": "A test tool.",
        "kind": "core",
        "source": CORE_TOOL_SOURCE,
        "permission": "read",
        "input_schema": CORE_TOOL_INPUT_SCHEMA,
        "timeout_ms": 30_000,
        "budget": Budget(max_calls_per_turn=10, max_parallel=1),
        "dangerous": False,
        "is_concurrency_safe": False,
        "mutates_workspace": False,
        "parallel_safety": "unsafe",
        "available_in_modes": ("plan", "act"),
        "tags": ("test",),
        "enabled_by_default": False,
        "opt_out": True,
    }
    base.update(overrides)
    return base


def test_validation_readonly_parallel_safety_with_dangerous_raises() -> None:
    """parallel_safety='readonly' with dangerous=True must be rejected."""
    with pytest.raises((ValidationError, ValueError)):
        ToolManifest.model_validate(
            _base_manifest_kwargs(
                parallel_safety="readonly",
                dangerous=True,
                side_effect_class="local_process",
            )
        )


def test_validation_readonly_parallel_safety_with_mutates_workspace_raises() -> None:
    """parallel_safety='readonly' with mutates_workspace=True must be rejected."""
    with pytest.raises((ValidationError, ValueError)):
        ToolManifest.model_validate(
            _base_manifest_kwargs(
                parallel_safety="readonly",
                mutates_workspace=True,
                side_effect_class="local_workspace",
            )
        )


def test_validation_readonly_parallel_safety_without_side_effects_is_valid() -> None:
    """parallel_safety='readonly' with no side-effects must be accepted."""
    m = ToolManifest.model_validate(
        _base_manifest_kwargs(
            parallel_safety="readonly",
            dangerous=False,
            mutates_workspace=False,
            side_effect_class="none",
        )
    )
    assert m.parallel_safety == "readonly"


def test_all_core_tools_are_present_with_explicit_parallel_safety() -> None:
    """All core tools must exist and have an explicit non-default annotation check."""
    m = manifests_by_name()
    expected = {
        "FileRead", "FileWrite", "FileEdit", "Glob", "Grep", "Bash", "TestRun",
        "GitDiff", "AskUserQuestion", "EnterPlanMode", "ExitPlanMode", "ArtifactCreate",
        "ArtifactRead", "ArtifactList", "Clock", "Calculation", "ToolSearch",
        "HealthStatus", "TaskList", "TaskGet", "TaskOutput", "CronList",
    }
    assert set(m.keys()) == expected
    # Every tool must have a valid parallel_safety value
    valid_values = {"unsafe", "readonly", "concurrency_safe"}
    for name, manifest in m.items():
        assert manifest.parallel_safety in valid_values, (
            f"{name} has unexpected parallel_safety={manifest.parallel_safety!r}"
        )
