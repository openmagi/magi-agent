from magi_agent.packs.context import PrimitiveType, Capability


def test_provides_types_present():
    # The unified D2 types (8 original + `role`, the declarative scope-label
    # extension) + the 3 Pack-C policy types (loop/schedule/memory).
    values = {t.value for t in PrimitiveType}
    assert values == {
        "tool",
        "callback",
        "validator",
        "harness",
        "control_plane",
        "evidence_producer",
        "recipe",
        "connector",
        "role",
        "loop_policy",
        "schedule_policy",
        "memory_strategy",
    }


def test_capabilities_are_frozenset_tokens():
    # Capabilities are opaque string tokens; full-trust local does not gate on them,
    # but contexts reserve a frozenset slot so a hosted build can later restrict.
    assert Capability.READ_SESSION in Capability.all_tokens()
    assert isinstance(Capability.all_tokens(), frozenset)
    assert Capability.DECIDE_TOOL in Capability.all_tokens()
