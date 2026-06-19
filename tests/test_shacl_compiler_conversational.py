"""TDD tests for Task 5.1 — compile_nl_to_shacl conversational/clarifying-question extension.

Tests cover:
  1. First call + fake returns valid TTL → {ok:True, shapeTtl}. Regression of existing.
  2. First call + fake returns {"questions":[...]} → {ok:False, clarifyingQuestions, confidenceLow:True, shapeTtl:None}.
     fake call_count == 1 (no retry consumed).
  3. Follow-up call + prior_turns (2 turns) + fake returns valid TTL → {ok:True}.
     Assert prior_turns reached the model (captured in llm_request.contents).
  4. Single question → 1 element; 3+ questions → trimmed to 2.
  5. Broken JSON + broken TTL → existing retry path → final ok:False (no clarifyingQuestions key).
  6. fail-open: model_factory=None → {ok:False, error:"unavailable"}, NO clarifyingQuestions key, never raises.
  7. {"questions":[]} (empty) → NOT treated as clarifying-questions; falls through to failure path.

Spec: docs/plans/2026-06-19-shacl-conversational-compile-tasks.md Task 5.1
"""
from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Shared fake ADK model helpers (mirrored from test_shacl_compiler.py)
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


def _make_fake_model(
    response_text: str,
    *,
    prompt_capture: list[str] | None = None,
    contents_capture: list[Any] | None = None,
    call_counter: list[int] | None = None,
) -> object:
    """Return a fake ADK model that yields a single canned response.

    - ``prompt_capture``: appends each user-part text to the list.
    - ``contents_capture``: appends the entire llm_request.contents list.
    - ``call_counter``: increments [0] on each generate_content_async call.
    """
    class _FakeModel:
        model = "fake-shacl-compiler-model"

        async def generate_content_async(
            self, llm_request: Any, stream: bool = False
        ) -> AsyncGenerator:
            if call_counter is not None:
                call_counter[0] += 1
            if prompt_capture is not None:
                try:
                    for content in llm_request.contents:
                        for part in content.parts:
                            if hasattr(part, "text") and part.text:
                                prompt_capture.append(part.text)
                except Exception:  # noqa: BLE001
                    pass
            if contents_capture is not None:
                try:
                    contents_capture.append(list(llm_request.contents))
                except Exception:  # noqa: BLE001
                    pass
            yield _FakeLlmResponse(response_text)

    return _FakeModel()


def _factory_for(
    response_text: str,
    *,
    prompt_capture: list[str] | None = None,
    contents_capture: list[Any] | None = None,
    call_counter: list[int] | None = None,
):
    """Return a model_factory callable yielding a fake model with a canned response."""
    def _factory() -> object:
        return _make_fake_model(
            response_text,
            prompt_capture=prompt_capture,
            contents_capture=contents_capture,
            call_counter=call_counter,
        )
    return _factory


def _factory_sequence(*responses: str, call_counter: list[int] | None = None):
    """Return a model_factory that iterates over a sequence of responses."""
    responses_list = list(responses)
    call_index: list[int] = [0]

    def _factory() -> object:
        idx = call_index[0]
        call_index[0] += 1
        if call_counter is not None:
            call_counter[0] += 1
        text = responses_list[idx] if idx < len(responses_list) else responses_list[-1]
        return _make_fake_model(text)

    return _factory


# ---------------------------------------------------------------------------
# Shared test constants
# ---------------------------------------------------------------------------

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

_VALID_TTL_RESPONSE = f"```turtle\n{_VALID_TTL}\n```"
_BROKEN_TTL_RESPONSE = "```turtle\nthis is not valid turtle @@@\n```"
_NL_TEXT = "amount field must not exceed 3000"
_FIELDS: list[dict] = [{"evidenceType": "Calculation", "fields": []}]


# ---------------------------------------------------------------------------
# Test 1 — first call + valid TTL → existing behavior (ok=True). Regression.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_call_valid_ttl_returns_ok_true() -> None:
    """First call (prior_turns=()) + fake returns valid TTL → {ok:True, shapeTtl}."""
    from magi_agent.customize.shacl_compiler import compile_nl_to_shacl

    factory = _factory_for(_VALID_TTL_RESPONSE)
    result = await compile_nl_to_shacl(_NL_TEXT, _FIELDS, model_factory=factory)

    assert result["ok"] is True, f"Expected ok=True, got: {result}"
    assert result.get("shapeTtl") is not None, "Expected shapeTtl to be present"
    assert "clarifyingQuestions" not in result, (
        "clarifyingQuestions must NOT be present on a successful compile"
    )


