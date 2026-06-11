"""Phase 5 S-0: the shared control-plane seam surface.

Adds the four seam capability surfaces (S-A evidence ledger view, S-B turn
snapshot + public fork runner, S-C per-invocation mutable state, S-D compaction
capability) onto a single ``ControlPlaneContext`` carrier so first-party and
third-party ``control_plane`` impls receive the IDENTICAL object (the §1 "no
privilege" keystone). No control is migrated here — only the surface is widened.
"""

from __future__ import annotations

from magi_agent.packs.context import (
    ControlPlaneContext,
    EvidenceLedgerView,
    PerInvocationState,
    TurnSnapshot,
)


def test_context_exposes_evidence_ledger_view_for_s_a():
    """S-A: a control reads the evidence ledger + open controls off the context,
    NOT a privileged receipt-store object."""
    view = EvidenceLedgerView(
        ledger=None,
        open_controls=(),
        contract_required=None,
        agent_role="general",
    )
    ctx = ControlPlaneContext.minimal(evidence=view)
    assert ctx.evidence is view
    assert ctx.evidence.agent_role == "general"


def test_context_exposes_turn_snapshot_and_fork_runner_for_s_b():
    """S-B: a pre-extracted typed snapshot + a public ForkRunner capability."""
    snap = TurnSnapshot(
        session_id="s1",
        turn_id="t1",
        system_prompt_blocks=({"type": "text", "text": "you are"},),
        parent_assistant_message={"role": "assistant", "content": []},
    )
    sentinel_fork = object()
    ctx = ControlPlaneContext.minimal(turn_snapshot=snap, fork_runner=sentinel_fork)
    assert ctx.turn_snapshot.turn_id == "t1"
    assert ctx.fork_runner is sentinel_fork


def test_context_exposes_per_invocation_state_for_s_c():
    """S-C: runtime-owned mutable per-invocation state with a clear hook + LRU bound."""
    state = PerInvocationState(max_scopes=2)
    state.set_scoped("inv-1", "FileEdit", 3)
    assert state.get_scoped("inv-1", "FileEdit", default=0) == 3
    state.clear_invocation("inv-1")
    assert state.get_scoped("inv-1", "FileEdit", default=0) == 0


def test_per_invocation_state_is_lru_bounded():
    state = PerInvocationState(max_scopes=2)
    state.set_scoped("a", "k", 1)
    state.set_scoped("b", "k", 1)
    state.set_scoped("c", "k", 1)  # evicts oldest ("a")
    assert state.get_scoped("a", "k", default=0) == 0
    assert state.get_scoped("c", "k", default=0) == 1


def test_per_invocation_state_objects_lru_bounded():
    state = PerInvocationState(max_scopes=2)
    state.get_object("a", "det", lambda: object())
    state.get_object("b", "det", lambda: object())
    state.get_object("c", "det", lambda: object())  # evicts oldest ("a")
    assert state.peek_object("a", "det") is None
    assert state.peek_object("c", "det") is not None


def test_per_invocation_state_object_factory_and_clear():
    state = PerInvocationState()
    obj = state.get_object("inv-1", "loop_detector", lambda: ["d"])
    assert obj == ["d"]
    # same scope/name returns the cached object (factory not re-run)
    assert state.get_object("inv-1", "loop_detector", lambda: ["other"]) is obj
    assert state.peek_object("inv-1", "loop_detector") is obj
    state.clear_invocation("inv-1")
    assert state.peek_object("inv-1", "loop_detector") is None


def test_per_invocation_pop_scoped():
    state = PerInvocationState()
    state.set_scoped("inv-1", "FileEdit", 2)
    state.pop_scoped("inv-1", "FileEdit")
    assert state.get_scoped("inv-1", "FileEdit", default=0) == 0
    # popping a missing key is a no-op
    state.pop_scoped("inv-1", "FileEdit")


def test_context_exposes_compaction_capability_for_s_d():
    """S-D: compaction decision narrowed behind one callable capability."""
    sentinel = object()
    ctx = ControlPlaneContext.minimal(compaction=sentinel)
    assert ctx.compaction is sentinel


def test_minimal_defaults_all_seams_to_none():
    ctx = ControlPlaneContext.minimal()
    assert ctx.evidence is None
    assert ctx.turn_snapshot is None
    assert ctx.fork_runner is None
    assert ctx.per_invocation is None
    assert ctx.compaction is None
