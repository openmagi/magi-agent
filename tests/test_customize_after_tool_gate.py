from __future__ import annotations

import asyncio

import pytest

from magi_agent.customize.after_tool_gate import (
    CUSTOMIZE_AFTER_TOOL_BLOCK_TYPE,
    CustomizeAfterToolControl,
)
from magi_agent.customize.verification_policy import CustomizeVerificationPolicy


class _Tool:
    def __init__(self, name: str) -> None:
        self.name = name


def _rule(*, enabled=True, **payload_over):
    payload = {"toolMatch": ["web_search"]}
    payload.update(payload_over)
    return {
        "id": "cr_1",
        "scope": "research",
        "enabled": enabled,
        "firesAt": "after_tool_use",
        "action": "override",
        "what": {"kind": "llm_criterion", "payload": payload},
    }


def _policy(*rules):
    return CustomizeVerificationPolicy(custom_rules=tuple(rules))


def _run(control, *, tool="web_search", result="some result text"):
    return asyncio.run(
        control.on_after_tool(
            tool=_Tool(tool), args={}, tool_context=None, result=result
        )
    )


@pytest.fixture(autouse=True)
def _flags_on(monkeypatch):
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")


def test_inert_when_flags_off(monkeypatch):
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "0")
    ctrl = CustomizeAfterToolControl(
        policy_loader=lambda: _policy(_rule(contentMatch={"pattern": "result"}))
    )
    assert _run(ctrl) is None


def test_content_match_substring_blocks():
    ctrl = CustomizeAfterToolControl(
        policy_loader=lambda: _policy(_rule(contentMatch={"pattern": "ssn"}))
    )
    out = _run(ctrl, result="leaked ssn 123-45-6789")
    assert out is not None
    assert out["response_type"] == CUSTOMIZE_AFTER_TOOL_BLOCK_TYPE
    assert out["rule_id"] == "cr_1"


def test_content_match_no_match_passes():
    ctrl = CustomizeAfterToolControl(
        policy_loader=lambda: _policy(_rule(contentMatch={"pattern": "ssn"}))
    )
    assert _run(ctrl, result="clean text") is None


def test_content_match_regex_and_negate():
    # negate=True: fire when the result does NOT contain a 10-K marker.
    ctrl = CustomizeAfterToolControl(
        policy_loader=lambda: _policy(
            _rule(contentMatch={"pattern": r"10-K", "isRegex": True, "negate": True})
        )
    )
    assert _run(ctrl, result="annual 10-K filing") is None
    assert _run(ctrl, result="press release") is not None


def test_tool_not_in_toolmatch_passes():
    ctrl = CustomizeAfterToolControl(
        policy_loader=lambda: _policy(_rule(contentMatch={"pattern": "x"}))
    )
    assert _run(ctrl, tool="bash", result="x") is None


def test_disabled_rule_inert():
    ctrl = CustomizeAfterToolControl(
        policy_loader=lambda: _policy(_rule(enabled=False, contentMatch={"pattern": "x"}))
    )
    assert _run(ctrl, result="x") is None


def test_llm_criterion_fail_blocks():
    async def fake_invoke(_model, _prompt):
        return '{"pass": false, "reason": "non-10K content"}'

    ctrl = CustomizeAfterToolControl(
        model_factory=lambda: object(),
        invoke=fake_invoke,
        policy_loader=lambda: _policy(_rule(criterion="only 10-K filings allowed")),
    )
    out = _run(ctrl, result="some filing")
    assert out is not None
    assert out["reason"] == "non-10K content"


def test_llm_criterion_pass_allows():
    async def fake_invoke(_model, _prompt):
        return '{"pass": true, "reason": "ok"}'

    ctrl = CustomizeAfterToolControl(
        model_factory=lambda: object(),
        invoke=fake_invoke,
        policy_loader=lambda: _policy(_rule(criterion="only 10-K filings allowed")),
    )
    assert _run(ctrl, result="10-K") is None


def test_llm_criterion_inert_without_model_factory():
    # criterion rule but no model factory (egress gate off) → inert, never blocks.
    called = {"n": 0}

    async def fake_invoke(_model, _prompt):
        called["n"] += 1
        return '{"pass": false, "reason": "x"}'

    ctrl = CustomizeAfterToolControl(
        model_factory=None,
        invoke=fake_invoke,
        policy_loader=lambda: _policy(_rule(criterion="block everything")),
    )
    assert _run(ctrl, result="anything") is None
    assert called["n"] == 0


def test_content_prefilter_gates_llm_call():
    # contentMatch + criterion: when the pre-filter does NOT match, the LLM is
    # never invoked (cost control) and the rule passes.
    called = {"n": 0}

    async def fake_invoke(_model, _prompt):
        called["n"] += 1
        return '{"pass": false, "reason": "x"}'

    ctrl = CustomizeAfterToolControl(
        model_factory=lambda: object(),
        invoke=fake_invoke,
        policy_loader=lambda: _policy(
            _rule(criterion="block non-10K", contentMatch={"pattern": "financial"})
        ),
    )
    assert _run(ctrl, result="unrelated text") is None
    assert called["n"] == 0
    # pre-filter matches → LLM runs → fail → block
    assert _run(ctrl, result="financial data") is not None
    assert called["n"] == 1


def test_fail_open_on_loader_error():
    def boom():
        raise RuntimeError("bad overrides")

    ctrl = CustomizeAfterToolControl(policy_loader=boom)
    assert _run(ctrl) is None


def test_dict_result_is_matched_serialized():
    ctrl = CustomizeAfterToolControl(
        policy_loader=lambda: _policy(_rule(contentMatch={"pattern": "secret"}))
    )
    assert _run(ctrl, result={"data": "a secret value"}) is not None
