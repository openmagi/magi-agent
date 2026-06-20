"""PR-C2 — HTTP route tests for the SeamSpec NL compile + persist endpoints.

Routes covered:
  POST   /v1/app/customize/seams/compile     — NL → SeamSpec (gated)
  PUT    /v1/app/customize/seams             — persist approved SeamSpec
  DELETE /v1/app/customize/seams/{id}        — remove a persisted SeamSpec

All three are gated behind ``MAGI_CUSTOMIZE_SEAM_SPEC_ENABLED``. OFF →
``{ok: False, error: "seam-spec compiler disabled"}`` (auth still runs
first; an unauthenticated probe must never reveal flag state).
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from magi_agent.app import create_app
from magi_agent.config.models import BuildInfo, RuntimeConfig
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime


_TOKEN = "test-gateway-token"


def _runtime(tmp_path=None, *, gateway_token: str = _TOKEN) -> OpenMagiRuntime:
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


# ---------------------------------------------------------------------------
# Fake ADK model — same shape as tests/test_seam_compiler.py
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


def _factory_seq(*responses: str):
    """Sequence-yielding factory so compile + review get distinct canned text."""
    call_idx = [0]

    def _factory() -> object:
        idx = call_idx[0]
        call_idx[0] += 1
        text = responses[idx] if idx < len(responses) else responses[-1]

        class _Model:
            model = "fake-seam-compiler-model"

            async def generate_content_async(
                self, req: Any, stream: bool = False
            ) -> AsyncGenerator:
                yield _FakeLlmResponse(text)

        return _Model()

    return _factory


_VALID_SPEC_JSON = json.dumps(
    {
        "spec_version": "0.1",
        "actions": [
            {"op": "modify_seam", "preset_id": "coding-verification", "wiring": "opt_in"}
        ],
    }
)
_VALID_SPEC_RESPONSE = f"```json\n{_VALID_SPEC_JSON}\n```"
_VALID_REVIEW_RESPONSE = (
    '{"verdict": "aligned", "issues": [], "confidence": 0.9}'
)


def _enable(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE_SEAM_SPEC_ENABLED", "1")


def _disable(monkeypatch) -> None:
    monkeypatch.delenv("MAGI_CUSTOMIZE_SEAM_SPEC_ENABLED", raising=False)


def _client(tmp_path, *, with_token: bool = True) -> TestClient:
    runtime = _runtime(tmp_path)
    client = TestClient(create_app(runtime))
    if with_token:
        client.headers.update({"x-gateway-token": _TOKEN})
    return client


# ---------------------------------------------------------------------------
# Auth always fires before flag check
# ---------------------------------------------------------------------------


def test_compile_route_requires_auth_even_when_flag_off(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    _disable(monkeypatch)
    client = _client(tmp_path, with_token=False)
    resp = client.post(
        "/v1/app/customize/seams/compile", json={"nlText": "anything"}
    )
    assert resp.status_code == 401


def test_put_route_requires_auth_even_when_flag_off(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    _disable(monkeypatch)
    client = _client(tmp_path, with_token=False)
    resp = client.put("/v1/app/customize/seams", json={"actions": []})
    assert resp.status_code == 401


def test_delete_route_requires_auth_even_when_flag_off(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    _disable(monkeypatch)
    client = _client(tmp_path, with_token=False)
    resp = client.delete("/v1/app/customize/seams/seam_x")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Flag-OFF returns "disabled" (auth has already passed at this point)
# ---------------------------------------------------------------------------


def test_compile_route_disabled_when_flag_off(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    _disable(monkeypatch)
    resp = _client(tmp_path).post(
        "/v1/app/customize/seams/compile", json={"nlText": "anything"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"ok": False, "error": "seam-spec compiler disabled"}


def test_put_route_disabled_when_flag_off(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    _disable(monkeypatch)
    resp = _client(tmp_path).put(
        "/v1/app/customize/seams", json={"spec_version": "0.1", "actions": []}
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": False, "error": "seam-spec compiler disabled"}


def test_delete_route_disabled_when_flag_off(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    _disable(monkeypatch)
    resp = _client(tmp_path).delete("/v1/app/customize/seams/seam_x")
    assert resp.status_code == 200
    assert resp.json() == {"ok": False, "error": "seam-spec compiler disabled"}


# ---------------------------------------------------------------------------
# Compile route — body validation + success/failure paths
# ---------------------------------------------------------------------------


def test_compile_route_rejects_missing_nl_text(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    _enable(monkeypatch)
    resp = _client(tmp_path).post(
        "/v1/app/customize/seams/compile", json={"nope": 1}
    )
    assert resp.status_code == 400
    assert "nlText" in resp.json()["error"]


def test_compile_route_rejects_empty_nl_text(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    _enable(monkeypatch)
    resp = _client(tmp_path).post(
        "/v1/app/customize/seams/compile", json={"nlText": "   "}
    )
    assert resp.status_code == 400
    assert "must not be empty" in resp.json()["error"]


def test_compile_route_returns_ok_false_when_no_model_configured(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    # Hermetic: point MAGI_CONFIG at an empty tmp path so the real
    # ~/.magi/config.toml is not consulted, and scrub provider env keys so
    # ``resolve_provider_config()`` returns None and the route MUST fail
    # open. Without this scrub the developer's real Anthropic/OpenAI key
    # would let the route actually call the model — defeating the test.
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
    resp = _client(tmp_path).post(
        "/v1/app/customize/seams/compile", json={"nlText": "any policy"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    # Fail-open: any of "unavailable" / generic compile error is acceptable.
    assert body.get("error")


def test_compile_route_returns_spec_review_and_schema_issues_with_injected_factory(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    _enable(monkeypatch)
    # Inject the fake factory by monkeypatching the module-level resolver —
    # same pattern as the SHACL compile route tests. The factory must be
    # distinct callables for compiler vs reviewer (orchestrator guard).
    factory = _factory_seq(_VALID_SPEC_RESPONSE, _VALID_REVIEW_RESPONSE)
    import magi_agent.transport.customize as customize_transport

    monkeypatch.setattr(
        customize_transport,
        "_resolve_seam_compile_factory",
        lambda body: factory,
    )
    resp = _client(tmp_path).post(
        "/v1/app/customize/seams/compile",
        json={"nlText": "flip coding-verification to opt-in"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["spec"]["actions"][0]["preset_id"] == "coding-verification"
    assert body["spec"]["actions"][0]["wiring"] == "opt_in"
    assert body["review"]["verdict"] == "aligned"
    assert body["schemaIssues"] == []


# ---------------------------------------------------------------------------
# PUT route — validates body, refuses to persist invalid specs
# ---------------------------------------------------------------------------


def test_put_route_persists_valid_spec_and_assigns_id(tmp_path, monkeypatch):
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    _enable(monkeypatch)
    resp = _client(tmp_path).put(
        "/v1/app/customize/seams",
        json={
            "spec_version": "0.1",
            "actions": [
                {
                    "op": "modify_seam",
                    "preset_id": "coding-verification",
                    "wiring": "opt_in",
                }
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    spec_id = body["id"]
    assert spec_id.startswith("seam_")
    # Persisted to disk.
    persisted = json.loads(cfile.read_text())["verification"]["seam_specs"]
    assert len(persisted) == 1
    assert persisted[0]["id"] == spec_id


def test_put_route_rejects_invalid_spec_with_schema_issues(tmp_path, monkeypatch):
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    _enable(monkeypatch)
    resp = _client(tmp_path).put(
        "/v1/app/customize/seams",
        json={
            "spec_version": "0.1",
            "actions": [{"op": "modify_seam", "preset_id": "does-not-exist"}],
        },
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["ok"] is False
    assert any("not a builtin seam" in i for i in body["schemaIssues"])
    # Nothing persisted.
    assert not cfile.exists() or json.loads(cfile.read_text())[
        "verification"
    ]["seam_specs"] == []


def test_put_route_rejects_malformed_json_body(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    _enable(monkeypatch)
    client = _client(tmp_path)
    resp = client.put(
        "/v1/app/customize/seams",
        data="not json",
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# DELETE route — removes only the target id
# ---------------------------------------------------------------------------


def test_delete_route_removes_only_target_id(tmp_path, monkeypatch):
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    _enable(monkeypatch)
    client = _client(tmp_path)
    # Persist two specs.
    spec_id_a = client.put(
        "/v1/app/customize/seams",
        json={
            "spec_version": "0.1",
            "actions": [
                {"op": "modify_seam", "preset_id": "coding-verification", "wiring": "opt_in"}
            ],
        },
    ).json()["id"]
    spec_id_b = client.put(
        "/v1/app/customize/seams",
        json={
            "spec_version": "0.1",
            "actions": [
                {"op": "modify_seam", "preset_id": "fact-grounding", "wiring": "opt_out"}
            ],
        },
    ).json()["id"]
    # Delete one.
    resp = client.delete(f"/v1/app/customize/seams/{spec_id_a}")
    assert resp.status_code == 200
    remaining = [s["id"] for s in resp.json()["overrides"]["verification"]["seam_specs"]]
    assert remaining == [spec_id_b]
