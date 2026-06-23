"""E-17 — egress critic uses ADK structured output instead of prose parsing.

The pre-E-17 critic relied on the model emitting parseable JSON via prose
instruction (``Reply with ONLY a JSON object: {...}``) and ran
``_parse_critic_response`` over the streamed text. That works for
cooperative models but is fragile under prose drift and burns tokens
on the surrounding scaffolding.

E-17 ships a typed ``EgressCriticVerdict`` Pydantic schema (and its
mime declaration ``application/json``) on the ``GenerateContentConfig``.
Providers that honor ``response_schema`` (Anthropic / Gemini today)
return a typed object; providers that don't fall back to the
prose-parse path, which itself fails-open per the gate's contract.

Fail-open is preserved end-to-end: unparseable response → status=None
(no block). The structured-output half is independent of E-7's cache
marker; cache marker on the static system_instruction is a follow-up.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any

import pytest

from magi_agent.introspection.egress_gate import (
    EgressCriticVerdict,
    run_egress_critic_check,
)
from magi_agent.introspection.projection import (
    FileReadView,
    SessionEvidenceView,
    SessionScopeView,
    ToolCallView,
)


# ---------------------------------------------------------------------------
# Fake ADK helpers (mirror tests/test_introspection_egress_gate.py)
# ---------------------------------------------------------------------------


class _FakePart:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeContent:
    def __init__(self, text: str) -> None:
        self.parts = [_FakePart(text)]


class _FakeLlmResponse:
    def __init__(self, text: str) -> None:
        self.content = _FakeContent(text)


def _capturing_critic_llm(json_text: str, captured: list[Any]):
    def _factory() -> object:
        class _FakeLlm:
            model = "fake-critic-model"

            async def generate_content_async(
                self, llm_request: Any, stream: bool = False
            ) -> AsyncGenerator:
                captured.append(llm_request)
                yield _FakeLlmResponse(json_text)

        return _FakeLlm()

    return _factory


def _capturing_fact_critical_llm(captured: list[Any]):
    """A FactCritical classifier model that always returns True (so the
    critic gets called). Captures its own LlmRequest so we can
    differentiate the critic call from the classifier call."""

    def _factory() -> object:
        class _FakeLlm:
            model = "fake-fact-critical-model"

            async def generate_content_async(
                self, llm_request: Any, stream: bool = False
            ) -> AsyncGenerator:
                captured.append(llm_request)
                # FactCriticalClassifier expects ``{"fact_critical": <bool>, "reason": "..."}``.
                yield _FakeLlmResponse('{"fact_critical": true, "reason": "needs verification"}')

        return _FakeLlm()

    return _factory


def _build_view() -> SessionEvidenceView:
    """Non-empty view — the FactCriticalClassifier short-circuits to
    ``no_evidence_activity`` (not fact-critical) on empty evidence."""

    return SessionEvidenceView(
        scope=SessionScopeView(sessionId="s1", turnsCovered=("t1",)),
        filesRead=(
            FileReadView(
                path="report.md",
                sha256="sha256:" + "a" * 64,
                turnId="t1",
                bytes=10,
            ),
        ),
        toolCalls=(ToolCallView(name="FileRead", status="ok", turnId="t1"),),
    )


# ---------------------------------------------------------------------------
# Schema definition
# ---------------------------------------------------------------------------


def test_egress_critic_verdict_schema_shape() -> None:
    """The Pydantic schema declares the three fields and only those."""

    schema = EgressCriticVerdict.model_json_schema()
    assert set(schema.get("properties", {}).keys()) == {
        "grounded",
        "relevant",
        "reason",
    }
    # ``grounded`` and ``relevant`` are bool; ``reason`` is a string.
    assert schema["properties"]["grounded"]["type"] == "boolean"
    assert schema["properties"]["relevant"]["type"] == "boolean"
    assert schema["properties"]["reason"]["type"] == "string"


def test_egress_critic_verdict_round_trips_well_shaped_json() -> None:
    obj = EgressCriticVerdict.model_validate(
        {"grounded": True, "relevant": True, "reason": "fits the view"}
    )
    assert obj.grounded is True
    assert obj.relevant is True
    assert obj.reason == "fits the view"


# ---------------------------------------------------------------------------
# Wire shape: the request carries response_schema + json mime
# ---------------------------------------------------------------------------


def test_critic_request_carries_response_schema_and_mime() -> None:
    """A typed structured-output request must declare BOTH the schema and
    the JSON mime so providers that support response_schema return a typed
    payload (no prose post-processing)."""

    critic_captured: list[Any] = []
    fact_captured: list[Any] = []
    result = asyncio.run(
        run_egress_critic_check(
            draft_text="The temperature was 23C.",
            user_query="What was the temperature?",
            view=_build_view(),
            model_factory=_capturing_critic_llm(
                '{"grounded": true, "relevant": true, "reason": "ok"}',
                critic_captured,
            ),
            fact_critical_model_factory=_capturing_fact_critical_llm(fact_captured),
        )
    )

    # Critic must have been invoked (fact_critical classifier said True).
    assert len(critic_captured) == 1
    request = critic_captured[0]
    config = request.config
    assert getattr(config, "response_schema", None) is EgressCriticVerdict
    assert getattr(config, "response_mime_type", None) == "application/json"
    # Critic verdict is parsed (status=passed when grounded&relevant).
    assert result.status == "passed"
    assert result.critic_invoked is True


# ---------------------------------------------------------------------------
# Structured response → typed verdict
# ---------------------------------------------------------------------------


def test_structured_well_shaped_json_yields_passed_status() -> None:
    critic_captured: list[Any] = []
    fact_captured: list[Any] = []
    result = asyncio.run(
        run_egress_critic_check(
            draft_text="Citing file foo.py shows the bug.",
            user_query="What's the bug?",
            view=_build_view(),
            model_factory=_capturing_critic_llm(
                '{"grounded": true, "relevant": true, "reason": "foo.py read"}',
                critic_captured,
            ),
            fact_critical_model_factory=_capturing_fact_critical_llm(fact_captured),
        )
    )
    assert result.status == "passed"
    assert result.grounded is True
    assert result.relevant is True


def test_structured_ungrounded_yields_missing_evidence_status() -> None:
    critic_captured: list[Any] = []
    fact_captured: list[Any] = []
    result = asyncio.run(
        run_egress_critic_check(
            draft_text="Citing a file that was never read.",
            user_query="What did you find?",
            view=_build_view(),
            model_factory=_capturing_critic_llm(
                '{"grounded": false, "relevant": true, "reason": "unsupported claim"}',
                critic_captured,
            ),
            fact_critical_model_factory=_capturing_fact_critical_llm(fact_captured),
        )
    )
    assert result.status == "missing_evidence"  # soft signal, not a block
    assert result.grounded is False
    assert result.relevant is True


# ---------------------------------------------------------------------------
# Fail-open preserved when the model ignores response_schema
# ---------------------------------------------------------------------------


def test_unparseable_response_still_fails_open() -> None:
    """A provider that doesn't honor ``response_schema`` returns prose.
    The prose-parse fallback either succeeds (cooperative model) or
    fails-open with status=None — the gate never blocks on a critic
    error."""

    critic_captured: list[Any] = []
    fact_captured: list[Any] = []
    result = asyncio.run(
        run_egress_critic_check(
            draft_text="A claim.",
            user_query="Q",
            view=_build_view(),
            model_factory=_capturing_critic_llm(
                "this is not JSON at all",
                critic_captured,
            ),
            fact_critical_model_factory=_capturing_fact_critical_llm(fact_captured),
        )
    )
    assert result.status is None  # fail-open
    assert result.critic_invoked is True
    assert result.source == "critic_error"
