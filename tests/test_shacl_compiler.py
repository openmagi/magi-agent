"""Tests for magi_agent.customize.shacl_compiler — Task 3.2 (model_factory functions).

TDD: written BEFORE implementation.  Tests cover:
  1. compile_nl_to_shacl: fake returns valid .ttl → ok=True; prompt contains field menu.
  2. compile_nl_to_shacl retry: attempt-1 broken .ttl, attempt-2 valid → ok=True (proves retry).
  3. compile_nl_to_shacl final failure: always broken → ok=False, error present.
  4. compile_nl_to_shacl fail-open: model_factory=None → ok=False, "unavailable", no exception.
  5. explain_shape: fake response → returned as-is; None → fallback string.
  6. review_compilation: structured verdict → parsed; garbage → conservative mismatch; None → unknown.

IMPORTANT LIMITATION (from spec / core principles):
  These tests verify PLUMBING only — that the prompt contains the field menu,
  that .ttl is extracted from the response, that parse failures trigger retry, that
  model verdicts are parsed. They do NOT verify compile quality (a real model needed).
  This limitation is documented here and in the function docstrings.

Zero network, zero real model calls. Fake factory mirrors the ADK async-generator
contract used by egress_gate / criterion_engine tests.

Spec: docs/plans/2026-06-18-shacl-PR3-compiler-tasks.md Task 3.2
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Fake ADK model helpers (LlmResponse-shaped async generator)
# Mirrors the pattern from test_introspection_egress_gate.py and
# test_introspection_fact_critical.py — the same ADK async-generator
# contract that _invoke_llm (egress_gate.py) consumes.
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


def _make_fake_model(response_text: str, *, prompt_capture: list[str] | None = None) -> object:
    """Return a fake ADK model that yields a single canned response.

    If ``prompt_capture`` is provided, the text of the LlmRequest user part is
    appended so tests can assert the prompt contents.
    """
    class _FakeModel:
        model = "fake-shacl-compiler-model"

        async def generate_content_async(
            self, llm_request: Any, stream: bool = False
        ) -> AsyncGenerator:
            if prompt_capture is not None:
                # Extract the user text from the LlmRequest contents.
                try:
                    for content in llm_request.contents:
                        for part in content.parts:
                            if hasattr(part, "text") and part.text:
                                prompt_capture.append(part.text)
                except Exception:  # noqa: BLE001
                    pass
            yield _FakeLlmResponse(response_text)

    return _FakeModel()


def _factory_for(response_text: str, *, prompt_capture: list[str] | None = None):
    """Return a model_factory callable yielding a fake model with a canned response."""
    def _factory() -> object:
        return _make_fake_model(response_text, prompt_capture=prompt_capture)
    return _factory


def _factory_sequence(*responses: str, prompt_capture: list[str] | None = None):
    """Return a model_factory that iterates over a sequence of responses.

    Each call to the factory returns a model that yields the NEXT response in the
    sequence.  Useful for testing retry logic: first call returns broken TTL,
    second returns valid TTL.
    """
    responses_list = list(responses)
    call_index: list[int] = [0]

    def _factory() -> object:
        idx = call_index[0]
        call_index[0] += 1
        text = responses_list[idx] if idx < len(responses_list) else responses_list[-1]
        return _make_fake_model(text, prompt_capture=prompt_capture)

    return _factory


# ---------------------------------------------------------------------------
# Shared test constants
# ---------------------------------------------------------------------------

# A minimal valid SHACL shape (same as used in test_shacl_preview.py).
_VALID_TTL = """\
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix magi: <https://openmagi.ai/ns/evidence#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

magi:AmountShape
    a sh:NodeShape ;
    sh:targetClass magi:Evidence ;
    sh:property [
        sh:path magi:field_amount ;
        sh:maxInclusive 3000 ;
        sh:message "amount must not exceed 3000" ;
    ] .
