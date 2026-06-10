"""Track 17 PR3 — the live child path runs through runtime-issued acceptance.

The wired live child path (`_run_live_child`) sanitised its output but did NOT
issue a runtime envelope or run token-validated acceptance — so a child could
return a plausible result with no proof the runtime boundary (not the child)
authored the acceptance. This routes the live path through the SAME
`_runtime_child_acceptance_envelope` → `accept_real_child_envelope` flow the
ADK-shadow path (`_run_real_child`) already uses, as a GATE — while keeping the
sanitised summary so the child's usable work product still reaches the parent.
"""
from __future__ import annotations

import asyncio
from collections.abc import Mapping

from magi_agent.runtime.child_runner_boundary import (
    ChildRunnerConfig,
    ChildTaskRequest,
    LocalChildRunnerBoundary,
)


class _FakeLiveRunner:
    openmagi_live_provider = True

    def __init__(self, *, tools: list[object] | None = None, **kwargs: object) -> None:
        pass

    async def run_child(self, request: object) -> Mapping[str, object]:
        return {
            "childExecutionId": "child-exec-fake",
            "status": "completed",
            "summary": "Bear case: the soju market is a HiteJinro/Lotte duopoly.",
            "evidenceRefs": (),
            "artifactRefs": (),
            "auditEventRefs": (),
        }


def _request(**over: object) -> ChildTaskRequest:
    base: dict[str, object] = {
        "parentExecutionId": "parent-1",
        "turnId": "turn-1",
        "taskId": "task-1",
        "objective": "Write the bear case.",
    }
    base.update(over)
    return ChildTaskRequest(**base)


def _boundary(runner: object | None = None) -> LocalChildRunnerBoundary:
    return LocalChildRunnerBoundary(
        ChildRunnerConfig(enabled=True, liveChildRunnerEnabled=True),
        child_runner=runner if runner is not None else _FakeLiveRunner(),
        agents_spawned_so_far=0,
    )


def test_live_child_passes_through_runtime_acceptance():
    result = asyncio.run(_boundary().run(_request()))
    assert result.status == "ok"
    diag = result.diagnostic_metadata or {}
    # Acceptance actually ran and accepted (runtime-issued, token-validated).
    assert diag.get("childAcceptanceStatus") == "accepted"
    assert "childAcceptanceAcceptedEvidenceCount" in diag


def test_live_child_keeps_sanitised_summary_after_acceptance():
    result = asyncio.run(_boundary().run(_request()))
    assert result.envelope is not None
    # The child's usable work product still surfaces (content preserved).
    assert "Bear case" in str(result.envelope.summary)


def test_live_child_liveChildRunnerCalled_diagnostic_still_set():
    result = asyncio.run(_boundary().run(_request()))
    assert (result.diagnostic_metadata or {}).get("liveChildRunnerCalled") is True
