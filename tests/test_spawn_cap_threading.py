"""Tests for spawn_cap ceiling threading into RealLocalChildRunner (Seam 2b).

Coverage:
- T1: RealLocalChildRunner constructed with spawn_cap=("FileRead","Glob") →
      runner._spawn_cap == ("FileRead","Glob").
- T2: RealLocalChildRunner constructed without spawn_cap →
      runner._spawn_cap is None.
- T3: Boundary path — drive spawn_agent with ChildTaskRequest(spawn_cap=(...))
      and assert the constructed RealLocalChildRunner received that tuple via
      its constructor kwargs (monkeypatched capture, mirrors test_spawn_cap_capture.py).
- T4: Regression — with spawn_cap=None, _resolve_turn_toolset output is
      unchanged vs pre-change baseline (tool-name list equals expected set,
      guards the byte-identical claim).
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping

import pytest

from magi_agent.runtime.child_runner_live import RealLocalChildRunner


# ---------------------------------------------------------------------------
# T1: explicit spawn_cap stored on instance
# ---------------------------------------------------------------------------


def test_real_local_child_runner_stores_spawn_cap() -> None:
    """RealLocalChildRunner(..., spawn_cap=(...)) stores the tuple on _spawn_cap."""
    runner = RealLocalChildRunner(spawn_cap=("FileRead", "Glob"))
    assert runner._spawn_cap == ("FileRead", "Glob")


# ---------------------------------------------------------------------------
# T2: default spawn_cap=None stored on instance
# ---------------------------------------------------------------------------


def test_real_local_child_runner_default_spawn_cap_is_none() -> None:
    """RealLocalChildRunner() without spawn_cap → _spawn_cap is None."""
    runner = RealLocalChildRunner()
    assert runner._spawn_cap is None


# ---------------------------------------------------------------------------
# T3: Boundary path — spawn_agent carries spawn_cap into RealLocalChildRunner
# ---------------------------------------------------------------------------


def test_boundary_passes_spawn_cap_to_real_local_child_runner(monkeypatch) -> None:
    """spawn_agent with ToolContext.spawn_cap → RealLocalChildRunner receives it
    as spawn_cap kwarg at construction time."""
    monkeypatch.setenv("MAGI_CHILD_RUNNER_LIVE_ENABLED", "1")
    monkeypatch.delenv("MAGI_CHILD_RUNNER_LIVE_KILL_SWITCH", raising=False)

    import magi_agent.runtime.child_runner_live as _live_mod

    captured_init_kwargs: list[dict[str, object]] = []

    class _CapturingRunner:
        openmagi_live_provider = True

        def __init__(self, **kwargs: object) -> None:
            captured_init_kwargs.append(dict(kwargs))

        async def run_child(self, request: object) -> dict[str, object]:
            return {
                "childExecutionId": "child-exec-seam2b",
                "status": "completed",
                "summary": "seam 2b threading verified",
                "evidenceRefs": (),
                "artifactRefs": (),
                "auditEventRefs": (),
            }

    monkeypatch.setattr(_live_mod, "RealLocalChildRunner", _CapturingRunner)

    from magi_agent.tools.context import ToolContext
    from magi_agent.plugins.native.subagents import spawn_agent

    ctx = ToolContext(
        botId="test-bot",
        sessionId="sess-seam2b",
        turnId="turn-seam2b",
        spawnDepth=0,
        spawnCap=("FileRead", "Glob"),
    )
    asyncio.run(spawn_agent({"prompt": "seam 2b boundary test"}, ctx))

    assert len(captured_init_kwargs) == 1
    assert captured_init_kwargs[0].get("spawn_cap") == ("FileRead", "Glob")


# ---------------------------------------------------------------------------
# T4: Regression — spawn_cap=None leaves _resolve_turn_toolset output unchanged
# ---------------------------------------------------------------------------


def test_resolve_turn_toolset_unaffected_by_none_spawn_cap(
    monkeypatch, tmp_path
) -> None:
    """With spawn_cap=None (default), _resolve_turn_toolset returns the same
    tool set as before Seam 2b — guarding the byte-identical claim.

    Uses the ``readonly`` profile so a non-empty tool list is resolved;
    MAGI_SUBAGENT_TOOL_TIGHTEN_ONLY_ENABLED is forced OFF so only the
    profile allowlist applies (no intersection from Task 2B.3).
    """
    monkeypatch.delenv("MAGI_SUBAGENT_TOOL_TIGHTEN_ONLY_ENABLED", raising=False)

    # Prevent bind_cli_local_full_tool_handlers from being called (not needed
    # for readonly; mirrors the existing readonly test in test_child_runner_live).
    import magi_agent.cli.tool_runtime as tool_runtime  # noqa: PLC0415

    def _fail_full(*args: object, **kwargs: object) -> None:
        raise AssertionError("full local handlers should not be built for readonly")

    monkeypatch.setattr(tool_runtime, "bind_cli_local_full_tool_handlers", _fail_full)

    runner = RealLocalChildRunner(
        toolset_profile="readonly",
        workspace_root=str(tmp_path),
        spawn_cap=None,  # explicit None — must be byte-identical
    )
    tools, collector = runner._resolve_turn_toolset("child-session-t4")

    assert collector is not None
    assert {str(tool.name) for tool in tools} == {
        "FileRead",
        "Glob",
        "Grep",
        "GitDiff",
    }
