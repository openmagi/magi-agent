import pytest

from magi_agent.packs.context import PrimitiveType
from magi_agent.packs.registries import PrimitiveRegistry, ForbiddenRefError


def _impl(tag):
    def fn(ctx):  # impls take only their typed ctx
        return tag
    return fn


def test_register_resolve_list_basic():
    reg = PrimitiveRegistry()
    reg.register("builtin:echo", _impl("a"), ptype=PrimitiveType.TOOL)
    assert reg.resolve("builtin:echo", ptype=PrimitiveType.TOOL)("x") == "a"
    assert [e.ref for e in reg.list(ptype=PrimitiveType.TOOL)] == ["builtin:echo"]


def test_user_can_override_a_first_party_ref_via_same_path():
    reg = PrimitiveRegistry()
    reg.register("builtin:gate", _impl("fp"), ptype=PrimitiveType.CONTROL_PLANE,
                 origin="first_party")
    # user override uses the SAME register call (no privileged path)
    reg.register("builtin:gate", _impl("user"), ptype=PrimitiveType.CONTROL_PLANE,
                 origin="user", override=True)
    assert reg.resolve("builtin:gate", ptype=PrimitiveType.CONTROL_PLANE)("c") == "user"


def test_register_dup_without_override_raises():
    reg = PrimitiveRegistry()
    reg.register("r", _impl("a"), ptype=PrimitiveType.TOOL)
    with pytest.raises(ValueError):
        reg.register("r", _impl("b"), ptype=PrimitiveType.TOOL)


def test_user_can_forbid_a_first_party_ref():
    reg = PrimitiveRegistry()
    reg.register("builtin:perm_gate", _impl("fp"), ptype=PrimitiveType.CONTROL_PLANE,
                 origin="first_party")
    reg.forbid("builtin:perm_gate", ptype=PrimitiveType.CONTROL_PLANE)
    with pytest.raises(ForbiddenRefError):
        reg.resolve("builtin:perm_gate", ptype=PrimitiveType.CONTROL_PLANE)
    assert reg.list(ptype=PrimitiveType.CONTROL_PLANE) == []


def test_ordered_types_sort_by_priority_then_registration_order():
    reg = PrimitiveRegistry()
    reg.register("c:low", _impl("low"), ptype=PrimitiveType.CONTROL_PLANE, priority=10)
    reg.register("c:high", _impl("high"), ptype=PrimitiveType.CONTROL_PLANE, priority=1)
    reg.register("c:tie", _impl("tie"), ptype=PrimitiveType.CONTROL_PLANE, priority=1)
    ordered = [e.ref for e in reg.list(ptype=PrimitiveType.CONTROL_PLANE)]
    assert ordered == ["c:high", "c:tie", "c:low"]


def test_origin_is_metadata_only_no_privilege():
    # A first_party entry is NOT protected from override/forbid — no privilege (§1).
    reg = PrimitiveRegistry()
    reg.register("x", _impl("fp"), ptype=PrimitiveType.TOOL, origin="first_party")
    reg.register("x", _impl("u"), ptype=PrimitiveType.TOOL, origin="user", override=True)
    assert reg.resolve("x", ptype=PrimitiveType.TOOL)("c") == "u"
