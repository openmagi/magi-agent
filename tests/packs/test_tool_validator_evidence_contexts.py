from magi_agent.packs.context import (
    ToolCtx, ValidatorCtx, EvidenceProducerCtx, SessionReadView,
)


def _sv():
    return SessionReadView(invocation_id="i", agent_name="a", turn_index=0, state={})


def test_tool_ctx_exposes_args_and_progress_sink():
    seen: list[str] = []
    ctx = ToolCtx(tool_name="Echo", tool_args={"msg": "hi"}, session=_sv(),
                  emit_progress=seen.append)
    assert ctx.tool_args["msg"] == "hi"
    ctx.progress("step 1")
    assert seen == ["step 1"]


def test_tool_ctx_progress_is_noop_when_no_sink():
    ctx = ToolCtx(tool_name="Echo", tool_args={}, session=_sv())
    ctx.progress("ignored")  # must not raise


def test_validator_ctx_emit_records_verdict():
    ctx = ValidatorCtx(ref="builtin:python_syntax",
                       artifact={"path": "a.py", "content": "x ="}, session=_sv())
    ctx.emit(passed=False, detail="SyntaxError: invalid syntax")
    v = ctx.verdict()
    assert v.passed is False
    assert v.detail == "SyntaxError: invalid syntax"
    assert v.ref == "builtin:python_syntax"


def test_validator_ctx_default_verdict_is_unset():
    ctx = ValidatorCtx(ref="r", artifact={}, session=_sv())
    assert ctx.verdict() is None


def test_evidence_producer_ctx_collects_emitted_evidence():
    ctx = EvidenceProducerCtx(session=_sv())
    ctx.emit(evidence_type="test_run", payload={"passed": 3, "failed": 0})
    items = ctx.emitted()
    assert items == ({"evidence_type": "test_run", "payload": {"passed": 3, "failed": 0}},)
