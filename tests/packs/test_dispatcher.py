import pytest

from magi_agent.packs.context import (
    PrimitiveType, ContextDispatcher, GatePositionViolation,
)
from magi_agent.packs.registries import PrimitiveRegistry


class _FakeLlmRequest:
    """Minimal stand-in for ADK LlmRequest (contents list + config.tools)."""
    class _Cfg:
        def __init__(self):
            self.tools = ["FileEdit", "Bash"]

    def __init__(self):
        self.contents = []
        self.config = self._Cfg()


class _FakeToolContext:
    invocation_id = "inv-9"
    agent_name = "root"

    def __init__(self):
        self.state = {"turn": 2}


def test_before_tool_first_deny_wins_and_returns_deny_result():
    reg = PrimitiveRegistry()

    def allow_impl(ctx):
        ctx.decide("allow")

    def deny_impl(ctx):
        ctx.decide("deny", reason="loop guard", deny_result={"error": "blocked"})

    reg.register("c:allow", allow_impl, ptype=PrimitiveType.CONTROL_PLANE,
                 priority=1, gate_position="before")
    reg.register("c:deny", deny_impl, ptype=PrimitiveType.CONTROL_PLANE,
                 priority=2, gate_position="before")
    d = ContextDispatcher(reg)
    args = {"path": "a.py"}
    out = d.dispatch_before_tool(tool_name="FileEdit", args=args,
                                 tool_context=_FakeToolContext())
    assert out == {"error": "blocked"}


def test_before_tool_rewrite_mutates_args_in_place_and_continues():
    reg = PrimitiveRegistry()

    def rewrite_impl(ctx):
        ctx.decide("rewrite", updated_args={"cmd": "ls -la"})

    reg.register("c:rw", rewrite_impl, ptype=PrimitiveType.CONTROL_PLANE,
                 gate_position="before")
    d = ContextDispatcher(reg)
    args = {"cmd": "ls"}
    out = d.dispatch_before_tool(tool_name="Bash", args=args,
                                 tool_context=_FakeToolContext())
    assert out is None
    assert args == {"cmd": "ls -la"}  # mutated in place


def test_gate_position_after_rejects_deciding_before_tool_impl():
    # default gate_position 'after' must NOT allow plugin-level deny (gate preserved)
    reg = PrimitiveRegistry()

    def deny_impl(ctx):
        ctx.decide("deny")

    reg.register("c:bad", deny_impl, ptype=PrimitiveType.CONTROL_PLANE)  # gate_position None -> after
    d = ContextDispatcher(reg)
    with pytest.raises(GatePositionViolation):
        d.dispatch_before_tool(tool_name="Bash", args={"cmd": "x"},
                               tool_context=_FakeToolContext())


def test_after_tool_first_non_none_override_wins():
    reg = PrimitiveRegistry()

    def noop_impl(ctx):
        return None

    def patch_impl(ctx):
        ctx.override({"ok": False})

    reg.register("c:noop", noop_impl, ptype=PrimitiveType.CONTROL_PLANE, priority=1)
    reg.register("c:patch", patch_impl, ptype=PrimitiveType.CONTROL_PLANE, priority=2)
    d = ContextDispatcher(reg)
    out = d.dispatch_after_tool(tool_name="Bash", args={}, result={"ok": True},
                                tool_context=_FakeToolContext())
    assert out == {"ok": False}


def test_before_model_reinject_appends_and_clear_tools_clears():
    reg = PrimitiveRegistry()

    def wrapup_impl(ctx):
        ctx.reinject(role="user", text="wrap up")
        ctx.clear_tools()

    reg.register("c:wrap", wrapup_impl, ptype=PrimitiveType.CONTROL_PLANE)
    d = ContextDispatcher(reg)
    req = _FakeLlmRequest()
    d.dispatch_before_model(callback_context=_FakeToolContext(), llm_request=req)
    assert req.contents == [{"role": "user", "content": "wrap up"}]
    assert req.config.tools == []
