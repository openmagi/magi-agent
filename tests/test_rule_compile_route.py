"""PR-D1 — HTTP route tests for the unified NL → rule compile endpoint.

Route: POST /v1/app/customize/rules/compile  (default-OFF gated by
MAGI_CUSTOMIZE_NL_RULE_COMPILER_ENABLED). Auth ALWAYS fires before the
flag check — an unauthenticated probe must never reveal flag state.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

from fastapi.testclient import TestClient

from magi_agent.app import create_app
from magi_agent.config.models import BuildInfo, RuntimeConfig
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime


_TOKEN = "test-gateway-token"


def _runtime(*, gateway_token: str = _TOKEN) -> OpenMagiRuntime:
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
            model = "fake-rule-compiler-model"

            async def generate_content_async(
                self, req: Any, stream: bool = False
            ) -> AsyncGenerator:
                yield _FakeLlmResponse(text)

        return _Model()

    return _factory


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
        "explanation": "Deny shell_exec before the agent calls it.",
    }
)
_VALID_TOOL_PERM_RESPONSE = f"```json\n{_VALID_TOOL_PERM_JSON}\n```"
_VALID_REVIEW_RESPONSE = (
    '{"verdict": "aligned", "issues": [], "confidence": 0.9}'
)


def _enable(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE_NL_RULE_COMPILER_ENABLED", "1")


def _client(*, with_token: bool = True) -> TestClient:
    runtime = _runtime()
    client = TestClient(create_app(runtime))
    if with_token:
        client.headers.update({"x-gateway-token": _TOKEN})
    return client


# ---------------------------------------------------------------------------
# Auth fires before flag
# ---------------------------------------------------------------------------


def test_route_requires_auth_even_when_flag_off(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    monkeypatch.delenv("MAGI_CUSTOMIZE_NL_RULE_COMPILER_ENABLED", raising=False)
    client = _client(with_token=False)
    resp = client.post(
        "/v1/app/customize/rules/compile", json={"nlText": "any policy"}
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Flag-OFF returns "disabled" (auth has passed)
# ---------------------------------------------------------------------------


def test_route_disabled_when_flag_off(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    monkeypatch.delenv("MAGI_CUSTOMIZE_NL_RULE_COMPILER_ENABLED", raising=False)
    resp = _client().post(
        "/v1/app/customize/rules/compile", json={"nlText": "any policy"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"ok": False, "error": "nl-rule compiler disabled"}


# ---------------------------------------------------------------------------
# Body validation
# ---------------------------------------------------------------------------


def test_route_rejects_missing_nl_text(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    _enable(monkeypatch)
    resp = _client().post("/v1/app/customize/rules/compile", json={"nope": 1})
    assert resp.status_code == 400
    assert "nlText" in resp.json()["error"]


def test_route_rejects_empty_nl_text(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    _enable(monkeypatch)
    resp = _client().post(
        "/v1/app/customize/rules/compile", json={"nlText": "   "}
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Success + hermetic fail-open
# ---------------------------------------------------------------------------


def test_route_returns_routed_draft_review_and_schema_issues(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    _enable(monkeypatch)

    factory = _factory_seq(_VALID_TOOL_PERM_RESPONSE, _VALID_REVIEW_RESPONSE)
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
    assert body["ok"] is True
    assert body["routedKind"] == "tool_perm"
    assert body["draft"]["what"]["payload"]["match"]["tool"] == "shell_exec"
    assert body["review"]["verdict"] == "aligned"
    assert body["schemaIssues"] == []
    assert "shell_exec" in body["explanation"]


def test_route_fails_open_when_no_model_configured(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    # Hermetic: scrub provider keys + config so the resolver returns None.
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "empty-config.toml"))
    for env in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GOOGLE_API_KEY",
        "FIREWORKS_API_KEY",
        "OPENROUTER_API_KEY",
        "MAGI_SHACL_COMPILER_MODEL",
        "MAGI_EGRESS_CRITIC_MODEL",
        "MAGI_LLM_PROVIDER",
    ):
        monkeypatch.delenv(env, raising=False)
    _enable(monkeypatch)
    resp = _client().post(
        "/v1/app/customize/rules/compile", json={"nlText": "any policy"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body.get("error")
