"""Tests for spawn_cap tool-name ceiling field (Seam 2a).

Coverage:
- T1: ChildTaskRequest constructed without spawn_cap → .spawn_cap is None.
- T2: spawn_agent with ToolContext.spawn_cap set → ChildTaskRequest.spawn_cap matches.
- T3: spawn_agent with ToolContext.spawn_cap=None → ChildTaskRequest.spawn_cap is None
      (proves byte-identical default path).
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import pytest

from magi_agent.tools.context import ToolContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _context(**overrides: object) -> ToolContext:
    """Minimal ToolContext for tests."""
    defaults: dict[str, object] = {
        "botId": "test-bot",
        "sessionId": "sess-1",
        "turnId": "turn-1",
        "spawnDepth": 0,
    }
    defaults.update(overrides)
    return ToolContext(**defaults)


# ---------------------------------------------------------------------------
# T1: ChildTaskRequest constructed without spawn_cap → .spawn_cap is None
# ---------------------------------------------------------------------------


def test_child_task_request_default_spawn_cap_is_none() -> None:
    """ChildTaskRequest without spawn_cap → .spawn_cap is None."""
    from magi_agent.runtime.child_runner_boundary import ChildTaskRequest

    req = ChildTaskRequest(
        parentExecutionId="exec-1",
        turnId="turn-1",
        taskId="task-1",
        objective="Do something",
    )
    assert req.spawn_cap is None


# ---------------------------------------------------------------------------
# T2: spawn_agent with ToolContext.spawn_cap set → ChildTaskRequest.spawn_cap matches
# ---------------------------------------------------------------------------


def test_spawn_agent_carries_spawn_cap_to_child_request(monkeypatch) -> None:
    """ToolContext.spawn_cap=("FileRead","Bash","WebSearch") flows into
    ChildTaskRequest.spawn_cap on the live path."""
    monkeypatch.setenv("MAGI_CHILD_RUNNER_LIVE_ENABLED", "1")
    monkeypatch.delenv("MAGI_CHILD_RUNNER_LIVE_KILL_SWITCH", raising=False)

    import magi_agent.runtime.child_runner_live as _live_mod

    captured_request: list[object] = []

    class _CapturingSpawnCapRunner:
        openmagi_live_provider = True

        def __init__(self, *, tools: list[object] | None = None, **kwargs: object) -> None:
            pass

        async def run_child(self, request: object) -> dict[str, object]:
            captured_request.append(request)
            return {
                "childExecutionId": "child-exec-spawn-cap",
                "status": "completed",
                "summary": "spawn_cap captured",
                "evidenceRefs": (),
                "artifactRefs": (),
                "auditEventRefs": (),
            }

    monkeypatch.setattr(_live_mod, "RealLocalChildRunner", _CapturingSpawnCapRunner)

    from magi_agent.plugins.native.subagents import spawn_agent

    ctx = _context(spawnDepth=0, spawnCap=("FileRead", "Bash", "WebSearch"))
    asyncio.run(spawn_agent({"prompt": "spawn-cap test"}, ctx))

    assert len(captured_request) == 1
    req = captured_request[0]
    assert req.spawn_cap == ("FileRead", "Bash", "WebSearch")


# ---------------------------------------------------------------------------
# T3: spawn_agent with ToolContext.spawn_cap=None → ChildTaskRequest.spawn_cap is None
# ---------------------------------------------------------------------------


def test_spawn_agent_none_spawn_cap_is_byte_identical(monkeypatch) -> None:
    """ToolContext.spawn_cap=None → ChildTaskRequest.spawn_cap is None (default path)."""
    monkeypatch.setenv("MAGI_CHILD_RUNNER_LIVE_ENABLED", "1")
    monkeypatch.delenv("MAGI_CHILD_RUNNER_LIVE_KILL_SWITCH", raising=False)

    import magi_agent.runtime.child_runner_live as _live_mod

    captured_request: list[object] = []

    class _CapturingNoneCapRunner:
        openmagi_live_provider = True

        def __init__(self, *, tools: list[object] | None = None, **kwargs: object) -> None:
            pass

        async def run_child(self, request: object) -> dict[str, object]:
            captured_request.append(request)
            return {
                "childExecutionId": "child-exec-none-cap",
                "status": "completed",
                "summary": "none cap captured",
                "evidenceRefs": (),
                "artifactRefs": (),
                "auditEventRefs": (),
            }

    monkeypatch.setattr(_live_mod, "RealLocalChildRunner", _CapturingNoneCapRunner)

    from magi_agent.plugins.native.subagents import spawn_agent

    # No spawnCap set — defaults to None
    ctx = _context(spawnDepth=0)
    asyncio.run(spawn_agent({"prompt": "none-cap test"}, ctx))

    assert len(captured_request) == 1
    req = captured_request[0]
    assert req.spawn_cap is None
