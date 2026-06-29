"""PR-N: child-runner readonly toolset includes the pure ``Calculation`` tool.

Kevin 0.1.91 direct-debug exposed a real production bug after the PR-M
provider routing fix: 6 of 9 SOTA-spawn children (opus, haiku, gemini flash
variants, some gpt-5.5) crash on a simple ``1+1`` cross-validate spawn with::

    [engine.trace] llm_call_exception attempt=1 turn_id=child-session-...
      exception=ValueError
      message_first80="Tool 'Calculation' not found.
       Available tools: FileRead, GitDiff, Glob, Grep"

Models that try to use a tool for arithmetic hit the readonly child profile,
which currently filters the bound core toolset down to FileRead / Glob / Grep
/ GitDiff. ``Calculation`` is bound (``bind_core_toolhost_handlers`` registers
it unconditionally) but the profile filter drops it.

Fix (Shape A, minimal, safest): include ``Calculation`` in the readonly
profile allowlist. Calculation is a deterministic AST-based expression
evaluator with NO filesystem or network side effects, so adding it does not
expand the workspace-mutation or egress surface.

These tests pin the new contract end-to-end:
1. The constant ``READONLY_TOOL_NAMES`` lists ``Calculation``.
2. ``_resolve_turn_toolset`` forwards a ``Calculation`` ADK tool to the
   governed runtime so a model that emits ``tool_call(name="Calculation")``
   no longer crashes with ``Tool not found``.
3. Workspace-mutation tools (FileWrite/FileEdit/PatchApply/Bash) and the
   broader ``PythonExec`` are STILL filtered out under readonly.
4. Pre-existing profile semantics (``none`` empty, ``full`` unfiltered) are
   unchanged.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from magi_agent.runtime.child_runner_live import RealLocalChildRunner
from magi_agent.runtime.child_toolset import (
    READONLY_TOOL_NAMES,
    resolve_child_toolset_profile,
    toolset_allowlist,
)


def _tool_name(tool: object) -> str:
    name = getattr(tool, "name", None)
    return str(name) if name is not None else ""


# --------------------------------------------------------------------------- #
# 1. Constant contract                                                         #
# --------------------------------------------------------------------------- #


def test_default_readonly_toolset_includes_calculation() -> None:
    """``READONLY_TOOL_NAMES`` lists Calculation alongside the inspection set."""
    assert "Calculation" in READONLY_TOOL_NAMES, (
        "Calculation must be in READONLY_TOOL_NAMES so the readonly child "
        "profile can serve simple-arithmetic models without a 'Tool not found' "
        "ValueError. Kevin 0.1.91 direct-debug: 6/9 SOTA-spawn children failed."
    )


def test_readonly_allowlist_keeps_source_inspection_tools() -> None:
    """Inspection tools are still present (regression guard)."""
    for name in ("FileRead", "Glob", "Grep", "GitDiff"):
        assert name in READONLY_TOOL_NAMES, (
            f"{name!r} must remain in READONLY_TOOL_NAMES; adding Calculation "
            "is additive, never subtractive."
        )


def test_readonly_allowlist_excludes_mutating_and_exec_tools() -> None:
    """Workspace-mutating and execution tools must NOT appear in readonly."""
    for name in (
        "FileWrite",
        "FileEdit",
        "PatchApply",
        "ApplyPatch",
        "Bash",
        "TestRun",
        "PythonExec",
        "FileDelete",
        "WorkspaceMutate",
        "BrowserOpen",
    ):
        assert name not in READONLY_TOOL_NAMES, (
            f"{name!r} must NOT be in the readonly allowlist (security invariant)."
        )


def test_toolset_allowlist_readonly_includes_calculation() -> None:
    """The allowlist callable returns the augmented set for the readonly profile."""
    allow = toolset_allowlist("readonly")
    assert allow is not None
    assert "Calculation" in allow


def test_resolve_profile_unchanged_for_known_values(monkeypatch) -> None:
    """Adding Calculation must NOT change ``resolve_child_toolset_profile``
    semantics for any of the three documented profile literals."""
    monkeypatch.delenv("MAGI_CHILD_RUNNER_TOOLSET", raising=False)
    assert resolve_child_toolset_profile() == "none"
    monkeypatch.setenv("MAGI_CHILD_RUNNER_TOOLSET", "readonly")
    assert resolve_child_toolset_profile() == "readonly"
    monkeypatch.setenv("MAGI_CHILD_RUNNER_TOOLSET", "full")
    assert resolve_child_toolset_profile() == "full"


# --------------------------------------------------------------------------- #
# 2. Runtime wiring contract: _resolve_turn_toolset exposes the tool           #
# --------------------------------------------------------------------------- #


def test_resolve_turn_toolset_readonly_exposes_calculation(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """End-to-end through ``_resolve_turn_toolset``: a readonly child receives
    an ADK ``Calculation`` tool so a model emitting a Calculation tool_call no
    longer hits ``ValueError("Tool 'Calculation' not found.")``.

    The runner builds the core toolset via ``build_cli_adk_tools``, which
    binds ``bind_core_toolhost_handlers`` (Calculation is registered there
    even with ``include_local_full_handlers=False``); the profile filter is
    the only thing that was dropping it.
    """
    import magi_agent.cli.tool_runtime as tool_runtime

    # Guard: the readonly profile must NOT pull in the writable local handlers
    # (preserves the no-write invariant). If it does, the test itself fails.
    def _fail_full_handler_bind(*args: object, **kwargs: object) -> None:
        raise AssertionError("full local handlers must not be bound for readonly")

    monkeypatch.setattr(
        tool_runtime,
        "bind_cli_local_full_tool_handlers",
        _fail_full_handler_bind,
    )

    runner = RealLocalChildRunner(
        toolset_profile="readonly",
        workspace_root=str(tmp_path),
    )

    tools, collector = runner._resolve_turn_toolset("child-session-calc")

    forwarded_names = {_tool_name(t) for t in tools}
    # The tool reaches the child's ADK runtime under the exact name the model
    # would emit in a tool_call.
    assert "Calculation" in forwarded_names, (
        f"readonly toolset forwarded {sorted(forwarded_names)!r}; "
        "Calculation missing → 'Tool not found' regression."
    )
    # All pre-existing inspection tools survive (regression guard).
    assert {"FileRead", "Glob", "Grep", "GitDiff"} <= forwarded_names
    # Mutating tools must STILL be absent under the readonly profile.
    assert "FileWrite" not in forwarded_names
    assert "FileEdit" not in forwarded_names
    assert "PatchApply" not in forwarded_names
    assert "Bash" not in forwarded_names
    # PythonExec is NOT exposed by Shape A — broader code-execution surface
    # remains an explicit follow-up.
    assert "PythonExec" not in forwarded_names
    # Evidence collector is still constructed for tool-enabled profiles.
    assert collector is not None


# --------------------------------------------------------------------------- #
# 3. Profile semantics preserved for ``none`` and ``full``                     #
# --------------------------------------------------------------------------- #


def test_none_profile_still_returns_empty_toolset(tmp_path: Path) -> None:
    """``none`` (the historical default) is unchanged: empty toolset, no
    evidence collector — byte-identical to v1 text-only child."""
    runner = RealLocalChildRunner(
        toolset_profile="none",
        workspace_root=str(tmp_path),
    )
    tools, collector = runner._resolve_turn_toolset("child-session-none")
    assert tools == []
    assert collector is None


def test_full_profile_unchanged_returns_no_name_filter() -> None:
    """``full`` profile's allowlist sentinel is still ``None`` (no name filter)
    so adding Calculation under readonly does not narrow ``full``."""
    assert toolset_allowlist("full") is None


def test_existing_explicit_tools_override_unchanged(tmp_path: Path) -> None:
    """Callers that inject ``tools=`` verbatim are untouched by the profile
    expansion: the override path returns the injected list as-is."""

    class _Tool:
        def __init__(self, name: str) -> None:
            self.name = name

    explicit = [_Tool("OnlyThisOne")]
    runner = RealLocalChildRunner(
        toolset_profile="readonly",
        workspace_root=str(tmp_path),
        tools=explicit,
    )
    tools, _collector = runner._resolve_turn_toolset("child-session-override")
    assert [_tool_name(t) for t in tools] == ["OnlyThisOne"]


# --------------------------------------------------------------------------- #
# 4. Honesty / non-regression: orchestrator readonly profile still narrow      #
# --------------------------------------------------------------------------- #


def test_orchestrator_main_profile_inherits_calculation() -> None:
    """``orchestrator_tool_names`` is the union of the readonly allowlist and
    SpawnAgent + ListCredentials. The Calculation addition flows through, so
    the orchestrator main agent can answer pure arithmetic without a spawn."""
    from magi_agent.runtime.main_agent_profile import orchestrator_tool_names

    names = orchestrator_tool_names()
    assert "Calculation" in names
    # SpawnAgent is still present (regression guard for the orchestrator
    # contract that all non-readonly work flows through a child).
    assert "SpawnAgent" in names
    # Mutation tools never appear in the orchestrator profile.
    for name in ("FileWrite", "Bash", "PatchApply", "PythonExec"):
        assert name not in names


# --------------------------------------------------------------------------- #
# 5. Sanity: Calculation dispatch is pure and produces ``{"value": 2}``        #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("1+1", 2),
        ("2*3", 6),
        ("(4+1)/5", 1.0),
    ],
)
def test_calculation_handler_is_pure_and_deterministic(expression: str, expected) -> None:
    """The Calculation handler used by the bound core-toolhost is a pure
    AST evaluator (no fs/net side effects). Documented here so a future
    refactor that adds I/O to Calculation must explicitly update the
    security note in this PR's readonly justification."""
    from magi_agent.gates.gate1a_readonly_tools import _evaluate_expression

    assert _evaluate_expression(expression) == expected
