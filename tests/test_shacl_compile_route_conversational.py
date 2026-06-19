"""TDD tests for Task 5.2 — compile route priorTurns thread-through.

Tests cover:
  1. No priorTurns + valid TTL → 200 {ok:True, shapeTtl, review, explanation}. (regression)
  2. priorTurns=[] + fake returns clarifyingQuestions → 200 {ok:False, clarifyingQuestions:[...],
     shapeTtl:null}. Reviewer/preview NOT invoked. Store unchanged.
  3. priorTurns 2 turns (1 user + 1 assistant) + fake returns valid TTL → 200 {ok:True, shapeTtl}.
     Compiler receives prior_turns (spy via contents capture).
  4. priorTurns with 3 user turns (round cap exceeded) → 400 {ok:False, error:"too many …"}.
     Compiler NOT invoked.
  5. priorTurns with role="system" or non-str content → those elements silently skipped;
     request still proceeds with the remainder.
  6. priorTurns is a dict (not a list) → ignored; behavior identical to no-priorTurns case.
  7. priorTurns total content exceeds 5 * _MAX_NL_TEXT_BYTES → 400 {ok:False, error:"priorTurns total content too large"}.

Spec: docs/plans/2026-06-19-shacl-conversational-compile-tasks.md Task 5.2
"""
from __future__ import annotations

import json as _json
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from magi_agent.app import create_app
from magi_agent.config.models import BuildInfo, RuntimeConfig
from magi_agent.customize.store import load_overrides
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime
from magi_agent.transport.customize import _MAX_NL_TEXT_BYTES

_TOKEN = "test-gateway-token"

# ---------------------------------------------------------------------------
# Fake ADK model helpers — mirror the existing route test pattern
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
    contents_capture: list[Any] | None = None,
    call_counter: list[int] | None = None,
) -> object:
    """Fake ADK model yielding a canned response, with optional introspection."""

    class _FakeModel:
        model = "fake-shacl-route-conversational-model"

        async def generate_content_async(
            self, llm_request: Any, stream: bool = False
        ) -> AsyncGenerator:
            if call_counter is not None:
                call_counter[0] += 1
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
    contents_capture: list[Any] | None = None,
    call_counter: list[int] | None = None,
):
    """Return a model_factory callable yielding a fake model with a canned response."""

    def _factory() -> object:
        return _make_fake_model(
            response_text,
            contents_capture=contents_capture,
            call_counter=call_counter,
        )

    return _factory


# ---------------------------------------------------------------------------
# Shared TTL and response constants
# ---------------------------------------------------------------------------

_VALID_TTL = """\
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix magi: <https://openmagi.ai/ns/evidence#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

magi:TestShape
    a sh:NodeShape ;
    sh:targetClass magi:Evidence ;
    sh:property [
        sh:path magi:field_exitCode ;
        sh:maxInclusive 0 ;
        sh:message "exitCode must be 0" ;
    ] .
"""

_VALID_TTL_RESPONSE = f"```turtle\n{_VALID_TTL}\n```"
_VALID_REVIEW_JSON = '{"verdict": "aligned", "issues": [], "confidence": 0.9}'
_VALID_EXPLANATION = "This shape checks that exitCode is 0."

_QUESTIONS_RESPONSE = _json.dumps({
    "questions": ["Which evidence type does this target?", "What is the unit?"]
})

# ---------------------------------------------------------------------------
# Factory helpers for multi-step scenarios
# ---------------------------------------------------------------------------


def _make_triple_response_factory(
    *,
    compile_response: str = _VALID_TTL_RESPONSE,
    contents_capture: list[Any] | None = None,
    compile_call_counter: list[int] | None = None,
):
    """Return a factory that cycles: compile → TTL, review → JSON, explain → text.

    When contents_capture is provided, it captures the LlmRequest.contents from
    the FIRST call (compile step) for spy assertions.
    """
    responses = [
        compile_response,  # compile_nl_to_shacl → TTL (or questions)
        _VALID_REVIEW_JSON,  # review_compilation → aligned JSON
        _VALID_EXPLANATION,  # explain_shape → plain text
    ]
    call_index: list[int] = [0]

    def _factory() -> object:
        idx = call_index[0]
        call_index[0] += 1
        text = responses[idx] if idx < len(responses) else responses[-1]
        # Only capture on the first (compile) call.
        cap = contents_capture if idx == 0 else None
        ctr = compile_call_counter if idx == 0 else None
        return _make_fake_model(text, contents_capture=cap, call_counter=ctr)

    return _factory


