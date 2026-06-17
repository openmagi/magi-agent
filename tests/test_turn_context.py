from magi_agent.runtime.turn_context import TurnContext


def test_to_turn_input_carries_self_as_harness_state():
    ctx = TurnContext(prompt="go", session_id="s1", turn_id="t1")
    ti = ctx.to_turn_input()
    assert ti["prompt"] == "go" and ti["session_id"] == "s1" and ti["turn_id"] == "t1"
    assert ti["harness_state"] is ctx


def test_defaults_are_behavior_neutral():
    ctx = TurnContext(prompt="x", session_id="s", turn_id="t")
    assert ctx.memory_mode == "normal" and ctx.depth == 0 and ctx.permission_cap is None
