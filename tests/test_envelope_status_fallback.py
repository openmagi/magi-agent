"""PR-3 (Containment hardening): ``_envelope_from_output`` historically
silently coerced ANY unexpected ``status`` (None / "ok" / "running" / "") to
``"completed"``. That silent rewrite is exactly the shape Kevin chased
across 23 versions: the child runner crashes / returns a half-shaped mapping
without a ``status`` key, and the boundary then ships a clean-looking
``status="completed"`` envelope to the parent agent. The parent treats the
empty answer as authoritative and goes hunting through the filesystem for
"the answer that must be there somewhere".

Fix (this PR): unknown / absent statuses now default to ``"failed"`` so the
unexpected case surfaces as a real failure instead of a fake success. The
existing whitelist (``"completed" | "blocked" | "failed"``) is preserved so
correct child runners keep working byte-identical.
"""

from __future__ import annotations

from magi_agent.runtime.child_runner_boundary import (
    ChildTaskRequest,
    _envelope_from_output,
)

_MAX_REFS = 8


def _request() -> ChildTaskRequest:
    return ChildTaskRequest(
        parentExecutionId="parent-exec-envelope-fallback",
        turnId="turn-envelope-fallback",
        taskId="task-envelope-fallback",
        objective="Drive envelope coercion paths for the status-fallback test.",
        role="general",
        delivery="return",
    )


def test_envelope_unknown_status_coerces_to_failed() -> None:
    """``status="running"`` is NOT in the whitelist. Pre-PR-3 it was
    silently rewritten to ``"completed"``. Now it must surface as
    ``"failed"`` so the parent sees a real failure."""
    envelope = _envelope_from_output(
        _request(),
        {"status": "running", "summary": "partial answer"},
        max_refs=_MAX_REFS,
    )
    assert envelope.status == "failed", (
        "Unexpected child status must default to failed, not completed. "
        f"Got envelope.status={envelope.status!r}"
    )


def test_envelope_missing_status_coerces_to_failed() -> None:
    """A child runner that crashes mid-build may emit an output mapping with
    NO ``status`` key at all. Pre-PR-3 the absent key became ``"completed"``;
    now it must default to ``"failed"`` to expose the shape problem."""
    envelope = _envelope_from_output(_request(), {}, max_refs=_MAX_REFS)
    assert envelope.status == "failed", (
        "Missing child status must default to failed, not completed. "
        f"Got envelope.status={envelope.status!r}"
    )


def test_envelope_completed_status_passes_through() -> None:
    """Whitelist preserved: ``"completed"`` stays ``"completed"``."""
    envelope = _envelope_from_output(
        _request(),
        {"status": "completed", "summary": "answer"},
        max_refs=_MAX_REFS,
    )
    assert envelope.status == "completed"


def test_envelope_blocked_status_passes_through() -> None:
    """Whitelist preserved: ``"blocked"`` stays ``"blocked"``."""
    envelope = _envelope_from_output(
        _request(),
        {"status": "blocked", "summary": "blocked"},
        max_refs=_MAX_REFS,
    )
    assert envelope.status == "blocked"


def test_envelope_failed_status_passes_through() -> None:
    """Whitelist preserved: ``"failed"`` stays ``"failed"``."""
    envelope = _envelope_from_output(
        _request(),
        {"status": "failed", "summary": "child failure"},
        max_refs=_MAX_REFS,
    )
    assert envelope.status == "failed"