# ---------------------------------------------------------------------------
# Test 2 — first call + fake returns questions JSON → clarifyingQuestions response.
# No retry consumed (call_count == 1).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_call_questions_response_returns_clarifying_questions() -> None:
    """First call + fake returns {"questions":[...]} → clarifyingQuestions branch.

    Verifies:
    - ok=False, shapeTtl=None, confidenceLow=True
    - clarifyingQuestions is a tuple of exactly the 2 questions
    - The fake was called exactly once (no retry budget consumed)
    """
    from magi_agent.customize.shacl_compiler import compile_nl_to_shacl

    questions_response = json.dumps({
        "questions": ["Which evidence type does this constraint target?", "What unit is amount in?"]
    })
    call_counter: list[int] = [0]
    factory = _factory_for(questions_response, call_counter=call_counter)

    result = await compile_nl_to_shacl(_NL_TEXT, _FIELDS, model_factory=factory)

    assert result["ok"] is False, f"Expected ok=False for questions response, got: {result}"
    assert result.get("shapeTtl") is None, f"Expected shapeTtl=None, got: {result.get('shapeTtl')}"
    assert result.get("confidenceLow") is True, f"Expected confidenceLow=True, got: {result.get('confidenceLow')}"
    assert "clarifyingQuestions" in result, f"Expected clarifyingQuestions key, got: {result}"

    cq = result["clarifyingQuestions"]
    assert isinstance(cq, tuple), f"clarifyingQuestions must be a tuple, got: {type(cq)}"
    assert len(cq) == 2, f"Expected 2 questions, got: {len(cq)}"
    assert cq[0] == "Which evidence type does this constraint target?", f"Got: {cq[0]}"
    assert cq[1] == "What unit is amount in?", f"Got: {cq[1]}"

    # No retry consumed — the model was called exactly once.
    assert call_counter[0] == 1, (
        f"Expected exactly 1 model call (questions = deliberate ask, not failure), "
        f"got: {call_counter[0]}"
    )


# ---------------------------------------------------------------------------
# Test 3 — follow-up call + prior_turns + valid TTL → ok:True.
# Assert prior_turns reached the model (role order in llm_request.contents).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_follow_up_call_with_prior_turns_reaches_model() -> None:
    """Follow-up with prior_turns → ok:True. prior_turns are present in llm_request.contents."""
    from magi_agent.customize.shacl_compiler import compile_nl_to_shacl

    prior_turns = (
        {"role": "user", "content": "amount field must not exceed 3000"},
        {"role": "assistant", "content": json.dumps({
            "questions": ["Which evidence type does this constraint target?"]
        })},
    )

    contents_capture: list[Any] = []
    factory = _factory_for(_VALID_TTL_RESPONSE, contents_capture=contents_capture)

    result = await compile_nl_to_shacl(
        _NL_TEXT,
        _FIELDS,
        model_factory=factory,
        prior_turns=prior_turns,
    )

    assert result["ok"] is True, f"Expected ok=True, got: {result}"
    assert result.get("shapeTtl") is not None, "Expected shapeTtl"

    # Assert that prior_turns were passed to the model — captured contents should
    # contain more than just the final user message (i.e., at least 3 Content objects:
    # the user turn, the assistant turn, and the current nl_text turn).
    assert contents_capture, "LlmRequest.contents was not captured — fake model not called"
    captured_contents = contents_capture[0]  # first (and only) call's contents list
    assert len(captured_contents) >= 3, (
        f"Expected at least 3 Content objects (prior user + prior assistant + current turn), "
        f"got {len(captured_contents)}: {[getattr(c, 'role', '?') for c in captured_contents]}"
    )

    # First two contents must have the roles from prior_turns.
    roles = [getattr(c, "role", None) for c in captured_contents]
    # "assistant" is translated to "model" in ADK (or kept as "user"/"model")
    assert roles[0] == "user", f"Expected first prior turn role='user', got: {roles[0]}"
    # ADK uses "model" for assistant turns; accept both during transition.
    assert roles[1] in ("assistant", "model"), (
        f"Expected second prior turn role='assistant' or 'model', got: {roles[1]}"
    )


