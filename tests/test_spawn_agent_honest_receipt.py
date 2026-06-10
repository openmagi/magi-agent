"""Honest-receipt tests for spawn_agent / spawn_worktree_apply (07-PR2, D4).

The original implementation returned receipts that *looked* successful even
though nothing happened:

- gate-OFF ``spawn_agent`` returned ``status="queued_locally"`` — implies the
  task was accepted into a queue, but no child runner is attached or scheduled.
- ``spawn_worktree_apply`` always returned ``status="review_required"`` — implies
  a patch is staged awaiting review, but no worktree mutation is attempted.

This PR makes those receipts *honest*:

- gate-OFF ``spawn_agent`` → ``status="not_attached"`` +
  ``reason="live_child_runner_disabled"`` + an activation hint, while preserving
  every pre-existing key (``persona``/``promptDigest``/``spawnDepth``/
  ``liveChildRunnerAttached``).
- ``spawn_worktree_apply`` → ``status="unimplemented"`` + a ``reason``, while
  preserving ``patchDigest``/``worktreeMutationAttached``.

No success-implying literal must remain on the not-attached / not-implemented
paths.
"""
from __future__ import annotations

from magi_agent.tools.context import ToolContext


def _context(**overrides: object) -> ToolContext:
    defaults: dict[str, object] = {
        "botId": "test-bot",
        "sessionId": "sess-1",
        "turnId": "turn-1",
        "spawnDepth": 0,
    }
    defaults.update(overrides)
    return ToolContext(**defaults)


# ---------------------------------------------------------------------------
# spawn_agent — gate OFF must be honest (not a fake "queued" success)
# ---------------------------------------------------------------------------


def test_spawn_agent_gate_off_status_is_honest_not_attached(monkeypatch) -> None:
    monkeypatch.delenv("MAGI_CHILD_RUNNER_LIVE_ENABLED", raising=False)
    monkeypatch.delenv("MAGI_CHILD_RUNNER_LIVE_KILL_SWITCH", raising=False)

    from magi_agent.plugins.native.subagents import spawn_agent

    result = spawn_agent({"prompt": "do it", "persona": "researcher"}, _context())

    assert result.status == "ok"
    output = result.output

    # Honest status — NOT a success-implying "queued_locally".
    assert output["status"] == "not_attached"
    assert output["status"] != "queued_locally"

    # Explicit machine-readable reason + human activation hint.
    assert output["reason"] == "live_child_runner_disabled"
    assert "MAGI_CHILD_RUNNER_LIVE_ENABLED" in str(output.get("hint", ""))

    # Honesty about attachment is preserved.
    assert output["liveChildRunnerAttached"] is False


def test_spawn_agent_gate_off_preserves_legacy_keys(monkeypatch) -> None:
    monkeypatch.delenv("MAGI_CHILD_RUNNER_LIVE_ENABLED", raising=False)
    monkeypatch.delenv("MAGI_CHILD_RUNNER_LIVE_KILL_SWITCH", raising=False)

    from magi_agent.plugins.native._common import digest
    from magi_agent.plugins.native.subagents import spawn_agent

    result = spawn_agent({"prompt": "Hello world", "persona": "tester"}, _context(spawnDepth=2))
    output = result.output

    # Every pre-existing key/value is preserved (only the status *literal* changed
    # and reason/hint were added).
    assert output["persona"] == "tester"
    assert output["promptDigest"] == digest("Hello world")
    assert output["spawnDepth"] == 2
    assert output["liveChildRunnerAttached"] is False


def test_spawn_agent_kill_switch_also_honest(monkeypatch) -> None:
    """ENABLED=1 + KILL_SWITCH=1 routes through the gate-OFF branch → honest receipt."""
    monkeypatch.setenv("MAGI_CHILD_RUNNER_LIVE_ENABLED", "1")
    monkeypatch.setenv("MAGI_CHILD_RUNNER_LIVE_KILL_SWITCH", "1")

    from magi_agent.plugins.native.subagents import spawn_agent

    result = spawn_agent({"prompt": "x", "persona": "tester"}, _context())
    output = result.output

    assert output["status"] == "not_attached"
    assert output["reason"] == "live_child_runner_disabled"
    assert output["liveChildRunnerAttached"] is False


# ---------------------------------------------------------------------------
# spawn_worktree_apply — must be honest (not a fake "review_required" success)
# ---------------------------------------------------------------------------


def test_spawn_worktree_apply_status_is_honest_unimplemented() -> None:
    from magi_agent.plugins.native.subagents import spawn_worktree_apply

    result = spawn_worktree_apply({"patch": "diff --git a b"}, _context())

    assert result.status == "ok"
    output = result.output

    # Honest status — NOT a success-implying "review_required".
    assert output["status"] == "unimplemented"
    assert output["status"] != "review_required"

    # Explicit reason.
    assert output["reason"] == "worktree_apply_not_implemented"

    # Honesty about mutation is preserved.
    assert output["worktreeMutationAttached"] is False


def test_spawn_worktree_apply_preserves_patch_digest() -> None:
    from magi_agent.plugins.native._common import digest
    from magi_agent.plugins.native.subagents import spawn_worktree_apply

    result = spawn_worktree_apply({"patch": "PATCHBODY"}, _context())

    assert result.output["patchDigest"] == digest("PATCHBODY")
    assert result.output["worktreeMutationAttached"] is False
