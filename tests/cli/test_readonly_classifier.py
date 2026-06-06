"""Tests for ReadOnlyClassifier (manifest-first, cache, LLM, fail-closed).

TDD — these tests are written BEFORE the implementation.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from collections.abc import Callable
from typing import Any

import pytest

from magi_agent.cli.contracts import ControlRequest
from magi_agent.tools.manifest import ToolManifest
from magi_agent.tools.registry import ToolRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_registry(*manifests: ToolManifest) -> ToolRegistry:
    r = ToolRegistry()
    for m in manifests:
        r.register(m)
    return r


def _base_manifest(**overrides: Any) -> ToolManifest:
    """Minimal valid ToolManifest for testing.

    Defaults to a safe read-only tool: side_effect_class='none',
    parallel_safety='readonly', dangerous=False, mutates_workspace=False.

    When overriding ``dangerous=True`` or ``mutatesWorkspace=True``, the
    manifest validator requires ``sideEffectClass`` to match; the
    ``_infer_structured_policy_defaults`` model_validator auto-infers it
    unless the caller also overrides ``sideEffectClass`` explicitly.
    To avoid the validator conflict, the base does NOT include
    ``sideEffectClass`` when the caller overrides a field that forces a
    specific class — instead the caller must include ``sideEffectClass``
    themselves.  The helper removes the default ``sideEffectClass`` when
    ``dangerous`` or ``mutatesWorkspace`` is set in overrides, so the
    inferrer can do its job.
    """
    data: dict[str, Any] = {
        "name": "TestTool",
        "description": "A test tool",
        "kind": "core",
        "source": {"kind": "builtin", "package": "magi_agent.tools"},
        "permission": "read",
        "inputSchema": {},
        "timeoutMs": 5000,
        "sideEffectClass": "none",
        "parallelSafety": "readonly",
        "dangerous": False,
        "mutatesWorkspace": False,
    }
    data.update(overrides)
    # If the caller forces dangerous=True or mutatesWorkspace=True, remove
    # the default sideEffectClass="none" so the model_validator can auto-
    # infer the correct class (local_process / local_workspace).
    if overrides.get("dangerous") is True or overrides.get("mutatesWorkspace") is True:
        if "sideEffectClass" not in overrides:
            data.pop("sideEffectClass", None)
    return ToolManifest.model_validate(data)


def _req(tool_name: str = "TestTool") -> ControlRequest:
    return ControlRequest(
        requestId=f"req:{tool_name}",
        turnId="turn-1",
        toolName=tool_name,
        arguments={},
        reason="tool_use",
    )


# ---------------------------------------------------------------------------
# manifest_verdict tests
# ---------------------------------------------------------------------------

def test_manifest_verdict_returns_true_for_readonly_parallel_safety() -> None:
    from magi_agent.cli.readonly_classifier import ReadOnlyClassifier

    registry = _make_registry(
        _base_manifest(name="SafeTool", sideEffectClass="none", parallelSafety="readonly")
    )
    cls = ReadOnlyClassifier(registry=registry)
    assert cls.manifest_verdict("SafeTool") is True


def test_manifest_verdict_returns_true_for_concurrency_safe() -> None:
    from magi_agent.cli.readonly_classifier import ReadOnlyClassifier

    registry = _make_registry(
        _base_manifest(name="ConcTool", sideEffectClass="none", parallelSafety="concurrency_safe")
    )
    cls = ReadOnlyClassifier(registry=registry)
    assert cls.manifest_verdict("ConcTool") is True


def test_manifest_verdict_returns_false_for_dangerous_tool() -> None:
    from magi_agent.cli.readonly_classifier import ReadOnlyClassifier

    # dangerous=True forces sideEffectClass=local_process (manifest validator)
    registry = _make_registry(
        _base_manifest(
            name="DangerTool",
            dangerous=True,
            parallelSafety="unsafe",
        )
    )
    cls = ReadOnlyClassifier(registry=registry)
    assert cls.manifest_verdict("DangerTool") is False


def test_manifest_verdict_returns_false_for_mutating_tool() -> None:
    from magi_agent.cli.readonly_classifier import ReadOnlyClassifier

    # mutates_workspace=True forces sideEffectClass=local_workspace
    registry = _make_registry(
        _base_manifest(
            name="MutateTool",
            mutatesWorkspace=True,
            parallelSafety="unsafe",
        )
    )
    cls = ReadOnlyClassifier(registry=registry)
    assert cls.manifest_verdict("MutateTool") is False


def test_manifest_verdict_returns_false_for_external_side_effect() -> None:
    from magi_agent.cli.readonly_classifier import ReadOnlyClassifier

    # side_effect_class="external" means NOT read-only even if parallel_safety
    # might appear safe. Test the sideEffectClass != "none" path.
    # We must pair external with mutatesWorkspace=True per the validator:
    # Actually: sideEffectClass="external" alone without mutatesWorkspace is invalid.
    # Use local_process (from dangerous=True).
    registry = _make_registry(
        _base_manifest(
            name="ExtTool",
            dangerous=True,
            parallelSafety="unsafe",
        )
    )
    cls = ReadOnlyClassifier(registry=registry)
    # dangerous => sideEffectClass != "none" => False
    assert cls.manifest_verdict("ExtTool") is False


def test_manifest_verdict_returns_none_for_unknown_tool() -> None:
    from magi_agent.cli.readonly_classifier import ReadOnlyClassifier

    registry = _make_registry()  # empty
    cls = ReadOnlyClassifier(registry=registry)
    assert cls.manifest_verdict("NoSuchTool") is None


def test_manifest_verdict_returns_none_when_no_registry() -> None:
    from magi_agent.cli.readonly_classifier import ReadOnlyClassifier

    cls = ReadOnlyClassifier(registry=None)
    assert cls.manifest_verdict("AnythingTool") is None


def test_manifest_verdict_false_for_unsafe_parallel_safety_even_if_side_effect_none() -> None:
    from magi_agent.cli.readonly_classifier import ReadOnlyClassifier

    # parallel_safety="unsafe" and side_effect_class="none" — not read-only
    registry = _make_registry(
        _base_manifest(
            name="UnsafeTool",
            sideEffectClass="none",
            parallelSafety="unsafe",
        )
    )
    cls = ReadOnlyClassifier(registry=registry)
    assert cls.manifest_verdict("UnsafeTool") is False


# ---------------------------------------------------------------------------
# Cache short-circuit
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_classify_caches_llm_result_and_short_circuits_second_call() -> None:
    """Second classify() call for same tool name uses cache, not LLM."""
    from magi_agent.cli.readonly_classifier import ReadOnlyClassifier

    call_count = 0

    def _fake_model_factory():
        class _FakeLlm:
            model = "fake-model"

            async def generate_content_async(
                self, llm_request: Any, stream: bool = False
            ) -> AsyncGenerator:
                nonlocal call_count
                call_count += 1
                yield _FakeLlmResponse('{"read_only": true, "reason": "test"}')

        return _FakeLlm()

    cls = ReadOnlyClassifier(
        registry=None,  # unknown tool -> triggers LLM
        model_factory=_fake_model_factory,
    )

    req = _req("UnknownTool")
    result1 = await cls.classify(req)
    result2 = await cls.classify(req)

    assert result1 is True
    assert result2 is True
    assert call_count == 1  # LLM called ONCE; second call uses cache


@pytest.mark.asyncio
async def test_classify_manifest_known_tool_skips_llm() -> None:
    """Manifest-known read-only tool returns True without any LLM call."""
    from magi_agent.cli.readonly_classifier import ReadOnlyClassifier

    call_count = 0

    def _fake_model_factory():
        class _FakeLlm:
            model = "fake-model"

            async def generate_content_async(
                self, llm_request: Any, stream: bool = False
            ) -> AsyncGenerator:
                nonlocal call_count
                call_count += 1
                yield _FakeLlmResponse('{"read_only": true, "reason": "test"}')

        return _FakeLlm()

    registry = _make_registry(
        _base_manifest(name="KnownTool", sideEffectClass="none", parallelSafety="readonly")
    )
    cls = ReadOnlyClassifier(registry=registry, model_factory=_fake_model_factory)
    result = await cls.classify(_req("KnownTool"))

    assert result is True
    assert call_count == 0  # no LLM call for manifest-known tool


# ---------------------------------------------------------------------------
# LLM error -> fail closed
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_classify_returns_false_on_llm_exception() -> None:
    """LLM raises -> classify() returns False (fail closed)."""
    from magi_agent.cli.readonly_classifier import ReadOnlyClassifier

    evidence: list[dict] = []

    cls = ReadOnlyClassifier(
        registry=None,
        model_factory=lambda: _make_error_llm(),
        evidence_sink=evidence.append,
    )
    result = await cls.classify(_req("BrokenTool"))

    assert result is False
    assert len(evidence) == 1
    ev = evidence[0]
    assert ev["source"] == "classifier_error"
    assert ev["verdict"] is False
    assert ev["tool"] == "BrokenTool"


@pytest.mark.asyncio
async def test_classify_returns_false_on_invalid_json_response() -> None:
    """LLM returns non-JSON -> classify() returns False (fail closed)."""
    from magi_agent.cli.readonly_classifier import ReadOnlyClassifier

    evidence: list[dict] = []

    cls = ReadOnlyClassifier(
        registry=None,
        model_factory=lambda: _make_fake_llm("not valid json at all"),
        evidence_sink=evidence.append,
    )
    result = await cls.classify(_req("BadJsonTool"))

    assert result is False
    # Error evidence emitted
    assert any(e["source"] == "classifier_error" for e in evidence)


@pytest.mark.asyncio
async def test_classify_returns_false_when_no_model_factory() -> None:
    """No model_factory -> classify() returns False (fail closed, no crash)."""
    from magi_agent.cli.readonly_classifier import ReadOnlyClassifier

    evidence: list[dict] = []
    cls = ReadOnlyClassifier(
        registry=None,
        model_factory=None,
        evidence_sink=evidence.append,
    )
    result = await cls.classify(_req("AnyTool"))

    assert result is False
    # Evidence emitted with error source
    assert len(evidence) == 1
    assert evidence[0]["source"] == "classifier_error"


# ---------------------------------------------------------------------------
# LLM success -> cache + evidence
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_classify_llm_success_caches_and_emits_llm_evidence() -> None:
    """LLM returns read_only=true -> classify True, evidence source=llm, cached."""
    from magi_agent.cli.readonly_classifier import ReadOnlyClassifier

    evidence: list[dict] = []

    cls = ReadOnlyClassifier(
        registry=None,
        model_factory=lambda: _make_fake_llm('{"read_only": true, "reason": "SELECT query"}'),
        evidence_sink=evidence.append,
    )
    req = _req("SelectTool")
    result = await cls.classify(req)

    assert result is True
    assert len(evidence) == 1
    ev = evidence[0]
    assert ev["source"] == "llm"
    assert ev["verdict"] is True
    assert ev["tool"] == "SelectTool"
    assert "reason" in ev

    # Second call uses cache: no new evidence, same result
    result2 = await cls.classify(req)
    assert result2 is True
    assert len(evidence) == 2  # cache evidence emitted on second call
    assert evidence[1]["source"] == "cache"


@pytest.mark.asyncio
async def test_classify_llm_returns_false_and_emits_evidence() -> None:
    """LLM returns read_only=false -> classify False + evidence."""
    from magi_agent.cli.readonly_classifier import ReadOnlyClassifier

    evidence: list[dict] = []

    cls = ReadOnlyClassifier(
        registry=None,
        model_factory=lambda: _make_fake_llm('{"read_only": false, "reason": "INSERT mutation"}'),
        evidence_sink=evidence.append,
    )
    result = await cls.classify(_req("InsertTool"))

    assert result is False
    assert evidence[0]["source"] == "llm"
    assert evidence[0]["verdict"] is False


# ---------------------------------------------------------------------------
# Real ADK async-generator contract verification
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_llm_async_generator_contract_produces_correct_verdict() -> None:
    """Verify the classifier correctly consumes an async-generator LlmResponse.

    This test encodes the PRODUCTION contract: generate_content_async is an
    async generator that yields objects with .content.parts[i].text, NOT an
    awaitable with .text.  Any regression that breaks this contract will fail
    here.
    """
    from magi_agent.cli.readonly_classifier import ReadOnlyClassifier

    evidence: list[dict] = []

    # Fake that matches the REAL ADK shape exactly.
    class _RealShapeLlm:
        model = "fake/real-shape-model"

        async def generate_content_async(
            self, llm_request: Any, stream: bool = False
        ) -> AsyncGenerator:
            # Yield a response whose text lives in content.parts[0].text —
            # exactly where LiteLlm places it in production.
            yield _FakeLlmResponse('{"read_only": true, "reason": "pure SELECT"}')

    cls = ReadOnlyClassifier(
        registry=None,
        model_factory=_RealShapeLlm,
        evidence_sink=evidence.append,
    )
    result = await cls.classify(_req("RealShapeTool"))

    assert result is True
    assert len(evidence) == 1
    ev = evidence[0]
    assert ev["source"] == "llm"
    assert ev["verdict"] is True
    assert ev["reason"] == "pure SELECT"
    assert ev["model"] == "fake/real-shape-model"


@pytest.mark.asyncio
async def test_classify_returns_false_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM call exceeds timeout -> fail closed with classifier_error evidence."""
    import os
    from magi_agent.cli.readonly_classifier import ReadOnlyClassifier

    # Set a very short timeout so the sleeping fake triggers it.
    monkeypatch.setenv("MAGI_SMART_APPROVE_TIMEOUT", "0.05")

    evidence: list[dict] = []

    class _SlowLlm:
        model = "fake-slow-model"

        async def generate_content_async(
            self, llm_request: Any, stream: bool = False
        ) -> AsyncGenerator:
            await asyncio.sleep(10)  # way longer than the 50 ms timeout
            yield _FakeLlmResponse('{"read_only": true, "reason": "never reached"}')

    cls = ReadOnlyClassifier(
        registry=None,
        model_factory=_SlowLlm,
        evidence_sink=evidence.append,
    )
    result = await cls.classify(_req("SlowTool"))

    assert result is False
    assert len(evidence) == 1
    ev = evidence[0]
    assert ev["source"] == "classifier_error"
    assert ev["verdict"] is False
    assert "timeout" in ev["reason"].lower()