# ---------------------------------------------------------------------------
# Test 4 — question count normalization: 1 question → 1 element; 3+ → trimmed to 2.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_question_normalized_to_one_element() -> None:
    """{"questions":["single question"]} → clarifyingQuestions is a 1-element tuple."""
    from magi_agent.customize.shacl_compiler import compile_nl_to_shacl

    single_q_response = json.dumps({"questions": ["What evidence type does this target?"]})
    factory = _factory_for(single_q_response)

    result = await compile_nl_to_shacl(_NL_TEXT, _FIELDS, model_factory=factory)

    assert result.get("confidenceLow") is True
    cq = result.get("clarifyingQuestions")
    assert isinstance(cq, tuple), f"Expected tuple, got {type(cq)}"
    assert len(cq) == 1, f"Expected 1 question, got: {len(cq)}"
    assert cq[0] == "What evidence type does this target?"


@pytest.mark.asyncio
async def test_three_questions_trimmed_to_two() -> None:
    """{"questions":["q1","q2","q3"]} → clarifyingQuestions capped at 2."""
    from magi_agent.customize.shacl_compiler import compile_nl_to_shacl

    three_q_response = json.dumps({
        "questions": ["Question one?", "Question two?", "Question three?"]
    })
    factory = _factory_for(three_q_response)

    result = await compile_nl_to_shacl(_NL_TEXT, _FIELDS, model_factory=factory)

    assert result.get("confidenceLow") is True
    cq = result.get("clarifyingQuestions")
    assert isinstance(cq, tuple), f"Expected tuple, got {type(cq)}"
    assert len(cq) == 2, f"Expected 2 questions (capped), got: {len(cq)}"
    assert cq[0] == "Question one?"
    assert cq[1] == "Question two?"


# ---------------------------------------------------------------------------
# Test 5 — broken JSON + broken TTL → existing retry → final ok:False, no clarifyingQuestions.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_broken_json_and_broken_ttl_falls_through_to_failure() -> None:
    """Fake returns broken JSON (not questions, not TTL) → retry path → ok:False.

    No clarifyingQuestions key in the result.
    """
    from magi_agent.customize.shacl_compiler import compile_nl_to_shacl

    # Both attempts return something that is neither valid JSON questions nor valid TTL.
    factory = _factory_for(_BROKEN_TTL_RESPONSE)

    result = await compile_nl_to_shacl(_NL_TEXT, _FIELDS, model_factory=factory)

    assert result["ok"] is False, f"Expected ok=False for broken TTL, got: {result}"
    assert "clarifyingQuestions" not in result, (
        f"clarifyingQuestions must NOT be present on failure path, got: {result}"
    )
    assert "error" in result, f"Expected error key, got: {result}"


# ---------------------------------------------------------------------------
# Test 6 — fail-open: model_factory=None → ok:False, no clarifyingQuestions, never raises.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fail_open_none_factory_no_clarifying_questions_key() -> None:
    """model_factory=None → {ok:False, error:"unavailable"}, NO clarifyingQuestions key, never raises."""
    from magi_agent.customize.shacl_compiler import compile_nl_to_shacl

    try:
        result = await compile_nl_to_shacl(_NL_TEXT, _FIELDS, model_factory=None)
    except Exception as exc:  # noqa: BLE001
        pytest.fail(
            f"compile_nl_to_shacl(model_factory=None) must not raise, but raised: {exc!r}"
        )

    assert result["ok"] is False, f"Expected ok=False for None factory, got: {result}"
    assert "error" in result, f"Expected 'error' key, got: {result}"
    assert "unavailable" in str(result["error"]).lower(), (
        f"Expected 'unavailable' in error string, got: {result['error']!r}"
    )
    assert "clarifyingQuestions" not in result, (
        f"clarifyingQuestions must NOT be present when model_factory=None, got: {result}"
    )


# ---------------------------------------------------------------------------
# Test 7 — empty questions list → NOT clarifyingQuestions; falls through to failure.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_questions_list_not_treated_as_clarifying_questions() -> None:
    """{"questions":[]} → NOT treated as a clarifying-question response.

    Falls through to the existing failure path (since it's also not a valid TTL).
    No clarifyingQuestions key in result.
    """
    from magi_agent.customize.shacl_compiler import compile_nl_to_shacl

    empty_q_response = json.dumps({"questions": []})
    factory = _factory_for(empty_q_response)

    result = await compile_nl_to_shacl(_NL_TEXT, _FIELDS, model_factory=factory)

    # Must NOT be treated as a questions response.
    assert "clarifyingQuestions" not in result, (
        f"Empty questions list must NOT yield clarifyingQuestions, got: {result}"
    )
    # It's also not a valid TTL, so it should ultimately fail.
    assert result["ok"] is False, (
        f"Expected ok=False (empty questions falls through to failure), got: {result}"
    )
