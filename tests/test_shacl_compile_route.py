"""Tests for POST /v1/app/customize/custom-rules/compile — Task 3.3 (TDD).

Written BEFORE implementation.  Covers:
  1. Flag OFF → compile route disabled (404 or {ok:False, "disabled"}).
  2. Flag ON + fake factory with valid .ttl + sampleRecords → 200 {ok:True,...},
     all fields present, JSON-serializable, store NOT modified.
  3. Flag ON + fake compile failure → {ok:False, error}, store unchanged.
  4. Fail-open: no factory (no test injection, no production key) → does not 500;
     returns {ok:False} ("unavailable" or equivalent).

Zero network, zero real model calls.  Fake factory injected via monkeypatching
the compile-route's model-factory resolver (``_resolve_shacl_compile_factory``
exported from ``magi_agent.transport.customize``).

NOTE on injection pattern:
  The ``_egressCriticModelFactory`` test-injection-via-body-key pattern works when
  the transport function is called directly with a Python dict (e.g. in
  test_chat_egress_gate_wiring.py).  Via the FastAPI TestClient HTTP path, the
  body is JSON-serialized and callables cannot survive the round-trip.  We
  therefore inject factories by monkeypatching the module-level resolver, which
  is the correct pattern for HTTP-path route tests.

Spec: docs/plans/2026-06-18-shacl-PR3-compiler-tasks.md Task 3.3
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

_TOKEN = "test-gateway-token"

# ---------------------------------------------------------------------------
# Fake ADK model helpers — same pattern as test_shacl_compiler.py
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


def _make_fake_model(response_text: str) -> object:
    """Fake ADK model that yields a canned response."""

    class _FakeModel:
        model = "fake-shacl-route-model"

        async def generate_content_async(
            self, llm_request: Any, stream: bool = False
        ) -> AsyncGenerator:
            yield _FakeLlmResponse(response_text)

    return _FakeModel()


def _factory_for(response_text: str):
    """Return a model_factory callable yielding a fake model."""

    def _factory() -> object:
        return _make_fake_model(response_text)

    return _factory


# A minimal valid SHACL TTL (same as other SHACL tests).
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
_BROKEN_TTL_RESPONSE = "```turtle\nthis is not valid turtle @@@\n```"

# A valid review JSON from the fake model (for the review step).
_VALID_REVIEW_JSON = '{"verdict": "aligned", "issues": [], "confidence": 0.9}'
# A plain English explanation from the fake model (for the explain step).
_VALID_EXPLANATION = "This shape checks that exitCode is 0."


def _make_triple_response_factory():
    """Return a factory that cycles: compile → valid_ttl, review → JSON, explain → text.

    The three compiler functions each call model_factory() once, so the factory
    is called three times: compile, review, explain (in that order within the
    route handler).
    """
    responses = [
        _VALID_TTL_RESPONSE,  # compile_nl_to_shacl → valid TTL
        _VALID_REVIEW_JSON,   # review_compilation → aligned JSON
        _VALID_EXPLANATION,   # explain_shape → plain text
    ]
    call_index: list[int] = [0]

    def _factory() -> object:
        idx = call_index[0]
        call_index[0] += 1
        text = responses[idx] if idx < len(responses) else responses[-1]
        return _make_fake_model(text)

    return _factory


def _make_failing_compile_factory():
    """Return a factory that always returns broken TTL (compile will fail)."""
    return _factory_for(_BROKEN_TTL_RESPONSE)


# ---------------------------------------------------------------------------
# App / client helpers
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


def _client(tmp_path, *, gateway_token: str = _TOKEN, with_token: bool = True) -> TestClient:
    runtime = _build_runtime(gateway_token=gateway_token)
    c = TestClient(create_app(runtime))
    if with_token:
        c.headers.update({"x-gateway-token": gateway_token})
    return c


# A minimal sample record dict for sampleRecords.
# The route converts these to EvidenceRecord objects using a simplified format:
#   type, status, fields (optional observedAt/source are filled in by the route).
_SAMPLE_RECORD_DICT = {
    "type": "TestRun",
    "status": "ok",
    "fields": {"exitCode": 0},
}


# ---------------------------------------------------------------------------
# Test 1 — flag OFF → compile route disabled
# ---------------------------------------------------------------------------


def test_compile_route_disabled_when_flag_off(tmp_path, monkeypatch):
    """MAGI_SHACL_COMPILER_ENABLED not set (default False) → route returns disabled.

    Accepts either 404 (route not registered) or {ok:False} with 200/4xx.
    The important thing: no compile happens, no store mutation.
    """
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    # Explicitly ensure flag is OFF (default, but be explicit).
    monkeypatch.delenv("MAGI_SHACL_COMPILER_ENABLED", raising=False)

    client = _client(tmp_path)
    resp = client.post(
        "/v1/app/customize/custom-rules/compile",
        json={"nlText": "amount must not exceed 3000"},
    )
    # Either 404 (route not registered when flag off) or {ok:False} 200/4xx.
    if resp.status_code == 200:
        body = resp.json()
        assert body.get("ok") is False, f"Expected ok=False when disabled, got: {body}"
    else:
        assert resp.status_code in (404, 400, 501), (
            f"Unexpected status {resp.status_code} when flag is OFF: {resp.text}"
        )
    # Store must not be touched.
    overrides = load_overrides()
    assert overrides["verification"]["custom_rules"] == []


# ---------------------------------------------------------------------------
# Test 2 — flag ON + valid compile + sampleRecords → 200 with all fields, not saved
# ---------------------------------------------------------------------------


def test_compile_route_success_with_sample_records(tmp_path, monkeypatch):
    """Flag ON + fake factory returns valid TTL + sampleRecords → full success response.

    Verifies:
    - ok=True
    - shapeTtl present and is a non-empty string
    - review present (verdict/issues/confidence)
    - explanation present (non-empty string)
    - previewCases present (list, non-empty since sampleRecords provided)
    - entire response is json.dumps-able (no MappingProxyType, etc.)
    - store was NOT modified (custom_rules still empty after the call)
    """
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    monkeypatch.setenv("MAGI_SHACL_COMPILER_ENABLED", "1")

    triple_factory = _make_triple_response_factory()

    # Inject the fake factory by monkeypatching the resolver in the transport module.
    import magi_agent.transport.customize as customize_transport
    monkeypatch.setattr(
        customize_transport,
        "_resolve_shacl_compile_factory",
        lambda body: triple_factory,
    )

    client = _client(tmp_path)
    resp = client.post(
        "/v1/app/customize/custom-rules/compile",
        json={
            "nlText": "exitCode must be 0",
            "sampleRecords": [_SAMPLE_RECORD_DICT],
        },
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()

    # ok=True
    assert body.get("ok") is True, f"Expected ok=True, got: {body}"

    # shapeTtl present
    assert "shapeTtl" in body, "shapeTtl missing from response"
    assert isinstance(body["shapeTtl"], str) and body["shapeTtl"].strip()

    # review present (dict with verdict/issues/confidence)
    assert "review" in body, "review missing from response"
    review = body["review"]
    assert isinstance(review, dict)
    assert "verdict" in review
    assert "issues" in review
    assert "confidence" in review

    # explanation present (non-empty string)
    assert "explanation" in body, "explanation missing from response"
    assert isinstance(body["explanation"], str) and body["explanation"].strip()

    # previewCases present (list, since sampleRecords was provided)
    assert "previewCases" in body, "previewCases missing from response"
    assert isinstance(body["previewCases"], list)
    assert len(body["previewCases"]) > 0

    # Entire response must be JSON-serializable (no MappingProxyType, etc.)
    try:
        _json.dumps(body)
    except TypeError as exc:
        pytest.fail(f"Response is not JSON-serializable: {exc}\nbody={body!r}")

    # Store must NOT be modified (preview-only, no save).
    overrides = load_overrides()
    assert overrides["verification"]["custom_rules"] == [], (
        "compile route must NOT save to store (preview-only)"
    )


# ---------------------------------------------------------------------------
# Test 3 — flag ON + compile failure → {ok:False, error}, store unchanged
# ---------------------------------------------------------------------------


def test_compile_route_compile_failure_returns_error(tmp_path, monkeypatch):
    """Flag ON + fake factory always returns broken TTL → {ok:False, error}, no save."""
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    monkeypatch.setenv("MAGI_SHACL_COMPILER_ENABLED", "1")

    failing_factory = _make_failing_compile_factory()

    import magi_agent.transport.customize as customize_transport
    monkeypatch.setattr(
        customize_transport,
        "_resolve_shacl_compile_factory",
        lambda body: failing_factory,
    )

    client = _client(tmp_path)
    resp = client.post(
        "/v1/app/customize/custom-rules/compile",
        json={"nlText": "exitCode must be 0"},
    )
    assert resp.status_code == 200, (
        f"Expected 200 (error payload), got {resp.status_code}: {resp.text}"
    )
    body = resp.json()

    assert body.get("ok") is False, f"Expected ok=False on compile failure, got: {body}"
    assert "error" in body, "error key missing from failure response"
    assert isinstance(body["error"], str) and body["error"].strip()

    # Store must NOT be modified.
    overrides = load_overrides()
    assert overrides["verification"]["custom_rules"] == [], (
        "compile failure must NOT save to store"
    )


# ---------------------------------------------------------------------------
# Test 4 — fail-open: no factory → does not 500; returns {ok:False}
# ---------------------------------------------------------------------------


def test_compile_route_failopen_no_factory(tmp_path, monkeypatch):
    """Flag ON + no real factory (None) → fail-open, no 500.

    With MAGI_SHACL_COMPILER_ENABLED=1 but the resolver returning None
    (mimicking the production fail-open when no key is configured), the route
    must return {ok:False} gracefully — never 500.
    """
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    monkeypatch.setenv("MAGI_SHACL_COMPILER_ENABLED", "1")

    # Resolver returns None (no factory) — mimics production fail-open.
    import magi_agent.transport.customize as customize_transport
    monkeypatch.setattr(
        customize_transport,
        "_resolve_shacl_compile_factory",
        lambda body: None,
    )

    client = _client(tmp_path)
    resp = client.post(
        "/v1/app/customize/custom-rules/compile",
        json={"nlText": "exitCode must be 0"},
    )
    # Must NOT be 500.
    assert resp.status_code != 500, (
        f"Route must not 500 on missing factory; got {resp.status_code}: {resp.text}"
    )
    # Must return {ok:False}.
    if resp.status_code == 200:
        body = resp.json()
        assert body.get("ok") is False, f"Expected ok=False on fail-open, got: {body}"
