"""E-17 — criterion engine uses ADK structured output (CriterionVerdict).

The pre-E-17 criterion judge formatted a prose ``Reply with ONLY a JSON
object: {"pass": <bool>, "reason": "..."}`` instruction and parsed the
streamed text with ``parse_verdict``. It also reused
``egress_gate._invoke_llm``, which hardcoded the GROUNDED/RELEVANT
critic's system_instruction — a latent contract mismatch (the prose
instruction said one thing while the system_instruction said another).

E-17 ships a typed ``CriterionVerdict`` Pydantic schema +
JSON mime declaration AND fixes the system_instruction mismatch by
threading a criterion-specific instruction through the (now-parametric)
``_invoke_llm``. Fail-open is preserved end-to-end.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any

import pytest

from magi_agent.customize.criterion_engine import (
    CriterionVerdict,
    _CRITERION_SYSTEM_INSTRUCTION,
    _default_invoke,
    evaluate_criterion,
)


# ---------------------------------------------------------------------------
# Fake ADK helpers
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


def _capturing_llm(json_text: str, captured: list[Any]):
    class _FakeLlm:
        model = "fake-judge-model"

        async def generate_content_async(
            self, llm_request: Any, stream: bool = False
        ) -> AsyncGenerator:
            captured.append(llm_request)
            yield _FakeLlmResponse(json_text)

    return _FakeLlm()


# ---------------------------------------------------------------------------
# Schema definition
# ---------------------------------------------------------------------------


def test_criterion_verdict_schema_shape() -> None:
    schema = CriterionVerdict.model_json_schema()
    assert set(schema.get("properties", {}).keys()) == {"pass_", "reason"} or set(
        schema.get("properties", {}).keys()
    ) == {"pass", "reason"}, schema.get("properties", {}).keys()


def test_criterion_verdict_round_trips_well_shaped_json() -> None:
    obj = CriterionVerdict.model_validate({"pass": True, "reason": "all cited"})
    assert obj.passed is True
    assert obj.reason == "all cited"


# ---------------------------------------------------------------------------
# Wire shape: the request carries response_schema + json mime
# ---------------------------------------------------------------------------


def test_default_invoke_wires_response_schema_and_mime() -> None:
    """``_default_invoke`` is the production path: it threads
    ``CriterionVerdict`` as response_schema and ``application/json`` as
    mime on the LlmRequest config so providers that support
    structured output return a typed payload."""

    captured: list[Any] = []
    model = _capturing_llm('{"pass": true, "reason": "ok"}', captured)
    result = asyncio.run(_default_invoke(model, "judge prompt"))
    assert len(captured) == 1
    config = captured[0].config
    assert getattr(config, "response_schema", None) is CriterionVerdict
    assert getattr(config, "response_mime_type", None) == "application/json"
    # The streamed text content is still returned for the prose-parse
    # fallback path (provider-agnostic).
    assert result == '{"pass": true, "reason": "ok"}'


def test_default_invoke_uses_criterion_system_instruction() -> None:
    """E-17 also fixes the latent system_instruction mismatch — the
    criterion judge passes its own pass/reason-shaped instruction, not
    the grounded/relevant one from the egress critic."""

    captured: list[Any] = []
    model = _capturing_llm('{"pass": true, "reason": "ok"}', captured)
    asyncio.run(_default_invoke(model, "judge prompt"))
    config = captured[0].config
    instruction = getattr(config, "system_instruction", "") or ""
    # Criterion judge talks about pass/reason, NOT grounded/relevant.
    assert '"pass"' in instruction or "pass" in instruction.lower()
    assert "grounded" not in instruction.lower(), (
        "Criterion judge must NOT carry the egress-critic system_instruction"
    )
    assert instruction == _CRITERION_SYSTEM_INSTRUCTION


# ---------------------------------------------------------------------------
# evaluate_criterion end-to-end with structured output
# ---------------------------------------------------------------------------


def test_evaluate_criterion_passes_with_structured_response() -> None:
    captured: list[Any] = []
    model = _capturing_llm('{"pass": true, "reason": "all claims cited"}', captured)

    async def _factory_invoke(m: Any, p: str) -> str:
        # Reuse the production _default_invoke to also exercise the
        # response_schema wiring end-to-end.
        return await _default_invoke(m, p)

    passed, reason = asyncio.run(
        evaluate_criterion(
            criterion="all claims cited",
            draft_text="...",
            model_factory=lambda: model,
            invoke=_factory_invoke,
        )
    )
    assert passed is True
    assert reason == "all claims cited"
    # And we did go through the structured-output seam.
    assert len(captured) == 1
    assert getattr(captured[0].config, "response_schema", None) is CriterionVerdict


def test_evaluate_criterion_fails_open_on_unparseable_response() -> None:
    captured: list[Any] = []
    model = _capturing_llm("this is not JSON at all", captured)

    async def _factory_invoke(m: Any, p: str) -> str:
        return await _default_invoke(m, p)

    passed, reason = asyncio.run(
        evaluate_criterion(
            criterion="all claims cited",
            draft_text="...",
            model_factory=lambda: model,
            invoke=_factory_invoke,
        )
    )
    assert passed is True
    assert "fail-open" in reason
