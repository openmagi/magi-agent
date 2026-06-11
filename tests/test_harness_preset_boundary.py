from magi_agent.harness.resolved import build_default_resolved_harness_state


def test_default_resolved_harness_state_keeps_opinionated_packs_default_on() -> None:
    state = build_default_resolved_harness_state()

    assert state.profile_name == "openmagi-opinionated"
    assert state.coding.enabled is True
    assert state.research.enabled is True
    assert state.verification.enabled is True
    assert "sealed-file-policy" in state.hard_safety.protected_gates
    assert state.hard_safety.opt_out is False
    assert "childReview" in state.coding.opt_out_allowed
    assert "factGrounding" in state.research.opt_out_allowed
    assert state.effective_harness_packs == ("general", "coding", "research", "verification", "hard-safety")
    assert state.effective_hooks == ()
    assert state.skipped_by_scope == ()
