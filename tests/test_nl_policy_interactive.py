"""Multi-turn conversational POLICY compiler. ZERO network."""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Any

from magi_agent.customize.nl_policy_interactive import step_policy_compile
from magi_agent.customize.policy_plan import validate_policy_plan


class _FakeLlmResponse:
    def __init__(self, text: str) -> None:
        self.content = type("C", (), {"parts": [type("P", (), {"text": text})()]})()


def _factory(response_text: str):
    class _FakeModel:
        model = "fake"

        async def generate_content_async(self, _req: Any, stream: bool = False) -> AsyncGenerator:
            yield _FakeLlmResponse(response_text)

    return lambda: _FakeModel()


def _step(*, history=None, params=None, answers=None, response=None, model=True):
    factory = _factory(response if response is not None else "{}") if model else None
    return asyncio.run(
        step_policy_compile(
            history=history,
            params_so_far=params,
            answers=answers,
            model_factory=factory,
        )
    )


# --- multi-turn convergence ---


def test_first_turn_missing_all_asks_questions() -> None:
    out = _step(
        history=[{"role": "user", "content": "gate a tool on a verified source"}],
        response=json.dumps({"assistant_message": "Which tool?", "param_updates": {}, "questions": ["Which tool?"]}),
    )
    assert out["ready_to_save"] is False
    assert out["plan"] is None
    assert set(out["missing_params"]) == {"gatedTool", "evidenceLabel", "allowlistDomains"}
    assert out["questions"]  # asked something


def test_answers_fill_and_converge_to_sound_plan() -> None:
    out = _step(
        params={},
        answers={
            "gatedTool": "execute_trade",
            "evidenceLabel": "source credibility",
            "allowlistDomains": "sec.gov, europa.eu",
        },
        response=json.dumps({"assistant_message": "Got it.", "param_updates": {}}),
    )
    assert out["ready_to_save"] is True
    assert out["missing_params"] == []
    plan = out["plan"]
    assert plan is not None
    assert plan["gate"]["what"]["payload"]["match"]["tool"] == "execute_trade"
    assert plan["producer"]["trigger"]["domainAllowlist"] == ["sec.gov", "europa.eu"]
    assert plan["binding"]["evidenceType"] == "custom:SourceCredibility"
    assert validate_policy_plan(plan) == []


def test_llm_param_updates_fill_missing() -> None:
    out = _step(
        history=[{"role": "user", "content": "gate execute_trade on sec.gov source credibility"}],
        params={},
        response=json.dumps({
            "assistant_message": "Assembling.",
            "param_updates": {
                "gatedTool": "execute_trade",
                "evidenceLabel": "source credibility",
                "allowlistDomains": ["sec.gov"],
            },
        }),
    )
    assert out["ready_to_save"] is True
    assert out["plan"]["binding"]["gateRuleId"] == out["plan"]["gate"]["id"]


def test_operator_answer_wins_over_llm() -> None:
    # Operator says execute_trade; the LLM tries to override with something else.
    out = _step(
        answers={"gatedTool": "execute_trade", "evidenceLabel": "kyc", "allowlistDomains": "sec.gov"},
        response=json.dumps({"param_updates": {"gatedTool": "other_tool"}}),
    )
    assert out["plan"]["gate"]["what"]["payload"]["match"]["tool"] == "execute_trade"


def test_carries_params_across_turns() -> None:
    # Turn 2: gatedTool carried in params_so_far, this turn supplies the rest.
    out = _step(
        params={"gatedTool": "execute_trade"},
        answers={"evidenceLabel": "source credibility", "allowlistDomains": "sec.gov"},
        response=json.dumps({"param_updates": {}}),
    )
    assert out["ready_to_save"] is True
    assert out["plan"]["gate"]["what"]["payload"]["match"]["tool"] == "execute_trade"


# --- not-ready / fail-open ---


def test_missing_domains_not_ready() -> None:
    out = _step(
        params={"gatedTool": "execute_trade", "evidenceLabel": "source credibility"},
        response=json.dumps({"param_updates": {}}),
    )
    assert out["ready_to_save"] is False
    assert "allowlistDomains" in out["missing_params"]
    assert out["plan"] is None


def test_fail_open_no_model_still_guides() -> None:
    out = _step(params={}, model=False)
    assert out["ready_to_save"] is False
    assert out["questions"]  # canonical questions guide the operator
    assert "can't reach" in out["assistant_message"].lower() or out["questions"]


def test_onunavailable_ask_threaded() -> None:
    out = _step(
        answers={
            "gatedTool": "execute_trade",
            "evidenceLabel": "src",
            "allowlistDomains": "sec.gov",
            "onUnavailable": "ask",
        },
        response=json.dumps({"param_updates": {}}),
    )
    assert out["plan"]["gate"]["what"]["payload"]["requireEvidence"]["onEvidenceUnavailable"] == "ask"