@pytest.mark.asyncio
async def test_reason_is_capped_at_max_chars() -> None:
    """LLM reason longer than _MAX_REASON_CHARS is truncated in evidence."""
    from magi_agent.cli.readonly_classifier import ReadOnlyClassifier, _MAX_REASON_CHARS

    long_reason = "x" * (_MAX_REASON_CHARS + 200)
    payload = json.dumps({"read_only": True, "reason": long_reason})

    evidence: list[dict] = []

    cls = ReadOnlyClassifier(
        registry=None,
        model_factory=lambda: _make_fake_llm(payload),
        evidence_sink=evidence.append,
    )
    await cls.classify(_req("LongReasonTool"))

    assert len(evidence) == 1
    assert len(evidence[0]["reason"]) <= _MAX_REASON_CHARS


# ---------------------------------------------------------------------------
# Manifest path evidence
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_classify_emits_manifest_evidence_for_known_readonly_tool() -> None:
    """Known read-only manifest tool: evidence source=manifest."""
    from magi_agent.cli.readonly_classifier import ReadOnlyClassifier

    evidence: list[dict] = []
    registry = _make_registry(
        _base_manifest(name="ReadOnlyTool", sideEffectClass="none", parallelSafety="readonly")
    )
    cls = ReadOnlyClassifier(
        registry=registry,
        evidence_sink=evidence.append,
    )
    result = await cls.classify(_req("ReadOnlyTool"))

    assert result is True
    assert len(evidence) == 1
    assert evidence[0]["source"] == "manifest"
    assert evidence[0]["verdict"] is True


