from magi_agent.packs.context import PrimitiveType, Capability


def test_eight_provides_types_present():
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
    }


def test_capabilities_are_frozenset_tokens():
    # Capabilities are opaque string tokens; full-trust local does not gate on them,
    # but contexts reserve a frozenset slot so a hosted build can later restrict.
    assert Capability.READ_SESSION in Capability.all_tokens()
    assert isinstance(Capability.all_tokens(), frozenset)
    assert Capability.DECIDE_TOOL in Capability.all_tokens()
