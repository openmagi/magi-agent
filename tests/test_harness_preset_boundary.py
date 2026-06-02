from magi_agent.harness.resolved import build_default_resolved_harness_state
from magi_agent.runtime.turn_controller import TurnControllerInput


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
    assert state.effective_harness_packs == ("coding", "research", "verification", "hard-safety")
    assert state.effective_hooks == ()
    assert state.skipped_by_scope == ()


def test_turn_controller_input_preserves_resolved_harness_state_boundary() -> None:
    state = build_default_resolved_harness_state()
    request = TurnControllerInput(
        user_id="user-1",
        session_id="agent:main:app:default",
        turn_id="turn-1",
        message_text="hello",
        harness_state=state,
    )

    assert request.harness_state is state
    assert request.harness_state.coding.components["tools"] == ("FileRead", "FileEdit", "PatchApply")
