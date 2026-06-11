from magi_agent.adk_bridge.control_plane import ToolDecision
from magi_agent.packs.context import (
    BeforeToolCtx, AfterToolCtx, BeforeModelCtx, AfterAgentCtx,
    SessionReadView, EvidenceReadView,
)


def _sv():
    return SessionReadView(invocation_id="i", agent_name="a", turn_index=0, state={})


def test_before_tool_ctx_args_read_only_and_decide_allow_default():
    ctx = BeforeToolCtx(tool_name="FileEdit", tool_args={"path": "a.py"},
                        session=_sv(), evidence=EvidenceReadView())
    # read-only view of args
    assert ctx.tool_args["path"] == "a.py"
    assert ctx.tool_name == "FileEdit"
    # default decision (no call) is allow
    assert ctx.decision() == ToolDecision(action="allow")


def test_before_tool_ctx_deny_and_rewrite_produce_tool_decision():
    ctx = BeforeToolCtx(tool_name="Bash", tool_args={"cmd": "rm -rf /"},
                        session=_sv(), evidence=EvidenceReadView())
    ctx.decide("deny", reason="dangerous", deny_result={"error": "blocked"})
    d = ctx.decision()
    assert d.action == "deny"
    assert d.deny_result == {"error": "blocked"}

    ctx2 = BeforeToolCtx(tool_name="Bash", tool_args={"cmd": "ls"},
                         session=_sv(), evidence=EvidenceReadView())
    ctx2.decide("rewrite", updated_args={"cmd": "ls -la"})
    assert ctx2.decision().updated_args == {"cmd": "ls -la"}


def test_after_tool_ctx_override_is_first_non_none_semantics_input():
    ctx = AfterToolCtx(tool_name="Bash", tool_args={}, result={"ok": True}, session=_sv())
    assert ctx.override_result() is None  # no override by default
    ctx.override({"ok": False, "patched": True})
    assert ctx.override_result() == {"ok": False, "patched": True}


def test_before_model_ctx_collects_reinjects_and_clear_tools():
    ctx = BeforeModelCtx(session=_sv())
    assert ctx.pending_reinjections() == ()
    assert ctx.wants_clear_tools() is False
    ctx.reinject(role="user", text="wrap up now")
    ctx.clear_tools()
    assert ctx.pending_reinjections() == (("user", "wrap up now"),)
    assert ctx.wants_clear_tools() is True


def test_after_agent_ctx_is_observe_only():
    ctx = AfterAgentCtx(agent_name="root", session=_sv())
    assert ctx.agent_name == "root"
    # no decide/override/reinject surface
    assert not hasattr(ctx, "decide")
    assert not hasattr(ctx, "override")
