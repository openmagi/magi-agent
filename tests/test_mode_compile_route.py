"""PR-U3.4: HTTP route tests for the NL → agent-mode compile endpoint.

Route: POST /v1/app/modes/compile  (profile-aware default-ON; opt out with
MAGI_CUSTOMIZE_NL_MODE_COMPILER_ENABLED=0). Auth ALWAYS fires before the flag
check: an unauthenticated probe must never reveal flag state.
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


def _factory_for(text: str):
    def _factory() -> object:
        class _Model:
            model = "fake-mode-compiler-model"

            async def generate_content_async(
                self, req: Any, stream: bool = False
            ) -> AsyncGenerator:
                yield _FakeLlmResponse(text)

        return _Model()

    return _factory


_VALID_JSON = json.dumps(
    {
        "displayName": "Careful reviewer",
        "systemPrompt": "Act as a read-only reviewer; cite sources.",
        "toolDelta": {"exclude": [], "include": []},
        "scopedPolicyIds": [],
        "permissionMode": "default",
        "explanation": "A read-only reviewer that cites sources.",
    }
)
_VALID_RESPONSE = f"```json\n{_VALID_JSON}\n```"


def _enable(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE_NL_MODE_COMPILER_ENABLED", "1")


def _client(*, with_token: bool = True) -> TestClient:
    client = TestClient(create_app(_runtime()))
    if with_token:
        client.headers.update({"x-gateway-token": _TOKEN})
    return client


def _disable(monkeypatch) -> None:
    # The flag is profile-aware default-ON, so "disabled" means an explicit
    # opt-out ("0"), which wins in every profile.
    monkeypatch.setenv("MAGI_CUSTOMIZE_NL_MODE_COMPILER_ENABLED", "0")


def test_route_requires_auth_even_when_flag_off(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    _disable(monkeypatch)
    resp = _client(with_token=False).post(
        "/v1/app/modes/compile", json={"nlText": "a reviewer"}
    )
    assert resp.status_code == 401


def test_route_disabled_when_explicitly_opted_out(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    _disable(monkeypatch)
    resp = _client().post("/v1/app/modes/compile", json={"nlText": "a reviewer"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": False, "error": "nl-mode compiler disabled"}


def test_route_default_on_in_full_profile(tmp_path, monkeypatch) -> None:
    # Profile-aware default-ON: with the flag UNSET in the normal/full profile
    # the endpoint is live (no explicit opt-in needed). Verifies the ON path.
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    monkeypatch.setenv("MAGI_RUNTIME_PROFILE", "full")
    monkeypatch.delenv("MAGI_CUSTOMIZE_NL_MODE_COMPILER_ENABLED", raising=False)

    import magi_agent.transport.customize as customize_transport

    monkeypatch.setattr(
        customize_transport,
        "_resolve_nl_mode_compile_factory",
        lambda body: _factory_for(_VALID_RESPONSE),
    )
    resp = _client().post("/v1/app/modes/compile", json={"nlText": "a reviewer"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Not the disabled response: the compiler actually ran.
    assert body["ok"] is True
    assert body["draft"]["displayName"] == "Careful reviewer"


def test_route_rejects_missing_nl_text(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    _enable(monkeypatch)
    resp = _client().post("/v1/app/modes/compile", json={"nope": 1})
    assert resp.status_code == 400
    assert "nlText" in resp.json()["error"]


def test_route_rejects_empty_nl_text(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    _enable(monkeypatch)
    resp = _client().post("/v1/app/modes/compile", json={"nlText": "   "})
    assert resp.status_code == 400


def test_route_returns_mode_draft(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    _enable(monkeypatch)

    import magi_agent.transport.customize as customize_transport

    monkeypatch.setattr(
        customize_transport,
        "_resolve_nl_mode_compile_factory",
        lambda body: _factory_for(_VALID_RESPONSE),
    )
    resp = _client().post(
        "/v1/app/modes/compile",
        json={"nlText": "a careful read-only reviewer that cites sources"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["draft"]["displayName"] == "Careful reviewer"
    assert body["draft"]["permissionMode"] == "default"
    assert body["warnings"] == []
    # The compile route must never ACTIVATE a mode (nor create a user mode).
    # (list_modes may include default-ON built-in posture modes; the invariant
    # is that nothing was activated and no user mode was persisted.)
    listing = _client().get("/v1/app/modes").json()
    assert listing["activeMode"] is None
    assert [m for m in listing["modes"] if not m["id"].startswith("builtin-")] == []


def test_route_fails_open_when_no_model_configured(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
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
    resp = _client().post("/v1/app/modes/compile", json={"nlText": "a reviewer"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body.get("error")
