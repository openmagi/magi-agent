"""NL -> multi-rule policy compiler (producer + gate + binding). ZERO network."""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Any

from magi_agent.customize.policy_compiler import compile_nl_to_policy
from magi_agent.customize.policy_plan import validate_policy_plan


class _FakePart:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeContent:
    def __init__(self, text: str) -> None:
        self.parts = [_FakePart(text)]


class _FakeLlmResponse:
    def __init__(self, text: str) -> None:
        self.content = _FakeContent(text)


def _factory(response_text: str):
    class _FakeModel:
        model = "fake-policy-compiler"

        async def generate_content_async(
            self, _req: Any, stream: bool = False
        ) -> AsyncGenerator:
            yield _FakeLlmResponse(response_text)

    return lambda: _FakeModel()


_PARAMS = {
    "intent": "require a credible source before running the trade tool",
    "gatedTool": "execute_trade",
    "fetchTool": "web_fetch",
    "allowlistDomains": ["sec.gov", "federalreserve.gov"],
    "evidenceLabel": "source credibility",
    "onUnavailable": "deny",
}


def _run(nl: str, response: str):
    return asyncio.run(
        compile_nl_to_policy(nl, model_factory=_factory(response))
    )


# --- happy path ---


def test_compiles_producer_gate_binding_plan() -> None:
    out = _run("require verified source before trading", json.dumps(_PARAMS))
    assert out["ok"] is True
    plan = out["plan"]
    # Producer: deterministic domain-allowlist emitting a custom: type.
    assert plan["producer"]["trigger"]["domainAllowlist"] == ["sec.gov", "federalreserve.gov"]
    assert plan["producer"]["emitsEvidenceType"] == "custom:SourceCredibility"
    # Gate: tool_perm on the gated tool with a requireEvidence bound to producer.
    payload = plan["gate"]["what"]["payload"]
    assert payload["match"]["tool"] == "execute_trade"
    assert payload["requireEvidence"]["producerRuleId"] == plan["producer"]["id"]
    assert payload["requireEvidence"]["evidenceType"] == "custom:SourceCredibility"
    assert payload["requireEvidence"]["onEvidenceUnavailable"] == "deny"
    # Binding links them by identity.
    assert plan["binding"]["producerRuleId"] == plan["producer"]["id"]
    assert plan["binding"]["gateRuleId"] == plan["gate"]["id"]
    # The emitted plan is structurally sound (no dangling/mismatch).
    assert validate_policy_plan(plan) == []
    assert "execute_trade" in out["explanation"]


def test_on_unavailable_ask_honored() -> None:
    out = _run("...", json.dumps({**_PARAMS, "onUnavailable": "ask"}))
    assert out["ok"] is True
    assert out["plan"]["gate"]["what"]["payload"]["requireEvidence"]["onEvidenceUnavailable"] == "ask"


def test_bad_on_unavailable_defaults_deny() -> None:
    out = _run("...", json.dumps({**_PARAMS, "onUnavailable": "explode"}))
    assert out["plan"]["gate"]["what"]["payload"]["requireEvidence"]["onEvidenceUnavailable"] == "deny"


def test_evidence_label_pascalized() -> None:
    out = _run("...", json.dumps({**_PARAMS, "evidenceLabel": "kyc check"}))
    assert out["plan"]["binding"]["evidenceType"] == "custom:KycCheck"


# --- clarifying / not-applicable / errors ---


def test_clarifying_questions() -> None:
    out = _run("verify stuff", json.dumps({"questions": ["Which tool?", "Which domains?"]}))
    assert out["ok"] is False
    assert out["confidenceLow"] is True
    assert out["clarifyingQuestions"] == ("Which tool?", "Which domains?")


def test_not_applicable() -> None:
    out = _run("block ssn in output", json.dumps({"notApplicable": True, "reason": "single check"}))
    assert out["ok"] is False
    assert out["notApplicable"] is True


def test_missing_gated_tool_errors() -> None:
    params = {k: v for k, v in _PARAMS.items() if k != "gatedTool"}
    out = _run("...", json.dumps(params))
    assert out["ok"] is False
    assert "gated tool" in out["error"]


def test_unparseable_output() -> None:
    out = _run("...", "not json at all")
    assert out["ok"] is False
    assert "unparseable" in out["error"]


def test_fail_open_no_model() -> None:
    out = asyncio.run(compile_nl_to_policy("x", model_factory=None))
    assert out["ok"] is False
    assert out["plan"] is None


def test_empty_allowlist_still_structurally_templated() -> None:
    # No domains -> the plan still templates (producer has an empty allowlist);
    # validate_policy_plan requires a domainAllowlist trigger to exist, which it
    # does (empty list is falsy -> flagged as non-deterministic).
    params = {**_PARAMS, "allowlistDomains": []}
    out = _run("...", json.dumps(params))
    # An empty allowlist is a non-deterministic producer per the structural
    # check, so the compiler surfaces it rather than shipping an unsafe plan.
    assert out["ok"] is False
    assert "deterministic" in out["error"] or "domainAllowlist" in out["error"]
