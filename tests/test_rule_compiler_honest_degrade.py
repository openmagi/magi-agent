"""PR-F3 — honest-degrade self-check tests for the NL rule_compiler.

Spec section 5 (PR-F3 honest-degrade guard):
  After LLM-generated SHACL (the current ``shacl_constraint`` path or the new
  ``field_constraint`` path), run a self-check:
    1. Parse the generated TTL (or the structured IR).
    2. For each ``sh:targetClass``, extract referenced ``sh:path`` predicates.
    3. Cross-check each path against ``available_fields(evidence_type)``.
    4. If any path is unknown: fail compile with structured error
       ``{ok: False, error: "field_not_in_catalog",
          missingFields: [{evidenceType, field}], explanation, suggestion}``.
  Plus a NL-side check: if the compiler's routing reads as ``field_constraint``
  but ``available_fields(picked_type)`` is empty, return ``clarifyingQuestions``.

ZERO network, ZERO real model calls.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

import pytest

from magi_agent.customize.rule_compiler import (
    compile_with_review,
)


# ---------------------------------------------------------------------------
# Fake ADK model helpers
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
    class _FakeModel:
        model = "fake-rule-compiler-model"

        async def generate_content_async(
            self, llm_request: Any, stream: bool = False
        ) -> AsyncGenerator:
            yield _FakeLlmResponse(response_text)

    return _FakeModel()


def _factory_for(response_text: str):
    def _factory() -> object:
        return _make_fake_model(response_text)

    return _factory


_VALID_REVIEW_RESPONSE = '{"verdict": "aligned", "issues": [], "confidence": 0.9}'


def _shacl_compile_response(shape_ttl: str) -> str:
    body = {
        "routedKind": "shacl_constraint",
        "draft": {
            "scope": "coding",
            "enabled": True,
            "firesAt": "pre_final",
            "action": "block",
            "what": {
                "kind": "shacl_constraint",
                "payload": {"shapeTtl": shape_ttl},
            },
        },
        "explanation": "raw SHACL shape from the LLM",
    }
    return f"```json\n{json.dumps(body)}\n```"


def _field_constraint_compile_response(payload: dict[str, Any]) -> str:
    body = {
        "routedKind": "field_constraint",
        "draft": {
            "scope": "coding",
            "enabled": True,
            "firesAt": "pre_final",
            "action": "block",
            "what": {"kind": "field_constraint", "payload": payload},
        },
        "explanation": "structured field constraint",
    }
    return f"```json\n{json.dumps(body)}\n```"


# ---------------------------------------------------------------------------
# Honest-degrade — vacuous SHACL rejected
# ---------------------------------------------------------------------------


def _shape_ttl_with_field(evidence_type: str, field_name: str) -> str:
    return (
        "@prefix sh: <http://www.w3.org/ns/shacl#> .\n"
        "@prefix magi: <https://openmagi.ai/ns/evidence#> .\n"
        "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .\n"
        "[] a sh:NodeShape ;\n"
        "  sh:targetClass magi:Evidence ;\n"
        "  sh:property [\n"
        f"    sh:path magi:field_{field_name} ;\n"
        "    sh:minCount 1 ;\n"
        "  ] .\n"
    )


# A field that is verifiably NOT in the catalog hints anywhere.
# (Audited 2026-06-23: `_BUILTIN_FIELD_HINTS` contains no key
# named "magicTotallyImaginaryField".)
_UNKNOWN_FIELD = "magicTotallyImaginaryField"


@pytest.mark.asyncio
async def test_honest_degrade_rejects_shacl_referencing_unknown_field() -> None:
    """Compile result must be ``ok=False`` with structured error when the LLM
    emitted SHACL references a path that isn't in ``available_fields()``."""
    shape_ttl = _shape_ttl_with_field("TestRun", _UNKNOWN_FIELD)
    out = await compile_with_review(
        "made up field constraint",
        compiler_model_factory=_factory_for(_shacl_compile_response(shape_ttl)),
        reviewer_model_factory=_factory_for(_VALID_REVIEW_RESPONSE),
    )
    assert out["ok"] is False
    assert out["error"] == "field_not_in_catalog"
    missing = out["missingFields"]
    assert isinstance(missing, list) and missing, "missingFields must be a non-empty list"
    assert any(
        m.get("field") == _UNKNOWN_FIELD
        for m in missing
    ), f"unknown field must appear in missingFields, got {missing!r}"
    assert "suggestion" in out and isinstance(out["suggestion"], str)
    assert "Reusable evidence" in out["suggestion"] or "Browse" in out["suggestion"]
    assert "explanation" in out and isinstance(out["explanation"], str)


@pytest.mark.asyncio
async def test_honest_degrade_passes_shacl_referencing_known_field() -> None:
    """Sanity counterpart: a SHACL shape that references a catalog-known field
    must NOT trigger the honest-degrade rejection (no false positives)."""
    shape_ttl = _shape_ttl_with_field("TestRun", "exitCode")
    out = await compile_with_review(
        "TestRun must have exitCode",
        compiler_model_factory=_factory_for(_shacl_compile_response(shape_ttl)),
        reviewer_model_factory=_factory_for(_VALID_REVIEW_RESPONSE),
    )
    assert out["ok"] is True, f"known field must pass; got {out!r}"
    assert out["routedKind"] == "shacl_constraint"


# ---------------------------------------------------------------------------
# Honest-degrade — field_constraint IR with unknown field
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_honest_degrade_rejects_field_constraint_with_unknown_field() -> None:
    payload = {
        "evidenceType": "TestRun",
        "field": _UNKNOWN_FIELD,
        "operator": "eq",
        "value": 0,
    }
    out = await compile_with_review(
        "made-up field constraint via IR",
        compiler_model_factory=_factory_for(
            _field_constraint_compile_response(payload)
        ),
        reviewer_model_factory=_factory_for(_VALID_REVIEW_RESPONSE),
    )
    assert out["ok"] is False
    assert out["error"] == "field_not_in_catalog"
    missing = out["missingFields"]
    assert missing
    assert any(
        m.get("evidenceType") == "TestRun" and m.get("field") == _UNKNOWN_FIELD
        for m in missing
    )


# ---------------------------------------------------------------------------
# NL-side check — empty available_fields(picked_type) → clarifyingQuestions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_field_constraint_against_empty_field_catalog_returns_clarifying() -> None:
    """When the LLM routes to ``field_constraint`` on an evidence type whose
    ``available_fields`` entry is ``[]`` (producer unverified), the compiler
    must NOT silently emit a vacuous shape: it must return
    ``clarifyingQuestions`` so the user picks a verified type or field."""
    # GitDiff is audited (2026-06-23) to have `fields=[]` in the field hints —
    # see magi_agent/customize/shacl_compiler.py::_BUILTIN_FIELD_HINTS.
    payload = {
        "evidenceType": "GitDiff",
        "field": "anyKey",
        "operator": "exists",
        "value": None,
    }
    out = await compile_with_review(
        "every GitDiff has anyKey",
        compiler_model_factory=_factory_for(
            _field_constraint_compile_response(payload)
        ),
        reviewer_model_factory=_factory_for(_VALID_REVIEW_RESPONSE),
    )
    assert out["ok"] is False
    questions = out.get("clarifyingQuestions")
    assert questions, "empty-catalog routing must short-circuit to clarifyingQuestions"
    assert len(questions) >= 1
    # confidenceLow signal mirrors the clarifying-questions branch contract.
    assert out.get("confidenceLow") is True
