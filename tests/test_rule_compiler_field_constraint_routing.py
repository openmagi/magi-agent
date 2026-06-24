"""PR-F3 — rule_compiler routing tests for the new ``field_constraint`` kind.

Scope (RED-first, per spec section 5):
  * ``ROUTED_KINDS`` now contains ``field_constraint``.
  * Field-shaped phrases route to ``field_constraint``; complex multi-shape
    phrases fall back to ``shacl_constraint``.
  * ``schema_issues_for("field_constraint", draft)`` accepts a well-formed IR
    and surfaces structural issues on malformed ones.
  * Orchestrator (``compile_with_review``) propagates the ``field_constraint``
    routedKind and runs schema dispatch.

ZERO network, ZERO real model calls.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

import pytest

from magi_agent.customize.rule_compiler import (
    ROUTED_KINDS,
    compile_nl_to_rule,
    compile_with_review,
    schema_issues_for,
)


# ---------------------------------------------------------------------------
# Fake ADK model helpers — mirrors tests/test_rule_compiler.py
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
    response_text: str, *, prompt_capture: list[str] | None = None
) -> object:
    class _FakeModel:
        model = "fake-rule-compiler-model"

        async def generate_content_async(
            self, llm_request: Any, stream: bool = False
        ) -> AsyncGenerator:
            if prompt_capture is not None:
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
    def _factory() -> object:
        return _make_fake_model(response_text, prompt_capture=prompt_capture)

    return _factory


_VALID_REVIEW_RESPONSE = (
    '{"verdict": "aligned", "issues": [], "confidence": 0.9}'
)


# ---------------------------------------------------------------------------
# ROUTED_KINDS enum now includes ``field_constraint``
# ---------------------------------------------------------------------------


def test_routed_kinds_now_includes_field_constraint() -> None:
    assert "field_constraint" in ROUTED_KINDS
    # All five priors stay present — additive, no removal.
    for kind in (
        "deterministic_ref",
        "tool_perm",
        "llm_criterion",
        "shacl_constraint",
        "seam_spec",
        "custom_check",
    ):
        assert kind in ROUTED_KINDS


# ---------------------------------------------------------------------------
# Field-shaped NL routes to ``field_constraint``
# ---------------------------------------------------------------------------


_FIELD_CONSTRAINT_DRAFT_TESTRUN_EXITCODE = {
    "scope": "coding",
    "enabled": True,
    "firesAt": "pre_final",
    "action": "block",
    "what": {
        "kind": "field_constraint",
        "payload": {
            "evidenceType": "TestRun",
            "field": "exitCode",
            "operator": "eq",
            "value": 0,
        },
    },
}


def _field_constraint_response(payload: dict[str, Any]) -> str:
    body = {
        "routedKind": "field_constraint",
        "draft": payload,
        "explanation": "single-record field equality on TestRun.exitCode == 0",
    }
    return f"```json\n{json.dumps(body)}\n```"


@pytest.mark.asyncio
async def test_field_shaped_nl_routes_to_field_constraint() -> None:
    """NL like "exitCode is 0 on every TestRun" must compile as field_constraint."""
    response = _field_constraint_response(_FIELD_CONSTRAINT_DRAFT_TESTRUN_EXITCODE)
    out = await compile_nl_to_rule(
        "Every TestRun must have exitCode 0.",
        model_factory=_factory_for(response),
    )
    assert out["ok"] is True
    assert out["routedKind"] == "field_constraint"
    payload = out["draft"]["what"]["payload"]
    assert payload["evidenceType"] == "TestRun"
    assert payload["field"] == "exitCode"
    assert payload["operator"] == "eq"
    assert payload["value"] == 0


@pytest.mark.asyncio
async def test_complex_multi_shape_nl_falls_back_to_shacl_constraint() -> None:
    """An NL phrase the LLM compiles as raw multi-shape SHACL must still flow
    through the legacy ``shacl_constraint`` kind — field_constraint is the
    preferred path, raw SHACL the escape hatch (section 6 decision)."""
    shacl_payload = {
        "scope": "coding",
        "enabled": True,
        "firesAt": "pre_final",
        "action": "block",
        "what": {
            "kind": "shacl_constraint",
            "payload": {
                "shapeTtl": (
                    "@prefix sh: <http://www.w3.org/ns/shacl#> .\n"
                    "@prefix magi: <https://openmagi.ai/ns/evidence#> .\n"
                    "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .\n"
                    "[] a sh:NodeShape ;\n"
                    "  sh:targetClass magi:Evidence ;\n"
                    "  sh:property [ sh:path magi:field_exitCode ; "
                    "sh:hasValue 0 ; sh:minCount 1 ] ;\n"
                    "  sh:property [ sh:path magi:field_checker ; "
                    "sh:minCount 1 ] .\n"
                ),
            },
        },
    }
    body = {
        "routedKind": "shacl_constraint",
        "draft": shacl_payload,
        "explanation": "multi-shape (TestRun exitCode + CodeDiagnostics checker)",
    }
    out = await compile_nl_to_rule(
        "Multi-evidence constraint across TestRun and CodeDiagnostics.",
        model_factory=_factory_for(f"```json\n{json.dumps(body)}\n```"),
    )
    assert out["ok"] is True
    assert out["routedKind"] == "shacl_constraint"


# ---------------------------------------------------------------------------
# Prompt must mention ``field_constraint`` so the LLM can pick it
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compile_prompt_mentions_field_constraint_in_kind_menu() -> None:
    captured: list[str] = []
    factory = _factory_for(
        _field_constraint_response(_FIELD_CONSTRAINT_DRAFT_TESTRUN_EXITCODE),
        prompt_capture=captured,
    )
    out = await compile_nl_to_rule("any", model_factory=factory)
    assert out["ok"] is True
    assert captured
    prompt = captured[0]
    assert "field_constraint" in prompt, (
        "kind menu must mention field_constraint so the LLM can pick it"
    )


# ---------------------------------------------------------------------------
# schema_issues_for ``field_constraint`` dispatch
# ---------------------------------------------------------------------------


def test_schema_issues_for_field_constraint_accepts_well_formed_ir() -> None:
    issues = schema_issues_for(
        "field_constraint", _FIELD_CONSTRAINT_DRAFT_TESTRUN_EXITCODE
    )
    assert issues == []


def test_schema_issues_for_field_constraint_rejects_unknown_operator() -> None:
    bad = {
        **_FIELD_CONSTRAINT_DRAFT_TESTRUN_EXITCODE,
        "what": {
            "kind": "field_constraint",
            "payload": {
                "evidenceType": "TestRun",
                "field": "exitCode",
                "operator": "approximately",
                "value": 0,
            },
        },
    }
    issues = schema_issues_for("field_constraint", bad)
    assert issues, "unknown operator must surface a schema issue"
    assert any("operator" in i for i in issues)


def test_schema_issues_for_field_constraint_rejects_missing_field() -> None:
    bad = {
        **_FIELD_CONSTRAINT_DRAFT_TESTRUN_EXITCODE,
        "what": {
            "kind": "field_constraint",
            "payload": {
                "evidenceType": "TestRun",
                "operator": "eq",
                "value": 0,
            },
        },
    }
    issues = schema_issues_for("field_constraint", bad)
    assert issues
    assert any("field" in i for i in issues)


def test_schema_issues_for_field_constraint_cross_record_well_formed() -> None:
    """``forEachExistsCovering`` cross-record form passes when both sides
    reference catalog-known evidence types + fields."""
    cross_record_draft = {
        "scope": "coding",
        "enabled": True,
        "firesAt": "pre_final",
        "action": "block",
        "what": {
            "kind": "field_constraint",
            "payload": {
                "operator": "forEachExistsCovering",
                "source": {"evidenceType": "TestRun", "field": "command"},
                "target": {"evidenceType": "TestRun", "field": "exitCode"},
            },
        },
    }
    assert schema_issues_for("field_constraint", cross_record_draft) == []


def test_schema_issues_for_field_constraint_rejects_unknown_field() -> None:
    """A field not in available_fields(evidenceType) must surface as a schema
    issue — the honest-degrade self-check folds the catalog cross-check into
    the deterministic dispatcher so the orchestrator never silently lets a
    vacuous shape escape."""
    bad = {
        **_FIELD_CONSTRAINT_DRAFT_TESTRUN_EXITCODE,
        "what": {
            "kind": "field_constraint",
            "payload": {
                "evidenceType": "TestRun",
                "field": "totallyImaginaryField",
                "operator": "eq",
                "value": 0,
            },
        },
    }
    issues = schema_issues_for("field_constraint", bad)
    assert issues
    assert any("totallyImaginaryField" in i for i in issues)


# ---------------------------------------------------------------------------
# Orchestrator integration — routedKind survives + schemaIssues populated
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_orchestrator_propagates_field_constraint_routed_kind() -> None:
    response = _field_constraint_response(_FIELD_CONSTRAINT_DRAFT_TESTRUN_EXITCODE)
    out = await compile_with_review(
        "Every TestRun must have exitCode 0.",
        compiler_model_factory=_factory_for(response),
        reviewer_model_factory=_factory_for(_VALID_REVIEW_RESPONSE),
    )
    assert out["ok"] is True
    assert out["routedKind"] == "field_constraint"
    assert out["schemaIssues"] == []
