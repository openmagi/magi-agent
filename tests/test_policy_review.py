"""Policy review loop: deterministic integrity + advisory LLM verdict. ZERO network."""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Any

from magi_agent.customize.policy_review import review_policy_plan


# --- fake model (mirrors test_policy_compiler) -----------------------------


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
        model = "fake-policy-reviewer"

        async def generate_content_async(
            self, _req: Any, stream: bool = False
        ) -> AsyncGenerator:
            yield _FakeLlmResponse(response_text)

    return lambda: _FakeModel()


def _sound_plan(**over) -> dict:
    plan = {
        "intent": "require a credible source before running the trade tool",
        "producer": {
            "id": "source-credibility",
            "label": "records credibility",
            "scope": "always",
            "enabled": True,
            "trigger": {"tool": "web_fetch", "domainAllowlist": ["sec.gov"]},
            "action": "audit",
            "emitsEvidenceType": "custom:SourceCredibility",
        },
        "gate": {
            "id": "cr_source_credibility_execute_trade_gate",
            "scope": "always",
            "enabled": True,
            "what": {
                "kind": "tool_perm",
                "payload": {
                    "match": {"tool": "execute_trade"},
                    "decision": "deny",
                    "requireEvidence": {
                        "evidenceType": "custom:SourceCredibility",
                        "producerRuleId": "source-credibility",
                        "scope": "session",
                        "onEvidenceUnavailable": "deny",
                    },
                },
            },
            "firesAt": "before_tool_use",
            "action": "block",
        },
        "binding": {
            "producerRuleId": "source-credibility",
            "gateRuleId": "cr_source_credibility_execute_trade_gate",
            "evidenceType": "custom:SourceCredibility",
        },
    }
    plan.update(over)
    return plan


def _run(plan, response: str | None):
    factory = _factory(response) if response is not None else None
    return asyncio.run(review_policy_plan(plan, model_factory=factory))


# --- deterministic layer (always present) ----------------------------------


def test_structural_sound_plan_reports_no_findings() -> None:
    out = _run(_sound_plan(), json.dumps({"verdict": "aligned", "issues": [], "confidence": 0.9}))
    assert out["structural"] == []
    assert out["structurallySound"] is True


def test_structural_catches_unsound_plan_regardless_of_llm() -> None:
    # Break the identity binding: gate binds a producer the plan does not define.
    bad = _sound_plan()
    bad["gate"]["what"]["payload"]["requireEvidence"]["producerRuleId"] = "ghost"
    out = _run(bad, json.dumps({"verdict": "aligned", "issues": [], "confidence": 1.0}))
    assert out["structurallySound"] is False
    assert any("identity mismatch" in f for f in out["structural"])


# --- advisory layer (LLM verdict) ------------------------------------------


def test_advisory_verdict_parsed() -> None:
    out = _run(
        _sound_plan(),
        json.dumps(
            {
                "verdict": "partial",
                "issues": ["only sec.gov is trusted; the intent implied all regulators"],
                "confidence": 0.7,
                "coverage": "gates execute_trade on a verified sec.gov source",
            }
        ),
    )
    assert out["review"]["verdict"] == "partial"
    assert out["review"]["confidence"] == 0.7
    assert out["review"]["issues"]
    assert "execute_trade" in out["review"]["coverage"]


def test_advisory_never_blocks_a_sound_plan() -> None:
    # Even a "misaligned" advisory verdict leaves structurallySound True: the
    # verdict is guidance, not a gate.
    out = _run(_sound_plan(), json.dumps({"verdict": "misaligned", "issues": ["x"], "confidence": 0.4}))
    assert out["structurallySound"] is True
    assert out["review"]["verdict"] == "misaligned"


def test_no_model_yields_unknown_but_keeps_structural() -> None:
    out = _run(_sound_plan(), None)
    assert out["review"]["verdict"] == "unknown"
    assert out["review"]["confidence"] == 0.0
    assert out["structurallySound"] is True


def test_unparseable_verdict_degrades_to_unknown() -> None:
    out = _run(_sound_plan(), "not json")
    assert out["review"]["verdict"] == "unknown"
    assert out["structural"] == []  # deterministic layer unaffected


def test_out_of_vocab_verdict_rejected_to_unknown() -> None:
    out = _run(_sound_plan(), json.dumps({"verdict": "great", "confidence": 1.0}))
    assert out["review"]["verdict"] == "unknown"


def test_confidence_clamped() -> None:
    out = _run(_sound_plan(), json.dumps({"verdict": "aligned", "confidence": 5.0}))
    assert out["review"]["confidence"] == 1.0


def test_non_dict_plan_is_safe() -> None:
    out = _run("nope", json.dumps({"verdict": "aligned", "confidence": 1.0}))
    assert out["structurallySound"] is False
    assert out["review"]["verdict"] == "unknown"  # model not consulted for a non-dict plan
