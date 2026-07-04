"""Tests for the NL policy compile endpoints under /v1/app/policies/compile."""
from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from magi_agent.app import create_app
from magi_agent.config.models import BuildInfo, RuntimeConfig
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime

_TOKEN = "test-gateway-token"

_PARAMS = {
    "intent": "require a credible source before trading",
    "gatedTool": "execute_trade",
    "fetchTool": "web_fetch",
    "allowlistDomains": ["sec.gov"],
    "evidenceLabel": "source credibility",
    "onUnavailable": "deny",
}


class _FakeLlmResponse:
    def __init__(self, text: str) -> None:
        self.content = type("C", (), {"parts": [type("P", (), {"text": text})()]})()


def _factory(response_text: str):
    class _FakeModel:
        model = "fake"

        async def generate_content_async(self, _req: Any, stream: bool = False) -> AsyncGenerator:
            yield _FakeLlmResponse(response_text)

    return lambda: _FakeModel()


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


def _authed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    client = TestClient(create_app(_runtime()))
    client.headers.update({"x-gateway-token": _TOKEN})
    return client


def _patch_factory(monkeypatch: pytest.MonkeyPatch, response_text: str) -> None:
    """Inject a fake model via the resolver (a function can't ride an HTTP JSON body)."""
    monkeypatch.setattr(
        "magi_agent.transport.customize._resolve_policy_compile_factory",
        lambda body: _factory(response_text),
    )


# --- one-shot /v1/app/policies/compile ---


def test_compile_requires_auth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    client = TestClient(create_app(_runtime()))
    assert client.post("/v1/app/policies/compile", json={"nlText": "x"}).status_code == 401


def test_compile_missing_nltext(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _authed(tmp_path, monkeypatch)
    resp = client.post("/v1/app/policies/compile", json={})
    assert resp.status_code == 400
    assert resp.json()["error"] == "nlText_required"


def test_compile_returns_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _authed(tmp_path, monkeypatch)
    _patch_factory(monkeypatch, json.dumps(_PARAMS))
    resp = client.post("/v1/app/policies/compile", json={"nlText": "verify source before trading"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["plan"]["gate"]["what"]["payload"]["match"]["tool"] == "execute_trade"
    assert body["plan"]["binding"]["evidenceType"] == "custom:SourceCredibility"


def test_compile_not_applicable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _authed(tmp_path, monkeypatch)
    _patch_factory(monkeypatch, json.dumps({"notApplicable": True, "reason": "single check"}))
    resp = client.post("/v1/app/policies/compile", json={"nlText": "block ssn"})
    assert resp.json()["ok"] is False
    assert resp.json()["notApplicable"] is True


# --- multi-turn /v1/app/policies/compile/interactive ---


def test_interactive_first_turn_asks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _authed(tmp_path, monkeypatch)
    _patch_factory(monkeypatch, json.dumps({"assistant_message": "Which tool?", "param_updates": {}}))
    resp = client.post(
        "/v1/app/policies/compile/interactive",
        json={
            "history": [{"role": "user", "content": "gate a tool on a verified source"}],
            "paramsSoFar": {},
            "answers": {},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ready_to_save"] is False
    assert body["plan"] is None
    assert body["missing_params"]


def test_interactive_converges_to_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _authed(tmp_path, monkeypatch)
    _patch_factory(monkeypatch, json.dumps({"param_updates": {}}))
    resp = client.post(
        "/v1/app/policies/compile/interactive",
        json={
            "history": [],
            "paramsSoFar": {},
            "answers": {
                "gatedTool": "execute_trade",
                "evidenceLabel": "source credibility",
                "allowlistDomains": "sec.gov",
            },
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ready_to_save"] is True
    assert body["plan"]["gate"]["what"]["payload"]["match"]["tool"] == "execute_trade"


def test_interactive_structural_violation_422(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _authed(tmp_path, monkeypatch)
    _patch_factory(monkeypatch, "{}")
    resp = client.post(
        "/v1/app/policies/compile/interactive",
        json={"history": "not-a-list"},
    )
    assert resp.status_code == 422
