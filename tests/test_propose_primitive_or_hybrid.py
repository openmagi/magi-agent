"""PR-F-UX6 — ``propose_primitive_or_hybrid`` LLM step unit tests.

ZERO network. Reuses the fake-ADK-model harness pattern.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

import pytest

from magi_agent.customize.rule_compiler import (
    PROPOSAL_TRUST_CLASSES,
    _parse_proposal,
    propose_primitive_or_hybrid,
)


# ---------------------------------------------------------------------------
# Fake-model harness
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


def _factory(response_text: str):
    def _f() -> object:
        class _Model:
            model = "fake-proposal-model"

            async def generate_content_async(
                self, req: Any, stream: bool = False
            ) -> AsyncGenerator:
                yield _FakeLlmResponse(response_text)

        return _Model()

    return _f


# ---------------------------------------------------------------------------
# Vocab sanity
# ---------------------------------------------------------------------------


def test_proposal_trust_classes_is_two_canonical() -> None:
    assert PROPOSAL_TRUST_CLASSES == frozenset({"deterministic", "advisory", "mutator"})


# ---------------------------------------------------------------------------
# Parser — single + hybrid + reject paths
# ---------------------------------------------------------------------------


_SINGLE_PRIMITIVE_JSON = json.dumps(
    {
        "mode": "single",
        "primitives": [
            {
                "kind": "tool_perm",
                "payload": {
                    "scope": "always",
                    "enabled": True,
                    "firesAt": "before_tool_use",
                    "action": "block",
                    "what": {
                        "kind": "tool_perm",
                        "payload": {
                            "match": {"tool": "shell_exec"},
                            "decision": "deny",
                        },
                    },
                },
                "trustClass": "deterministic",
                "rationale": "shell_exec is unsafe; deny deterministically",
            }
        ],
        "summary": "Deny shell_exec before invocation",
        "explanation": "Single deterministic gate — no critic value-add.",
    }
)


_HYBRID_JSON = json.dumps(
    {
        "mode": "hybrid",
        "primitives": [
            {
                "kind": "llm_criterion",
                "payload": {
                    "scope": "always",
                    "enabled": True,
                    "firesAt": "after_tool_use",
                    "action": "override",
                    "what": {
                        "kind": "llm_criterion",
                        "payload": {
                            "toolMatch": ["FileRead", "shell_exec"],
                            "contentMatch": {
                                "pattern": "AKIA[0-9A-Z]{16}",
                                "isRegex": True,
                            },
                            "criterion": "Is this a real AWS key or a fixture?",
                        },
                    },
                },
                "trustClass": "advisory",
                "rationale": "Regex narrows critic to positives.",
            },
            {
                "kind": "custom_check",
                "payload": {
                    "id": "secret-audit",
                    "label": "AWS Secret",
                    "scope": "always",
                    "enabled": True,
                    "trigger": {
                        "tool": "FileRead",
                        "match": {
                            "pattern": "AKIA[0-9A-Z]{16}",
                            "isRegex": True,
                        },
                    },
                    "action": "audit",
                },
                "trustClass": "deterministic",
                "rationale": "Cheap pre-filter + audit record.",
            },
        ],
        "summary": "Audit AWS keys: regex pre-filter + LLM critic on hits",
        "explanation": "Hybrid — deterministic pre-filter avoids critic on every tool.",
    }
)


def test_parse_proposal_accepts_single_mode_with_one_primitive() -> None:
    proposal = _parse_proposal(_SINGLE_PRIMITIVE_JSON)
    assert proposal is not None
    assert proposal["mode"] == "single"
    assert len(proposal["primitives"]) == 1
    assert proposal["primitives"][0]["kind"] == "tool_perm"
    assert proposal["primitives"][0]["trustClass"] == "deterministic"


def test_parse_proposal_accepts_hybrid_with_two_primitives() -> None:
    proposal = _parse_proposal(_HYBRID_JSON)
    assert proposal is not None
    assert proposal["mode"] == "hybrid"
    assert len(proposal["primitives"]) == 2
    trust = {p["trustClass"] for p in proposal["primitives"]}
    assert trust == {"deterministic", "advisory"}


def test_parse_proposal_rejects_single_mode_with_zero_primitives() -> None:
    raw = json.dumps({"mode": "single", "primitives": []})
    assert _parse_proposal(raw) is None


def test_parse_proposal_rejects_single_mode_with_multiple_primitives() -> None:
    payload = json.loads(_HYBRID_JSON)
    payload["mode"] = "single"
    assert _parse_proposal(json.dumps(payload)) is None


def test_parse_proposal_rejects_hybrid_with_one_primitive() -> None:
    payload = json.loads(_HYBRID_JSON)
    payload["primitives"] = payload["primitives"][:1]
    assert _parse_proposal(json.dumps(payload)) is None


def test_parse_proposal_rejects_unknown_kind() -> None:
    payload = json.loads(_SINGLE_PRIMITIVE_JSON)
    payload["primitives"][0]["kind"] = "definitely_not_a_routed_kind"
    assert _parse_proposal(json.dumps(payload)) is None


def test_parse_proposal_rejects_bad_trust_class() -> None:
    payload = json.loads(_SINGLE_PRIMITIVE_JSON)
    payload["primitives"][0]["trustClass"] = "magical"
    assert _parse_proposal(json.dumps(payload)) is None


def test_parse_proposal_rejects_non_dict_root() -> None:
    assert _parse_proposal("[]") is None
    assert _parse_proposal("not json") is None


# ---------------------------------------------------------------------------
# propose_primitive_or_hybrid contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_propose_returns_ok_proposal_on_success() -> None:
    intent = {
        "whatToCheck": "audit AWS keys",
        "whereInLifecycle": "after_tool_use",
        "whatToDoOnFail": "block",
    }
    result = await propose_primitive_or_hybrid(
        intent, model_factory=_factory(_HYBRID_JSON)
    )
    assert result["ok"] is True
    assert result["proposal"]["mode"] == "hybrid"


@pytest.mark.asyncio
async def test_propose_fails_open_on_none_factory() -> None:
    result = await propose_primitive_or_hybrid({"x": 1}, model_factory=None)
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_propose_returns_error_on_unparseable_response() -> None:
    result = await propose_primitive_or_hybrid(
        {"x": 1}, model_factory=_factory("totally not json")
    )
    assert result["ok"] is False
    assert "unparseable" in result["error"]