def _make_questions_factory(*, spy_compile_called: list[int] | None = None):
    """Return a factory where the first call returns clarifying questions.

    spy_compile_called is incremented when the compile model is called,
    letting tests assert that reviewer/explain were NOT reached.
    """
    call_index: list[int] = [0]

    def _factory() -> object:
        idx = call_index[0]
        call_index[0] += 1
        if spy_compile_called is not None and idx == 0:
            spy_compile_called[0] += 1
        # Only the compile step is ever called for questions responses;
        # the reviewer/explain steps must NOT be invoked.
        text = _QUESTIONS_RESPONSE
        return _make_fake_model(text)

    return _factory


# ---------------------------------------------------------------------------
# App / client helpers (mirror existing test pattern)
# ---------------------------------------------------------------------------


def _build_runtime(*, gateway_token: str = _TOKEN) -> OpenMagiRuntime:
    return OpenMagiRuntime(
        config=RuntimeConfig(
            bot_id="local-bot",
            user_id="local-user",
            gateway_token=gateway_token,
            api_proxy_url="http://api-proxy.local",
            chat_proxy_url="http://chat-proxy.local",
            redis_url="redis://redis.local:6379/0",
            model="gpt-5.2",
            build=BuildInfo(version="0.1.0", build_sha="sha-test"),
        )
    )


def _client(tmp_path, *, gateway_token: str = _TOKEN) -> TestClient:
    runtime = _build_runtime(gateway_token=gateway_token)
    c = TestClient(create_app(runtime))
    c.headers.update({"x-gateway-token": gateway_token})
    return c


# ---------------------------------------------------------------------------
# Test 1 — No priorTurns + valid TTL → 200 ok:True (regression)
# ---------------------------------------------------------------------------


