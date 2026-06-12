from google.adk.models.llm_request import LlmRequest

from magi_agent.adk_bridge.control_plane import ToolDecision
from magi_agent.packs.context import (
    PrimitiveType, ContextDispatcher, BeforeToolCtx,
)
from magi_agent.packs.registries import PrimitiveRegistry


class _DuckCtx:
    invocation_id = "inv-real"
    agent_name = "root"
    state = {"turn": 1}


def test_before_tool_ctx_emits_real_tool_decision_type():
    ctx = BeforeToolCtx(tool_name="Bash", tool_args={"cmd": "ls"},
                        session=__import__("magi_agent.packs.context",
                                           fromlist=["SessionReadView"]).SessionReadView(
                            invocation_id="i", agent_name="a", turn_index=0, state={}),
                        evidence=__import__("magi_agent.packs.context",
                                            fromlist=["EvidenceReadView"]).EvidenceReadView())
    ctx.decide("deny", reason="x")
    assert isinstance(ctx.decision(), ToolDecision)


def test_before_model_clears_real_llm_request_tools_and_reinjects():
    reg = PrimitiveRegistry()

    def wrapup(ctx):
        ctx.reinject(role="user", text="finish")
        ctx.clear_tools()

    reg.register("c:wrap", wrapup, ptype=PrimitiveType.CONTROL_PLANE)
    # Real ADK LlmRequest (pydantic). config defaults to a GenerateContentConfig.
    req = LlmRequest(model="local-dev")
    # ensure config + tools attribute exists in the ADK shape we mutate
    if req.config is None:
        from google.genai import types as genai_types
        req.config = genai_types.GenerateContentConfig()
    req.config.tools = ["placeholder"]
    d = ContextDispatcher(reg)
    d.dispatch_before_model(callback_context=_DuckCtx(), llm_request=req)
    assert req.config.tools == []
    assert req.contents and req.contents[-1] == {"role": "user", "content": "finish"}