@pytest.mark.asyncio
async def test_classify_emits_manifest_evidence_for_known_dangerous_tool() -> None:
    """Known dangerous tool: evidence source=manifest, verdict=False."""
    from magi_agent.cli.readonly_classifier import ReadOnlyClassifier

    evidence: list[dict] = []
    registry = _make_registry(
        _base_manifest(name="DangerTool2", dangerous=True, parallelSafety="unsafe")
    )
    cls = ReadOnlyClassifier(
        registry=registry,
        evidence_sink=evidence.append,
    )
    result = await cls.classify(_req("DangerTool2"))

    assert result is False
    assert evidence[0]["source"] == "manifest"
    assert evidence[0]["verdict"] is False


# ---------------------------------------------------------------------------
# Evidence type name is the registered custom type
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_classify_evidence_uses_registered_type_name() -> None:
    """Evidence records use the canonical type name."""
    from magi_agent.cli.readonly_classifier import (
        SMART_APPROVE_EVIDENCE_TYPE,
        ReadOnlyClassifier,
    )
    from magi_agent.evidence.types import validate_evidence_type_name

    # Validate the constant is a valid evidence type name
    assert validate_evidence_type_name(SMART_APPROVE_EVIDENCE_TYPE) == SMART_APPROVE_EVIDENCE_TYPE

    evidence: list[dict] = []
    registry = _make_registry(
        _base_manifest(name="SomeTool", sideEffectClass="none", parallelSafety="readonly")
    )
    cls = ReadOnlyClassifier(registry=registry, evidence_sink=evidence.append)
    await cls.classify(_req("SomeTool"))

    assert evidence[0]["type"] == SMART_APPROVE_EVIDENCE_TYPE