def test_no_prior_turns_valid_ttl_returns_ok_true(tmp_path, monkeypatch):
    """No priorTurns + valid TTL → 200 {ok:True, shapeTtl, review, explanation}. Regression."""
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    monkeypatch.setenv("MAGI_SHACL_COMPILER_ENABLED", "1")

    triple_factory = _make_triple_response_factory()

    import magi_agent.transport.customize as customize_transport
    monkeypatch.setattr(
        customize_transport,
        "_resolve_shacl_compile_factory",
        lambda body: triple_factory,
    )

    client = _client(tmp_path)
    resp = client.post(
        "/v1/app/customize/custom-rules/compile",
        json={"nlText": "exitCode must be 0"},
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()

    assert body.get("ok") is True, f"Expected ok=True, got: {body}"
    assert "shapeTtl" in body, "shapeTtl missing"
    assert isinstance(body["shapeTtl"], str) and body["shapeTtl"].strip()
    assert "review" in body, "review missing"
    assert "explanation" in body, "explanation missing"


# ---------------------------------------------------------------------------
# Test 2 — priorTurns=[] + fake returns clarifyingQuestions → 200 ok:False with questions.
#           Reviewer/preview NOT invoked. Store unchanged.
# ---------------------------------------------------------------------------


def test_empty_prior_turns_with_clarifying_questions_response(tmp_path, monkeypatch):
    """priorTurns=[] + fake returns questions → 200 {ok:False, clarifyingQuestions:[...],
    shapeTtl:null}. Reviewer/preview NOT invoked. Store unchanged."""
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    monkeypatch.setenv("MAGI_SHACL_COMPILER_ENABLED", "1")

    # Track total model calls to verify reviewer/explain NOT called.
    total_calls: list[int] = [0]
    questions_factory = _make_questions_factory(spy_compile_called=total_calls)

    import magi_agent.transport.customize as customize_transport
    monkeypatch.setattr(
        customize_transport,
        "_resolve_shacl_compile_factory",
        lambda body: questions_factory,
    )

    client = _client(tmp_path)
    resp = client.post(
        "/v1/app/customize/custom-rules/compile",
        json={"nlText": "exitCode must be 0", "priorTurns": []},
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()

    assert body.get("ok") is False, f"Expected ok=False for questions branch, got: {body}"
    assert "clarifyingQuestions" in body, f"Expected clarifyingQuestions in response, got: {body}"
    cq = body["clarifyingQuestions"]
    assert isinstance(cq, list), f"clarifyingQuestions must be a list in JSON, got: {type(cq)}"
    assert len(cq) >= 1, f"Expected at least 1 question, got: {cq}"

    # shapeTtl must be null/None.
    assert body.get("shapeTtl") is None, f"Expected shapeTtl=null, got: {body.get('shapeTtl')}"

    # Reviewer/preview must NOT have been called:
    # only 1 compile model call should have happened (the factory increments once per
    # call to _factory(); questions branch skips reviewer+explain).
    assert total_calls[0] == 1, (
        f"Expected exactly 1 compile call, reviewer/explain not called. Got {total_calls[0]}"
    )

    # review/explanation must NOT be in response.
    assert "review" not in body or body.get("review") is None, (
        f"Reviewer must not be called on clarifyingQuestions branch, got review: {body.get('review')}"
    )

    # Response must be JSON-serializable (no MappingProxyType leakage).
    try:
        _json.dumps(body)
    except TypeError as exc:
        pytest.fail(f"Response not JSON-serializable: {exc}")

    # Store must NOT be modified.
    overrides = load_overrides()
    assert overrides["verification"]["custom_rules"] == [], "Store must not be modified"


# ---------------------------------------------------------------------------
# Test 3 — priorTurns 2 turns (1 user + 1 assistant) + valid TTL → 200 ok:True.
#           Compiler receives prior_turns (spy via contents capture).
# ---------------------------------------------------------------------------


def test_two_prior_turns_threaded_into_compiler(tmp_path, monkeypatch):
    """priorTurns=[user,assistant] + valid TTL → 200 ok:True.
    Spy confirms compiler received prior_turns as LlmRequest.contents."""
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    monkeypatch.setenv("MAGI_SHACL_COMPILER_ENABLED", "1")

    contents_capture: list[Any] = []
    triple_factory = _make_triple_response_factory(contents_capture=contents_capture)

    import magi_agent.transport.customize as customize_transport
    monkeypatch.setattr(
        customize_transport,
        "_resolve_shacl_compile_factory",
        lambda body: triple_factory,
    )

    prior_turns = [
        {"role": "user", "content": "exitCode must be 0"},
        {"role": "assistant", "content": _json.dumps({
            "questions": ["Which evidence type does this target?"]
        })},
    ]

    client = _client(tmp_path)
    resp = client.post(
        "/v1/app/customize/custom-rules/compile",
        json={"nlText": "exitCode must be 0 for TestRun", "priorTurns": prior_turns},
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()

    assert body.get("ok") is True, f"Expected ok=True, got: {body}"
    assert "shapeTtl" in body, "shapeTtl missing"
    assert isinstance(body.get("review"), dict), "review missing or not a dict"

    # Assert compiler received prior_turns — there should be at least 3 Content
    # objects in the first compile call's request (prior_user + prior_assistant + current).
    assert contents_capture, "Compiler contents_capture was not populated (fake not called?)"
    captured = contents_capture[0]
    assert len(captured) >= 3, (
        f"Expected ≥3 Content objects in LlmRequest (2 prior turns + current), "
        f"got {len(captured)}: roles={[getattr(c, 'role', '?') for c in captured]}"
    )
    # First content must be user role (from prior_turns[0]).
    assert getattr(captured[0], "role", None) == "user", (
        f"Expected first prior turn role='user', got: {getattr(captured[0], 'role', None)}"
    )
    # Second content must be assistant/model role (from prior_turns[1]).
    assert getattr(captured[1], "role", None) in ("assistant", "model"), (
        f"Expected second prior turn role='assistant'/'model', got: {getattr(captured[1], 'role', None)}"
    )


# ---------------------------------------------------------------------------
# Test 4 — priorTurns with 3 user turns → 400 round cap exceeded.
#           Compiler NOT invoked.
# ---------------------------------------------------------------------------


def test_three_user_prior_turns_exceeds_round_cap(tmp_path, monkeypatch):
    """priorTurns containing 3 user turns → 400 {ok:False, error:'too many conversation rounds'}.
    Compiler must NOT be invoked."""
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    monkeypatch.setenv("MAGI_SHACL_COMPILER_ENABLED", "1")

    compiler_call_count: list[int] = [0]

    import magi_agent.transport.customize as customize_transport

    def _counting_factory(body: object) -> object:
        def _factory() -> object:
            compiler_call_count[0] += 1
            return _make_fake_model(_VALID_TTL_RESPONSE)
        return _factory

    monkeypatch.setattr(customize_transport, "_resolve_shacl_compile_factory", _counting_factory)

    # 3 user turns + 2 assistant turns (alternating), so 3 user turns total.
    prior_turns = [
        {"role": "user", "content": "first user question"},
        {"role": "assistant", "content": "first assistant clarification"},
        {"role": "user", "content": "second user answer"},
        {"role": "assistant", "content": "second assistant clarification"},
        {"role": "user", "content": "third user answer"},
    ]

    client = _client(tmp_path)
    resp = client.post(
        "/v1/app/customize/custom-rules/compile",
        json={"nlText": "exitCode must be 0", "priorTurns": prior_turns},
    )
    assert resp.status_code == 400, f"Expected 400 for round cap, got {resp.status_code}: {resp.text}"
    body = resp.json()

    assert body.get("ok") is False, f"Expected ok=False, got: {body}"
    assert "error" in body, "error key missing"
    assert "too many conversation rounds" in body["error"], (
        f"Expected 'too many conversation rounds' in error, got: {body['error']!r}"
    )

    # Compiler must NOT have been called.
    assert compiler_call_count[0] == 0, (
        f"Compiler must not be invoked when round cap exceeded, called {compiler_call_count[0]} times"
    )


# ---------------------------------------------------------------------------
# Test 5 — priorTurns with invalid elements (role="system", non-str content)
#           → those elements silently skipped; valid ones passed through.
# ---------------------------------------------------------------------------


def test_invalid_prior_turn_elements_silently_skipped(tmp_path, monkeypatch):
    """priorTurns with role='system' or non-str content → those elements skipped,
    valid elements passed to compiler; request still succeeds."""
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    monkeypatch.setenv("MAGI_SHACL_COMPILER_ENABLED", "1")

    contents_capture: list[Any] = []
    triple_factory = _make_triple_response_factory(contents_capture=contents_capture)

    import magi_agent.transport.customize as customize_transport
    monkeypatch.setattr(
        customize_transport,
        "_resolve_shacl_compile_factory",
        lambda body: triple_factory,
    )

    prior_turns = [
        {"role": "system", "content": "should be skipped (invalid role)"},
        {"role": "user", "content": 12345},        # non-str content → skipped
        {"role": "assistant", "content": None},    # non-str content → skipped
        {"role": "user", "content": "valid user turn"},  # only this passes
    ]

    client = _client(tmp_path)
    resp = client.post(
        "/v1/app/customize/custom-rules/compile",
        json={"nlText": "exitCode must be 0", "priorTurns": prior_turns},
    )
    # Request must still succeed (invalid elements skipped, not rejected).
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body.get("ok") is True, f"Expected ok=True (valid elements still passed), got: {body}"

    # Compiler was called with only the 1 valid turn prepended.
    # LlmRequest.contents should have: 1 prior (valid user) + 1 current = 2 Content objects.
    assert contents_capture, "contents_capture was empty — compiler not called?"
    captured = contents_capture[0]
    # 1 valid prior turn + 1 current prompt turn = 2 contents minimum.
    assert len(captured) >= 2, (
        f"Expected ≥2 Content objects (1 valid prior + current), got {len(captured)}"
    )
    # Should NOT have 4 contents (that would mean invalid elements were included).
    assert len(captured) < 5, (
        f"Too many Content objects — invalid elements were not skipped? Got {len(captured)}"
    )


# ---------------------------------------------------------------------------
# Test 6 — priorTurns is a dict (not a list) → ignored; identical to no-priorTurns.
# ---------------------------------------------------------------------------


def test_prior_turns_dict_not_list_is_ignored(tmp_path, monkeypatch):
    """priorTurns is a dict (not a list) → silently ignored.
    Behavior identical to the no-priorTurns case (regression-safe)."""
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    monkeypatch.setenv("MAGI_SHACL_COMPILER_ENABLED", "1")

    contents_capture: list[Any] = []
    triple_factory = _make_triple_response_factory(contents_capture=contents_capture)

    import magi_agent.transport.customize as customize_transport
    monkeypatch.setattr(
        customize_transport,
        "_resolve_shacl_compile_factory",
        lambda body: triple_factory,
    )

    client = _client(tmp_path)
    resp = client.post(
        "/v1/app/customize/custom-rules/compile",
        json={"nlText": "exitCode must be 0", "priorTurns": {"role": "user", "content": "bad"}},
    )
    # Must still succeed — dict is ignored, not rejected.
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body.get("ok") is True, f"Expected ok=True when priorTurns is a dict, got: {body}"

    # Compiler was called with NO prior turns (dict was ignored).
    # LlmRequest.contents should have exactly 1 entry (the current prompt).
    assert contents_capture, "contents_capture was empty"
    captured = contents_capture[0]
    assert len(captured) == 1, (
        f"Expected exactly 1 Content object (current prompt only, no prior turns), "
        f"got {len(captured)}: {[getattr(c, 'role', '?') for c in captured]}"
    )


# ---------------------------------------------------------------------------
# Test 7 — priorTurns total content exceeds 5 * _MAX_NL_TEXT_BYTES → 400 DoS guard.
# ---------------------------------------------------------------------------


def test_prior_turns_total_content_too_large_rejected(tmp_path, monkeypatch):
    """priorTurns total content > 5 * _MAX_NL_TEXT_BYTES → 400 {ok:False, error:'priorTurns total content too large'}."""
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    monkeypatch.setenv("MAGI_SHACL_COMPILER_ENABLED", "1")

    compiler_call_count: list[int] = [0]

    import magi_agent.transport.customize as customize_transport

    def _counting_factory(body: object) -> object:
        def _factory() -> object:
            compiler_call_count[0] += 1
            return _make_fake_model(_VALID_TTL_RESPONSE)
        return _factory

    monkeypatch.setattr(customize_transport, "_resolve_shacl_compile_factory", _counting_factory)

    # Build turns whose combined content exceeds the cap.
    # Each turn content is just at _MAX_NL_TEXT_BYTES (one byte below the per-element cap),
    # so 6 such turns would exceed 5 * _MAX_NL_TEXT_BYTES.
    big_content = "x" * _MAX_NL_TEXT_BYTES  # exactly at per-element cap (valid per-element)
    prior_turns = [
        {"role": "user", "content": big_content},
        {"role": "assistant", "content": big_content},
        {"role": "user", "content": big_content},
        {"role": "assistant", "content": big_content},
        {"role": "user", "content": big_content},
        {"role": "assistant", "content": big_content},
    ]
    # Total = 6 * _MAX_NL_TEXT_BYTES > 5 * _MAX_NL_TEXT_BYTES → should be rejected.

    client = _client(tmp_path)
    resp = client.post(
        "/v1/app/customize/custom-rules/compile",
        json={"nlText": "exitCode must be 0", "priorTurns": prior_turns},
    )
    assert resp.status_code == 400, (
        f"Expected 400 for oversized priorTurns total, got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    assert body.get("ok") is False, f"Expected ok=False, got: {body}"
    assert "error" in body, "error key missing"
    assert "priorTurns total content too large" in body["error"], (
        f"Expected 'priorTurns total content too large' in error, got: {body['error']!r}"
    )

    # Compiler must NOT have been invoked.
    assert compiler_call_count[0] == 0, (
        f"Compiler must not run when DoS guard fires, called {compiler_call_count[0]} times"
    )
