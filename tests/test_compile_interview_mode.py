"""PR-F-UX6 — end-to-end interview-mode flow through the HTTP route.

Walks the full architect loop:
  - underspecified input → ``mode="interview"`` + questions[]
  - subsequent turn with priorTurns → ``mode="proposal"`` (composed primitives)
  - flag OFF → legacy one-shot ``compile_with_review`` path unchanged

ZERO network. Reuses the FakeModel sequencing pattern from
``tests/test_rule_compile_route.py``.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from magi_agent.app import create_app
from magi_agent.config.models import BuildInfo, RuntimeConfig
from magi_agent.customize.rule_compiler import (
    compile_interview_step,
)
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime


_TOKEN = "test-gateway-token"


def _runtime() -> OpenMagiRuntime:
    return OpenMagiRuntime(
        config=RuntimeConfig(
            bot_id="local-bot",
            user_id="local-user",
            gateway_token=_TOKEN,
            api_proxy_url="http://api-proxy.local",
            chat_proxy_url="http://chat-proxy.local",
            redis_url="redis://redis.local:6379/0",
            model="gpt-5.2",
            build=BuildInfo(version="0.1.0", build_sha="sha-test"),
        )
    )


class _FakePart:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeContent:
    def __init__(self, text: str) -> None:
        self.parts = [_FakePart(text)]


class _FakeLlmResponse:
    def __init__(self, text: str) -> None:
        self.content = _FakeContent(text)


def _factory_seq(*responses: str):
    call_idx = [0]

    def _factory() -> object:
        idx = call_idx[0]
        call_idx[0] += 1
        text = responses[idx] if idx < len(responses) else responses[-1]

        class _Model:
            model = "fake-interview-model"

            async def generate_content_async(
                self, req: Any, stream: bool = False
            ) -> AsyncGenerator:
                yield _FakeLlmResponse(text)

        return _Model()

    return _factory


# ---------------------------------------------------------------------------
# Canned responses — one interview turn, one resolved turn
# ---------------------------------------------------------------------------


_INTERVIEW_TURN_RESPONSE = json.dumps(
    {
        "whatToCheck": "audit AWS keys",
        "whereInLifecycle": "unknown",
        "whatToDoOnFail": "unknown",
        "openQuestions": [
            {
                "question": "Which tool's output should we scan?",
                "expects": "tool_name",
                "inventory": ["FileRead", "shell_exec"],
            }
        ],
        "confidence": 0.4,
    }
)


_RESOLVED_INTENT_RESPONSE = json.dumps(
    {
        "whatToCheck": "audit AWS keys in FileRead output",
        "whereInLifecycle": "after_tool_use",
        "whatToDoOnFail": "block",
        "openQuestions": [],
        "confidence": 0.9,
    }
)


_HYBRID_PROPOSAL_RESPONSE = json.dumps(
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
                            "toolMatch": ["FileRead"],
                            "contentMatch": {
                                "pattern": "AKIA[0-9A-Z]{16}",
                                "isRegex": True,
                            },
                            "criterion": "Is this a real AWS key?",
                        },
                    },
                },
                "trustClass": "advisory",
                "rationale": "Regex pre-filter narrows critic invocation.",
            },
            {
                "kind": "custom_check",
                "payload": {
                    "id": "aws-audit",
                    "label": "AWS key audit",
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
                "rationale": "Cheap pre-filter records an audit row.",
            },
        ],
        "summary": "Audit AWS keys: regex + LLM critic composed",
        "explanation": "Hybrid lets the deterministic filter narrow the critic.",
    }
)


_VALID_TOOL_PERM_JSON = json.dumps(
    {
        "routedKind": "tool_perm",
        "draft": {
            "scope": "always",
            "enabled": True,
            "firesAt": "before_tool_use",
            "action": "block",
            "what": {
                "kind": "tool_perm",
                "payload": {"match": {"tool": "shell_exec"}, "decision": "deny"},
            },
        },
        "explanation": "Deny shell_exec before invocation.",
    }
)


_VALID_REVIEW_RESPONSE = (
    '{"verdict": "aligned", "issues": [], "confidence": 0.9}'
)


# ---------------------------------------------------------------------------
# compile_interview_step unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_interview_step_returns_questions_for_underspecified_input() -> None:
    factory = _factory_seq(_INTERVIEW_TURN_RESPONSE)
    result = await compile_interview_step(
        "audit AWS keys",
        compiler_model_factory=factory,
        reviewer_model_factory=lambda: factory(),
    )
    assert result["ok"] is True
    assert result["mode"] == "interview"
    assert len(result["questions"]) == 1
    assert result["questions"][0]["expects"] == "tool_name"


@pytest.mark.asyncio
async def test_interview_step_returns_proposal_when_intent_resolved() -> None:
    # First call (discover_intent) returns resolved intent; second call
    # (propose_primitive_or_hybrid) returns the hybrid proposal.
    compile_factory = _factory_seq(_RESOLVED_INTENT_RESPONSE)
    propose_factory = _factory_seq(_HYBRID_PROPOSAL_RESPONSE)
    result = await compile_interview_step(
        "audit AWS keys",
        compiler_model_factory=compile_factory,
        reviewer_model_factory=propose_factory,
        force_interview=True,
    )
    assert result["ok"] is True, result
    assert result["mode"] == "proposal"
    assert result["proposal"]["mode"] == "hybrid"
    assert len(result["proposal"]["primitives"]) == 2


@pytest.mark.asyncio
async def test_interview_step_routes_well_formed_input_to_legacy() -> None:
    # Long enough that ``_looks_underspecified`` returns False → legacy path.
    well_formed = (
        "Deny the shell_exec tool whenever the agent attempts to invoke it "
        "without first emitting evidence:test-run on this coding turn."
    )
    compile_factory = _factory_seq(_VALID_TOOL_PERM_JSON)
    review_factory = _factory_seq(_VALID_REVIEW_RESPONSE)
    result = await compile_interview_step(
        well_formed,
        compiler_model_factory=compile_factory,
        reviewer_model_factory=review_factory,
    )
    assert result["mode"] == "compile"
    assert result.get("ok") is True
    assert result["routedKind"] == "tool_perm"


# ---------------------------------------------------------------------------
# HTTP route — flag OFF preserves legacy contract
# ---------------------------------------------------------------------------


def _client() -> TestClient:
    runtime = _runtime()
    client = TestClient(create_app(runtime))
    client.headers.update({"x-gateway-token": _TOKEN})
    return client


def test_route_flag_off_preserves_legacy_path(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    monkeypatch.setenv("MAGI_CUSTOMIZE_NL_RULE_COMPILER_ENABLED", "1")
    monkeypatch.delenv("MAGI_CUSTOMIZE_NL_INTERVIEW_MODE_ENABLED", raising=False)

    factory = _factory_seq(_VALID_TOOL_PERM_JSON, _VALID_REVIEW_RESPONSE)
    import magi_agent.transport.customize as customize_transport

    monkeypatch.setattr(
        customize_transport,
        "_resolve_nl_rule_compile_factory",
        lambda body: factory,
    )

    resp = _client().post(
        "/v1/app/customize/rules/compile",
        json={"nlText": "deny shell_exec"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Legacy success shape — no ``mode`` key.
    assert body["ok"] is True
    assert "mode" not in body
    assert body["routedKind"] == "tool_perm"


# ---------------------------------------------------------------------------
# HTTP route — flag ON, underspecified input → interview turn
# ---------------------------------------------------------------------------


def test_route_flag_on_returns_interview_questions(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    monkeypatch.setenv("MAGI_CUSTOMIZE_NL_RULE_COMPILER_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_NL_INTERVIEW_MODE_ENABLED", "1")

    factory = _factory_seq(_INTERVIEW_TURN_RESPONSE)
    import magi_agent.transport.customize as customize_transport

    monkeypatch.setattr(
        customize_transport,
        "_resolve_nl_rule_compile_factory",
        lambda body: factory,
    )

    resp = _client().post(
        "/v1/app/customize/rules/compile",
        json={"nlText": "audit AWS keys"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "interview"
    assert isinstance(body["questions"], list) and body["questions"]
    assert body["questions"][0]["expects"] == "tool_name"
    assert body["intent"]["whatToCheck"] == "audit AWS keys"


# ---------------------------------------------------------------------------
# HTTP route — flag ON, resolved → proposal
# ---------------------------------------------------------------------------


def test_route_flag_on_returns_hybrid_proposal(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    monkeypatch.setenv("MAGI_CUSTOMIZE_NL_RULE_COMPILER_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_NL_INTERVIEW_MODE_ENABLED", "1")

    # First call → resolved intent; second call → hybrid proposal.
    factory = _factory_seq(_RESOLVED_INTENT_RESPONSE, _HYBRID_PROPOSAL_RESPONSE)
    import magi_agent.transport.customize as customize_transport

    monkeypatch.setattr(
        customize_transport,
        "_resolve_nl_rule_compile_factory",
        lambda body: factory,
    )

    # Use ``mode=interview`` to force the interview path even for a
    # well-formed input — the test isolates the proposal-emission branch.
    resp = _client().post(
        "/v1/app/customize/rules/compile",
        json={
            "nlText": "audit AWS keys after FileRead",
            "mode": "interview",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "proposal"
    assert body["proposal"]["mode"] == "hybrid"
    assert len(body["proposal"]["primitives"]) == 2
    trust = {p["trustClass"] for p in body["proposal"]["primitives"]}
    assert trust == {"deterministic", "advisory"}