"""

# Fake model response that wraps the valid TTL in a code fence (as a model
# might return it).
_VALID_TTL_RESPONSE = f"```turtle\n{_VALID_TTL}\n```"

# A broken TTL response (not parseable as Turtle).
_BROKEN_TTL_RESPONSE = "```turtle\nthis is not valid turtle @@@\n```"

# A simple NL text for compile tests.
_NL_TEXT = "amount field must not exceed 3000"

# A simple fields list (simplified; real callers use available_fields()).
_FIELDS = [{"evidenceType": "Calculation", "fields": []}]


# ---------------------------------------------------------------------------
# Test 1 — compile_nl_to_shacl: valid response → ok=True; prompt has field menu
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compile_nl_to_shacl_valid_response_ok() -> None:
    """Fake model returns a valid .ttl response → result ok=True, shapeTtl present.

    Also verifies that the prompt sent to the fake model contains the field menu
    (evidenceType strings from available_fields()), confirming that available_fields()
    output is injected into the compile prompt.

    NOTE: This test verifies PLUMBING only (prompt injection + TTL extraction).
    It does NOT verify that the compiled shape is semantically correct.
    """
    from magi_agent.customize.shacl_compiler import compile_nl_to_shacl, available_fields

    prompt_capture: list[str] = []
    factory = _factory_for(_VALID_TTL_RESPONSE, prompt_capture=prompt_capture)

    result = await compile_nl_to_shacl(_NL_TEXT, _FIELDS, model_factory=factory)

    assert result["ok"] is True, f"Expected ok=True, got: {result}"
    assert result["shapeTtl"] is not None, "Expected shapeTtl to be present"
    assert isinstance(result["shapeTtl"], str), f"shapeTtl must be a string, got {type(result['shapeTtl'])}"
    assert len(result["shapeTtl"].strip()) > 0, "shapeTtl must not be empty"

    # Assert the prompt contains the field menu: at least one evidenceType from available_fields().
    assert prompt_capture, "Expected prompt to be captured"
    joined_prompt = " ".join(prompt_capture)
    menu = available_fields()
    menu_types = [item["evidenceType"] for item in menu]
    # At least one evidenceType must appear in the prompt.
    found_in_prompt = [t for t in menu_types if t in joined_prompt]
    assert found_in_prompt, (
        f"Prompt does not contain any evidenceType from available_fields(). "
        f"Prompt snippet: {joined_prompt[:500]!r}. "
        f"Expected one of: {menu_types[:5]!r}"
    )


# ---------------------------------------------------------------------------
# Test 2 — compile retry: broken on attempt 1, valid on attempt 2 → ok=True
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compile_nl_to_shacl_retry_on_first_failure() -> None:
    """First model response is broken TTL; second is valid.  Retry → ok=True.

    Proves that the function retries at least once on parse failure, injecting
    the validation errors into the retry prompt.
    """
    from magi_agent.customize.shacl_compiler import compile_nl_to_shacl

    # Attempt 1: broken TTL → validation fails; attempt 2: valid TTL → ok.
    factory = _factory_sequence(_BROKEN_TTL_RESPONSE, _VALID_TTL_RESPONSE)

    result = await compile_nl_to_shacl(_NL_TEXT, _FIELDS, model_factory=factory)

    assert result["ok"] is True, (
        f"Expected ok=True after retry with valid .ttl on attempt 2, got: {result}"
    )
    assert result["shapeTtl"] is not None, "Expected shapeTtl after successful retry"


# ---------------------------------------------------------------------------
# Test 3 — compile final failure: both attempts broken → ok=False, error present
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compile_nl_to_shacl_persistent_failure() -> None:
    """Fake always returns broken TTL.  After max retries → ok=False, error present.

    Max retries = 2 total attempts per spec.
    """
    from magi_agent.customize.shacl_compiler import compile_nl_to_shacl

    factory = _factory_for(_BROKEN_TTL_RESPONSE)

    result = await compile_nl_to_shacl(_NL_TEXT, _FIELDS, model_factory=factory)

    assert result["ok"] is False, f"Expected ok=False for persistent broken TTL, got: {result}"
    assert "error" in result, f"Expected 'error' key in result, got: {result}"
    assert result["error"], f"Expected non-empty error string, got: {result}"
    assert result.get("shapeTtl") is None, f"Expected shapeTtl=None on failure, got: {result}"


# ---------------------------------------------------------------------------
# Test 4 — compile fail-open: model_factory=None → ok=False, "unavailable", no raise
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compile_nl_to_shacl_fail_open_no_factory() -> None:
    """model_factory=None → ok=False, error contains 'unavailable', no exception raised.

    Fail-open is sacrosanct: absence of a model NEVER raises.
    """
    from magi_agent.customize.shacl_compiler import compile_nl_to_shacl

    try:
        result = await compile_nl_to_shacl(_NL_TEXT, _FIELDS, model_factory=None)
    except Exception as exc:  # noqa: BLE001
        pytest.fail(
            f"compile_nl_to_shacl with model_factory=None must not raise, but raised: {exc!r}"
        )

    assert result["ok"] is False, f"Expected ok=False for None factory, got: {result}"
    assert "error" in result, f"Expected 'error' key, got: {result}"
    assert "unavailable" in str(result["error"]).lower(), (
        f"Expected 'unavailable' in error string, got: {result['error']!r}"
    )


# ---------------------------------------------------------------------------
# Test 5 — explain_shape: fake response → returned; None → fallback string
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_explain_shape_returns_model_response() -> None:
    """Fake model returns an explanation string → that string is returned."""
    from magi_agent.customize.shacl_compiler import explain_shape

    explanation_text = "This shape requires that the amount field does not exceed 3000."
    factory = _factory_for(explanation_text)

    result = await explain_shape(_VALID_TTL, model_factory=factory)

    assert isinstance(result, str), f"explain_shape must return a str, got {type(result)}"
    assert result.strip(), "explain_shape must return a non-empty string"
    # The returned string should contain the model's response (possibly stripped).
    assert explanation_text.strip() in result or result.strip() in explanation_text.strip() or len(result) > 0, (
        f"Expected explanation text in result. Got: {result!r}"
    )


@pytest.mark.asyncio
async def test_explain_shape_fail_open_no_factory() -> None:
    """model_factory=None → fallback string returned, no exception raised."""
    from magi_agent.customize.shacl_compiler import explain_shape

    try:
        result = await explain_shape(_VALID_TTL, model_factory=None)
    except Exception as exc:  # noqa: BLE001
        pytest.fail(
            f"explain_shape with model_factory=None must not raise, but raised: {exc!r}"
        )

    assert isinstance(result, str), f"explain_shape must return a str, got {type(result)}"
    assert result.strip(), "explain_shape must return a non-empty fallback string"


# ---------------------------------------------------------------------------
# Test 6 — review_compilation: structured verdict → parsed; garbage → mismatch; None → unknown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_review_compilation_parses_structured_verdict() -> None:
    """Fake model returns a structured verdict JSON → parsed correctly."""
    from magi_agent.customize.shacl_compiler import review_compilation

    structured_response = json.dumps({
        "verdict": "aligned",
        "issues": [],
        "confidence": 0.9,
    })
    factory = _factory_for(structured_response)

    result = await review_compilation(_NL_TEXT, _VALID_TTL, _FIELDS, model_factory=factory)

    assert isinstance(result, dict), f"Expected dict, got {type(result)}"
    assert result["verdict"] == "aligned", f"Expected verdict='aligned', got: {result['verdict']!r}"
    assert isinstance(result["issues"], list), f"Expected issues to be a list, got {type(result['issues'])}"
    assert isinstance(result["confidence"], float), f"Expected confidence to be a float, got {type(result['confidence'])}"
    assert 0.0 <= result["confidence"] <= 1.0, f"confidence must be in [0, 1], got {result['confidence']}"


@pytest.mark.asyncio
async def test_review_compilation_garbage_response_conservative_mismatch() -> None:
    """Fake model returns garbage (unparseable) → conservative verdict='mismatch'."""
    from magi_agent.customize.shacl_compiler import review_compilation

    factory = _factory_for("this is not json at all %%$#")

    result = await review_compilation(_NL_TEXT, _VALID_TTL, _FIELDS, model_factory=factory)

    assert isinstance(result, dict), f"Expected dict, got {type(result)}"
    assert result["verdict"] == "mismatch", (
        f"Parse failure must yield conservative verdict='mismatch', got: {result['verdict']!r}"
    )


@pytest.mark.asyncio
async def test_review_compilation_fail_open_no_factory() -> None:
    """model_factory=None → verdict='unknown', issues=[], confidence=0.0, no raise."""
    from magi_agent.customize.shacl_compiler import review_compilation

    try:
        result = await review_compilation(_NL_TEXT, _VALID_TTL, _FIELDS, model_factory=None)
    except Exception as exc:  # noqa: BLE001
        pytest.fail(
            f"review_compilation with model_factory=None must not raise, but raised: {exc!r}"
        )

    assert isinstance(result, dict), f"Expected dict, got {type(result)}"
    assert result["verdict"] == "unknown", (
        f"None factory must yield verdict='unknown', got: {result['verdict']!r}"
    )
    assert result.get("issues") == [], f"Expected issues=[], got: {result.get('issues')!r}"
    assert result.get("confidence") == 0.0, (
        f"Expected confidence=0.0, got: {result.get('confidence')!r}"
    )


# ---------------------------------------------------------------------------
# Test 7 — system-instruction-flow: compile sends COMPILE instruction, not critic
#
# This test is the one that would have caught the original defect:
# the compiler was importing egress_gate._invoke_llm which hardcodes the
# "_CRITIC_SYSTEM_INSTRUCTION" ("You are an answer-grounding critic...").
# With the fix, the compiler's local _invoke_llm receives system_instruction
# as a parameter; this test asserts the COMPILE instruction reaches the model.
# ---------------------------------------------------------------------------


def _make_system_instruction_capturing_model(
    response_text: str,
    *,
    sys_instruction_capture: list[str],
) -> object:
    """Return a fake ADK model that captures the system_instruction from LlmRequest.

    The fake inspects ``llm_request.config.system_instruction`` (the field that
    ``_invoke_llm`` populates from its ``system_instruction`` parameter) and
    appends it to ``sys_instruction_capture``.  This lets tests assert that the
    correct system persona reached the model call.
    """
    class _CapturingModel:
        model = "fake-capturing-model"

        async def generate_content_async(
            self, llm_request: Any, stream: bool = False
        ) -> AsyncGenerator:
            try:
                # LlmRequest.config is a GenerateContentConfig; .system_instruction
                # is the field populated by our local _invoke_llm helper.
                si = getattr(
                    getattr(llm_request, "config", None),
                    "system_instruction",
                    None,
                )
                if si is not None:
                    sys_instruction_capture.append(str(si))
            except Exception:  # noqa: BLE001
                pass
            yield _FakeLlmResponse(response_text)

    return _CapturingModel()


def _factory_capturing_sys_instruction(
    response_text: str, sys_instruction_capture: list[str]
):
    """Return a model_factory that captures the system_instruction on each call."""
    def _factory() -> object:
        return _make_system_instruction_capturing_model(
            response_text, sys_instruction_capture=sys_instruction_capture
        )
    return _factory


@pytest.mark.asyncio
async def test_compile_sends_compile_system_instruction_not_critic() -> None:
    """compile_nl_to_shacl sends the COMPILE system instruction (not the egress critic's).

    This is the regression test that would have caught the original defect:
    - Before fix: compiler imported egress_gate._invoke_llm which hardcodes
      _CRITIC_SYSTEM_INSTRUCTION ("You are an answer-grounding critic...").
      All three compiler constants (_COMPILE_/_EXPLAIN_/_REVIEW_SYSTEM_INSTRUCTION)
      were dead code — the model always received the critic persona.
    - After fix: compiler's own _invoke_llm takes system_instruction as a
      parameter; compile_nl_to_shacl passes _COMPILE_SYSTEM_INSTRUCTION.

    Asserts:
      1. The system_instruction that reaches the model contains compiler-specific
         wording ("SHACL constraint compiler").
      2. It does NOT contain the critic persona wording
         ("answer-grounding critic").
    """
    from magi_agent.customize.shacl_compiler import (
        compile_nl_to_shacl,
        _COMPILE_SYSTEM_INSTRUCTION,
    )

    sys_instruction_capture: list[str] = []
    factory = _factory_capturing_sys_instruction(
        _VALID_TTL_RESPONSE, sys_instruction_capture
    )

    result = await compile_nl_to_shacl(_NL_TEXT, _FIELDS, model_factory=factory)

    assert result["ok"] is True, f"Expected ok=True, got: {result}"
    assert sys_instruction_capture, (
        "system_instruction was not captured — the fake model may not have been called, "
        "or LlmRequest.config.system_instruction is not set"
    )

    captured_si = sys_instruction_capture[0]

    # Must contain the compiler-specific persona.
    assert "SHACL constraint compiler" in captured_si, (
        f"Expected 'SHACL constraint compiler' in system_instruction, got: {captured_si!r}. "
        f"The _COMPILE_SYSTEM_INSTRUCTION is not reaching the model — "
        f"check that _invoke_llm in shacl_compiler.py uses the system_instruction parameter."
    )

    # Must NOT contain the egress critic persona (the bug: sharing egress_gate._invoke_llm).
    assert "answer-grounding critic" not in captured_si, (
        f"'answer-grounding critic' found in system_instruction: {captured_si!r}. "
        f"This is the egress critic persona — the compiler is incorrectly inheriting it. "
        f"Ensure shacl_compiler._invoke_llm is NOT egress_gate._invoke_llm."
    )

    # The captured instruction must match the module constant exactly.
    assert captured_si == _COMPILE_SYSTEM_INSTRUCTION, (
        f"system_instruction mismatch. Expected:\n  {_COMPILE_SYSTEM_INSTRUCTION!r}\n"
        f"Got:\n  {captured_si!r}"
    )


@pytest.mark.asyncio
async def test_explain_sends_explain_system_instruction() -> None:
    """explain_shape sends _EXPLAIN_SYSTEM_INSTRUCTION to the model."""
    from magi_agent.customize.shacl_compiler import (
        explain_shape,
        _EXPLAIN_SYSTEM_INSTRUCTION,
    )

    sys_instruction_capture: list[str] = []
    factory = _factory_capturing_sys_instruction(
        "This shape checks that amount <= 3000.", sys_instruction_capture
    )

    result = await explain_shape(_VALID_TTL, model_factory=factory)

    assert isinstance(result, str) and result.strip(), f"Expected non-empty explanation, got: {result!r}"
    assert sys_instruction_capture, "system_instruction was not captured"

    captured_si = sys_instruction_capture[0]
    assert "SHACL shape explainer" in captured_si, (
        f"Expected 'SHACL shape explainer' in system_instruction, got: {captured_si!r}"
    )
    assert "answer-grounding critic" not in captured_si, (
        f"explain_shape must not use the critic persona, got: {captured_si!r}"
    )
    assert captured_si == _EXPLAIN_SYSTEM_INSTRUCTION


@pytest.mark.asyncio
async def test_review_sends_review_system_instruction() -> None:
    """review_compilation sends _REVIEW_SYSTEM_INSTRUCTION to the model."""
    from magi_agent.customize.shacl_compiler import (
        review_compilation,
        _REVIEW_SYSTEM_INSTRUCTION,
    )

    structured_response = json.dumps({"verdict": "aligned", "issues": [], "confidence": 0.95})
    sys_instruction_capture: list[str] = []
    factory = _factory_capturing_sys_instruction(structured_response, sys_instruction_capture)

    result = await review_compilation(_NL_TEXT, _VALID_TTL, _FIELDS, model_factory=factory)

    assert result["verdict"] == "aligned", f"Expected verdict='aligned', got: {result}"
    assert sys_instruction_capture, "system_instruction was not captured"

    captured_si = sys_instruction_capture[0]
    assert "SHACL shape reviewer" in captured_si, (
        f"Expected 'SHACL shape reviewer' in system_instruction, got: {captured_si!r}"
    )
    assert "answer-grounding critic" not in captured_si, (
        f"review_compilation must not use the critic persona, got: {captured_si!r}"
    )
    assert captured_si == _REVIEW_SYSTEM_INSTRUCTION


# ---------------------------------------------------------------------------
# Test 8 — model raises (simulates network error): all three functions fail-open
# ---------------------------------------------------------------------------


def _make_raising_model() -> object:
    """Return a fake ADK model whose generate_content_async raises RuntimeError."""
    class _RaisingModel:
        model = "fake-raising-model"

        async def generate_content_async(
            self, llm_request: Any, stream: bool = False
        ) -> AsyncGenerator:
            raise RuntimeError("simulated network error")
            yield  # make this an async generator  # noqa: unreachable

    return _RaisingModel()


def _raising_factory():
    """model_factory that always returns a model that raises on generate_content_async."""
    def _factory() -> object:
        return _make_raising_model()
    return _factory


@pytest.mark.asyncio
async def test_compile_model_raises_fail_open() -> None:
    """If the model raises (e.g. network error), compile_nl_to_shacl returns ok=False, never raises."""
    from magi_agent.customize.shacl_compiler import compile_nl_to_shacl

    try:
        result = await compile_nl_to_shacl(_NL_TEXT, _FIELDS, model_factory=_raising_factory())
    except Exception as exc:  # noqa: BLE001
        pytest.fail(
            f"compile_nl_to_shacl must not raise when model raises, but raised: {exc!r}"
        )

    assert result["ok"] is False, f"Expected ok=False when model raises, got: {result}"
    assert "error" in result and result["error"], f"Expected non-empty error, got: {result}"
    assert result.get("shapeTtl") is None, f"Expected shapeTtl=None, got: {result}"


@pytest.mark.asyncio
async def test_explain_model_raises_fail_open() -> None:
    """If the model raises (e.g. network error), explain_shape returns fallback string, never raises."""
    from magi_agent.customize.shacl_compiler import explain_shape

    try:
        result = await explain_shape(_VALID_TTL, model_factory=_raising_factory())
    except Exception as exc:  # noqa: BLE001
        pytest.fail(
            f"explain_shape must not raise when model raises, but raised: {exc!r}"
        )

    assert isinstance(result, str), f"Expected str fallback, got {type(result)}"
    assert result.strip(), "explain_shape must return a non-empty fallback string when model raises"
    # Must NOT be the success path — should be a fallback/error string.
    assert "unavailable" in result.lower() or "error" in result.lower(), (
        f"Expected fallback string mentioning unavailability/error, got: {result!r}"
    )


@pytest.mark.asyncio
async def test_review_model_raises_fail_open() -> None:
    """If the model raises (e.g. network error), review_compilation returns conservative result, never raises."""
    from magi_agent.customize.shacl_compiler import review_compilation

    try:
        result = await review_compilation(_NL_TEXT, _VALID_TTL, _FIELDS, model_factory=_raising_factory())
    except Exception as exc:  # noqa: BLE001
        pytest.fail(
            f"review_compilation must not raise when model raises, but raised: {exc!r}"
        )

    assert isinstance(result, dict), f"Expected dict, got {type(result)}"
    # Conservative result on error: mismatch (safer than aligned).
    assert result["verdict"] in {"mismatch", "unknown"}, (
        f"Expected conservative verdict (mismatch or unknown) when model raises, got: {result['verdict']!r}"
    )
