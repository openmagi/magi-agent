"""S-B typed-context migration tests for SelfReviewAfterTurnControl.

The control must consume a pre-extracted ``TurnSnapshot`` and the public
``ForkRunner`` capability off a ``ControlPlaneContext`` (no privileged
``session.events`` traversal in the control body). It stays observational:
``apply_after_agent`` returns ``None`` and only *schedules* the C1 fork.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from datetime import UTC, datetime
from typing import Any

from magi_agent.adk_bridge.control_plane import SelfReviewAfterTurnControl
from magi_agent.harness.self_review import (
    REVIEW_DISABLED_TOOLSETS,
    ReviewCandidate,
    SelfReviewConfig,
)
from magi_agent.packs.context import ControlPlaneContext, TurnSnapshot
from magi_agent.runtime.fork_runner import ChildResult, ForkCacheShareEvidence

_NOW = datetime(2026, 6, 7, 12, 0, 0, tzinfo=UTC)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


class _RecordingForkRunner:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def fork(
        self,
        *,
        parent_turn_id: str,
        system_prompt_blocks: list[dict[str, Any]],
        parent_assistant_message: dict[str, Any],
        child_directives: list[str],
        disabled_toolsets: tuple[str, ...] = (),
    ) -> tuple[list[ChildResult], ForkCacheShareEvidence]:
        self.calls.append(
            {
                "parent_turn_id": parent_turn_id,
                "system_prompt_blocks": system_prompt_blocks,
                "parent_assistant_message": parent_assistant_message,
                "child_directives": child_directives,
                "disabled_toolsets": disabled_toolsets,
            }
        )
        return (
            [
                ChildResult(
                    directive=child_directives[0],
                    status="ok",
                    output='{"kind":"memory","proposal":"Remember context migration.","confidence":0.9}',
                )
            ],
            ForkCacheShareEvidence(
                parentTurnId=parent_turn_id,
                childCount=len(child_directives),
                sharedPrefixFingerprint="fake-fp",
                disabledToolsets=disabled_toolsets,
                status="ok",
                elapsedMs=0.1,
            ),
        )


class _FakeCandidateSink:
    def __init__(self) -> None:
        self.received: list[ReviewCandidate] = []

    def receive(self, candidate: ReviewCandidate) -> None:
        self.received.append(candidate)


def _snapshot() -> TurnSnapshot:
    return TurnSnapshot(
        session_id="sess-ctx",
        turn_id="turn-ctx",
        system_prompt_blocks=({"type": "text", "text": "System prompt for fork cache sharing."},),
        parent_assistant_message={
            "role": "assistant",
            "content": [{"type": "text", "text": "Task completed."}],
        },
    )


def test_self_review_uses_context_snapshot_and_fork_runner() -> None:
    """apply_after_agent reads ctx.turn_snapshot + ctx.fork_runner and schedules
    the fork with that runner — no session-tree traversal in the control body."""
    scheduled: list[Coroutine[Any, Any, None]] = []
    fork = _RecordingForkRunner()
    sink = _FakeCandidateSink()
    ctrl = SelfReviewAfterTurnControl(
        candidate_sink=sink,
        config=SelfReviewConfig(enabled=True, shadow=True),
        now=_NOW,
        scheduler=scheduled.append,
    )
    ctx = ControlPlaneContext.minimal(turn_snapshot=_snapshot(), fork_runner=fork)

    result = _run(ctrl.apply_after_agent(ctx))

    # Observational: returns None and only schedules (does not run inline).
    assert result is None
    assert fork.calls == []
    assert len(scheduled) == 1

    _run(scheduled[0])

    # The scheduled coroutine used the CONTEXT's fork_runner + snapshot fields.
    assert fork.calls[0]["parent_turn_id"] == "turn-ctx"
    assert fork.calls[0]["disabled_toolsets"] == REVIEW_DISABLED_TOOLSETS
    assert fork.calls[0]["system_prompt_blocks"] == [
        {"type": "text", "text": "System prompt for fork cache sharing."}
    ]
    assert fork.calls[0]["parent_assistant_message"] == {
        "role": "assistant",
        "content": [{"type": "text", "text": "Task completed."}],
    }
    assert len(sink.received) == 1
    assert sink.received[0].mode == "shadow"
    assert sink.received[0].acted is False


def test_self_review_prefers_context_fork_runner_over_constructor() -> None:
    """When the context supplies a fork_runner, it wins over the control's own
    (full-trust public capability semantics)."""
    scheduled: list[Coroutine[Any, Any, None]] = []
    ctor_fork = _RecordingForkRunner()
    ctx_fork = _RecordingForkRunner()
    ctrl = SelfReviewAfterTurnControl(
        fork_runner=ctor_fork,
        config=SelfReviewConfig(enabled=True, shadow=True),
        now=_NOW,
        scheduler=scheduled.append,
    )
    ctx = ControlPlaneContext.minimal(turn_snapshot=_snapshot(), fork_runner=ctx_fork)

    _run(ctrl.apply_after_agent(ctx))
    assert len(scheduled) == 1
    _run(scheduled[0])

    assert len(ctx_fork.calls) == 1
    assert ctor_fork.calls == []


def test_self_review_no_snapshot_is_noop() -> None:
    """No snapshot on the context -> no scheduling, no crash."""
    scheduled: list[Coroutine[Any, Any, None]] = []
    ctrl = SelfReviewAfterTurnControl(
        fork_runner=_RecordingForkRunner(),
        config=SelfReviewConfig(enabled=True, shadow=True),
        now=_NOW,
        scheduler=scheduled.append,
    )
    ctx = ControlPlaneContext.minimal(turn_snapshot=None)

    result = _run(ctrl.apply_after_agent(ctx))

    assert result is None
    assert scheduled == []
