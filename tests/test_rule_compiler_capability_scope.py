"""PR-F4 — rule_compiler routing tests for the new ``capability_scope`` kind.

Scope (RED-first, per F4 task plan):
  * ``ROUTED_KINDS`` now contains ``capability_scope``.
  * Spawn-scoped NL phrases (e.g. "subagents cannot use shell_exec",
    "researcher subagent must be readonly") route to ``capability_scope``
    with a tightenOnly=true payload.
  * The compile prompt mentions ``capability_scope`` so the LLM can pick it
    AND includes at least one trigger-phrase example.
  * ``schema_issues_for("capability_scope", draft)`` flows through
    ``validate_custom_rule`` — accepts a well-formed draft and surfaces
    payload errors (missing tightenOnly, empty payload, etc.).
  * Orchestrator (``compile_with_review``) propagates the
    ``capability_scope`` routedKind and runs schema dispatch.
  * Ambiguous NL ("limit what subagents do") returns clarifyingQuestions
    instead of inventing a denyTools list.

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
# Canonical capability_scope drafts
# ---------------------------------------------------------------------------


_CAPABILITY_SCOPE_DENY_SHELL = {
    "scope": "always",
    "enabled": True,
    "firesAt": "spawn",
    "action": "block",
    "what": {
        "kind": "capability_scope",
        "payload": {
            "denyTools": ["shell_exec"],
            "tightenOnly": True,
        },
    },
}


_CAPABILITY_SCOPE_READONLY = {
    "scope": "always",
    "enabled": True,
    "firesAt": "spawn",
    "action": "block",
    "what": {
        "kind": "capability_scope",
        "payload": {
            "maxPermissionClass": "readonly",
            "tightenOnly": True,
        },
    },
}


_CAPABILITY_SCOPE_DENY_WRITE_FILE = {
    "scope": "always",
    "enabled": True,
    "firesAt": "spawn",
    "action": "block",
    "what": {
        "kind": "capability_scope",
        "payload": {
            "denyTools": ["write_file"],
            "tightenOnly": True,
        },
    },
}


def _capability_scope_response(draft: dict[str, Any], explanation: str) -> str:
    body = {
        "routedKind": "capability_scope",
        "draft": draft,
        "explanation": explanation,
    }
    return f"```json\n{json.dumps(body)}\n```"


# ---------------------------------------------------------------------------
# ROUTED_KINDS enum now includes ``capability_scope``
# ---------------------------------------------------------------------------


def test_routed_kinds_now_includes_capability_scope() -> None:
    assert "capability_scope" in ROUTED_KINDS
    # All seven priors stay present — additive, no removal.
    for kind in (
        "deterministic_ref",
        "tool_perm",
        "llm_criterion",
        "shacl_constraint",
        "seam_spec",
        "custom_check",
        "field_constraint",
    ):
        assert kind in ROUTED_KINDS


# ---------------------------------------------------------------------------
# Spawn-shaped NL routes to ``capability_scope``
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deny_tool_nl_routes_to_capability_scope_with_deny_tools() -> None:
    """NL like "subagents cannot use shell_exec" must compile as capability_scope
    with denyTools=["shell_exec"] and tightenOnly=true."""
    response = _capability_scope_response(
        _CAPABILITY_SCOPE_DENY_SHELL,
        "Spawned subagents are denied the shell_exec tool.",
    )
    out = await compile_nl_to_rule(
        "Subagents cannot use shell_exec.",
        model_factory=_factory_for(response),
    )
    assert out["ok"] is True
    assert out["routedKind"] == "capability_scope"
    payload = out["draft"]["what"]["payload"]
    assert "shell_exec" in payload["denyTools"]
    assert payload["tightenOnly"] is True


@pytest.mark.asyncio
async def test_readonly_subagent_nl_routes_with_max_permission_class() -> None:
    """NL like "subagents must be readonly" must compile as capability_scope
    with maxPermissionClass="readonly" and tightenOnly=true."""
    response = _capability_scope_response(
        _CAPABILITY_SCOPE_READONLY,
        "Spawned subagents are capped at the readonly permission class.",
    )
    out = await compile_nl_to_rule(
        "Subagents must be readonly.",
        model_factory=_factory_for(response),
    )
    assert out["ok"] is True
    assert out["routedKind"] == "capability_scope"
    payload = out["draft"]["what"]["payload"]
    assert payload["maxPermissionClass"] == "readonly"
    assert payload["tightenOnly"] is True


@pytest.mark.asyncio
async def test_deny_write_file_nl_routes_to_capability_scope() -> None:
    """NL like "subagents are denied write_file" routes as capability_scope."""
    response = _capability_scope_response(
        _CAPABILITY_SCOPE_DENY_WRITE_FILE,
        "Spawned subagents cannot call write_file.",
    )
    out = await compile_nl_to_rule(
        "Subagents are denied write_file.",
        model_factory=_factory_for(response),
    )
    assert out["ok"] is True
    assert out["routedKind"] == "capability_scope"
    assert "write_file" in out["draft"]["what"]["payload"]["denyTools"]


@pytest.mark.asyncio
async def test_ambiguous_subagent_nl_returns_clarifying_questions() -> None:
    """An ambiguous NL like "limit what subagents do" should not invent a
    denyTools list — the compiler must ask which tool / permission class
    to cap.  The fake compiler responds with the clarifying-questions
    JSON envelope; this asserts the rule_compiler honors that envelope."""
    factory = _factory_for(
        '{"questions": ["Which tool should subagents be denied?",'
        ' "Or do you mean cap their permission class instead?"]}'
    )
    out = await compile_nl_to_rule(
        "Limit what subagents can do.",
        model_factory=factory,
    )
    assert out["ok"] is False
    assert out.get("draft") is None
    assert out["clarifyingQuestions"] == (
        "Which tool should subagents be denied?",
        "Or do you mean cap their permission class instead?",
    )
    assert out.get("confidenceLow") is True


# ---------------------------------------------------------------------------
# Prompt must mention ``capability_scope`` so the LLM can pick it
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compile_prompt_mentions_capability_scope_in_kind_menu() -> None:
    captured: list[str] = []
    factory = _factory_for(
        _capability_scope_response(
            _CAPABILITY_SCOPE_DENY_SHELL, "deny shell_exec at spawn"
        ),
        prompt_capture=captured,
    )
    out = await compile_nl_to_rule("any", model_factory=factory)
    assert out["ok"] is True
    assert captured
    prompt = captured[0]
    assert "capability_scope" in prompt, (
        "kind menu must mention capability_scope so the LLM can pick it"
    )


@pytest.mark.asyncio
async def test_compile_prompt_mentions_subagent_trigger_phrasing() -> None:
    """The kind-menu blurb must include at least one subagent-shaped
    example phrase so the LLM understands when to pick the kind."""
    captured: list[str] = []
    factory = _factory_for(
        _capability_scope_response(
            _CAPABILITY_SCOPE_DENY_SHELL, "deny shell_exec at spawn"
        ),
        prompt_capture=captured,
    )
    await compile_nl_to_rule("any", model_factory=factory)
    assert captured
    prompt = captured[0]
    lowered = prompt.lower()
    assert "subagent" in lowered or "spawned" in lowered, (
        "kind menu must include subagent/spawn trigger phrasing"
    )
    assert "tightenonly" in lowered, (
        "kind menu must mention the required tightenOnly field"
    )


# ---------------------------------------------------------------------------
# schema_issues_for ``capability_scope`` dispatch (flows through
# validate_custom_rule per the F4 task plan; capability_scope is already
# registered in custom_rules.KINDS/FIRES_AT/_LEGAL by the sibling
# kind-register-validator task).
# ---------------------------------------------------------------------------


def test_schema_issues_for_capability_scope_accepts_deny_tools_draft() -> None:
    issues = schema_issues_for(
        "capability_scope", _CAPABILITY_SCOPE_DENY_SHELL
    )
    assert issues == []


def test_schema_issues_for_capability_scope_accepts_readonly_draft() -> None:
    issues = schema_issues_for(
        "capability_scope", _CAPABILITY_SCOPE_READONLY
    )
    assert issues == []


def test_schema_issues_for_capability_scope_rejects_missing_tighten_only() -> None:
    bad = {
        **_CAPABILITY_SCOPE_DENY_SHELL,
        "what": {
            "kind": "capability_scope",
            "payload": {"denyTools": ["shell_exec"]},
        },
    }
    issues = schema_issues_for("capability_scope", bad)
    assert issues
    assert any("tightenOnly" in i for i in issues)


def test_schema_issues_for_capability_scope_rejects_widening_attempt() -> None:
    """tightenOnly=false must be rejected — capability_scope can only narrow."""
    bad = {
        **_CAPABILITY_SCOPE_DENY_SHELL,
        "what": {
            "kind": "capability_scope",
            "payload": {
                "denyTools": ["shell_exec"],
                "tightenOnly": False,
            },
        },
    }
    issues = schema_issues_for("capability_scope", bad)
    assert issues
    assert any("tightenOnly" in i for i in issues)


def test_schema_issues_for_capability_scope_rejects_empty_payload() -> None:
    """A rule that sets neither denyTools nor maxPermissionClass is inert."""
    bad = {
        **_CAPABILITY_SCOPE_DENY_SHELL,
        "what": {
            "kind": "capability_scope",
            "payload": {"tightenOnly": True},
        },
    }
    issues = schema_issues_for("capability_scope", bad)
    assert issues
    assert any(
        "denyTools" in i or "maxPermissionClass" in i for i in issues
    )


def test_schema_issues_for_capability_scope_rejects_unknown_permission_class() -> None:
    bad = {
        **_CAPABILITY_SCOPE_DENY_SHELL,
        "what": {
            "kind": "capability_scope",
            "payload": {
                "maxPermissionClass": "admin",  # not in {readonly, safe_write}
                "tightenOnly": True,
            },
        },
    }
    issues = schema_issues_for("capability_scope", bad)
    assert issues
    assert any("maxPermissionClass" in i for i in issues)


def test_schema_issues_for_capability_scope_rejects_wrong_fires_at() -> None:
    """capability_scope must only fire at the spawn slot."""
    bad = {
        **_CAPABILITY_SCOPE_DENY_SHELL,
        "firesAt": "pre_final",
    }
    issues = schema_issues_for("capability_scope", bad)
    assert issues
    assert any(
        "pre_final" in i or "capability_scope" in i or "spawn" in i
        for i in issues
    )


# ---------------------------------------------------------------------------
# Orchestrator integration — routedKind survives + schemaIssues populated
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_orchestrator_propagates_capability_scope_routed_kind() -> None:
    response = _capability_scope_response(
        _CAPABILITY_SCOPE_DENY_SHELL,
        "Spawned subagents are denied shell_exec.",
    )
    out = await compile_with_review(
        "Subagents cannot use shell_exec.",
        compiler_model_factory=_factory_for(response),
        reviewer_model_factory=_factory_for(_VALID_REVIEW_RESPONSE),
    )
    assert out["ok"] is True
    assert out["routedKind"] == "capability_scope"
    assert out["schemaIssues"] == []
    assert out["review"]["verdict"] == "aligned"


@pytest.mark.asyncio
async def test_orchestrator_surfaces_capability_scope_schema_issue() -> None:
    """When the compiled draft is malformed (e.g., missing tightenOnly),
    the orchestrator must surface validate_custom_rule's deterministic
    error through ``schemaIssues`` rather than silently passing it."""
    bad_draft = {
        **_CAPABILITY_SCOPE_DENY_SHELL,
        "what": {
            "kind": "capability_scope",
            "payload": {"denyTools": ["shell_exec"]},
        },
    }
    response = _capability_scope_response(
        bad_draft, "deny shell_exec (missing tightenOnly)"
    )
    out = await compile_with_review(
        "Subagents cannot use shell_exec.",
        compiler_model_factory=_factory_for(response),
        reviewer_model_factory=_factory_for(_VALID_REVIEW_RESPONSE),
    )
    assert out["ok"] is True
    assert out["routedKind"] == "capability_scope"
    assert out["schemaIssues"]
    assert any("tightenOnly" in i for i in out["schemaIssues"])