# ---------------------------------------------------------------------------
# Fake LLM helpers matching the REAL ADK async-generator contract.
#
# The real LiteLlm.generate_content_async(llm_request, stream=False) is an
# async generator that yields LlmResponse objects, each of which has:
#   .content -> types.Content
#   .content.parts -> list[types.Part]
#   .content.parts[i].text -> str
#
# The fakes below implement exactly this contract so tests encode production
# behaviour and any regression will be caught here.
# ---------------------------------------------------------------------------

class _FakePart:
    """Minimal stand-in for google.genai.types.Part."""

    def __init__(self, text: str) -> None:
        self.text = text


class _FakeContent:
    """Minimal stand-in for google.genai.types.Content."""

    def __init__(self, text: str) -> None:
        self.parts = [_FakePart(text)]


class _FakeLlmResponse:
    """Minimal stand-in for google.adk.models.llm_response.LlmResponse."""

    def __init__(self, text: str) -> None:
        self.content = _FakeContent(text)


def _make_fake_llm(json_text: str) -> object:
    """Return a fake LLM that yields one *LlmResponse-shaped* object from
    generate_content_async, matching the real ADK async-generator contract."""

    class _FakeLlm:
        model = "fake-model"

        async def generate_content_async(  # noqa: D401
            self, llm_request: Any, stream: bool = False
        ) -> AsyncGenerator:
            yield _FakeLlmResponse(json_text)

    return _FakeLlm()


def _make_error_llm() -> object:
    """Return a fake LLM whose generate_content_async raises immediately."""

    class _ErrorLlm:
        model = "fake-model"

        async def generate_content_async(
            self, llm_request: Any, stream: bool = False
        ) -> AsyncGenerator:
            raise RuntimeError("network error")
            # needed so Python treats this as an async generator
            yield  # type: ignore[misc]  # pragma: no cover

    return _ErrorLlm()
