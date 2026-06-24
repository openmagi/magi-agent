"""Tests for GET /v1/app/customize/runtime-fields (PR-F-UX2 / F8 core).

The endpoint surfaces the wizard's chip-picker menu per (lifecycle,
condition, tool?) tuple. Read-only + fail-open: unknown tuples return
``{fields: [], context, source: 'unknown'}`` rather than 4xx/5xx so the
dashboard silently degrades.

Gated by ``MAGI_CUSTOMIZE_RUNTIME_FIELDS_ENDPOINT_ENABLED`` — strict
default-OFF in the registry; tests set it on per call.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from magi_agent.app import create_app
from magi_agent.config.models import BuildInfo, RuntimeConfig
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime

_TOKEN = "test-gateway-token"
_FLAG = "MAGI_CUSTOMIZE_RUNTIME_FIELDS_ENDPOINT_ENABLED"


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


def _client(*, with_auth: bool = True) -> TestClient:
    client = TestClient(create_app(_runtime()))
    if with_auth:
        client.headers.update({"x-gateway-token": _TOKEN})
    return client


# ---------------------------------------------------------------------------
# auth + flag gating
# ---------------------------------------------------------------------------


def test_runtime_fields_requires_auth(monkeypatch) -> None:
    monkeypatch.setenv(_FLAG, "1")
    client = _client(with_auth=False)
    resp = client.get(
        "/v1/app/customize/runtime-fields",
        params={"lifecycle": "before_tool_use", "condition": "regex"},
    )
    assert resp.status_code == 401


def test_runtime_fields_returns_404_when_flag_off(monkeypatch) -> None:
    monkeypatch.delenv(_FLAG, raising=False)
    client = _client()
    resp = client.get(
        "/v1/app/customize/runtime-fields",
        params={"lifecycle": "before_tool_use", "condition": "regex"},
    )
    # Surface as 404 with a typed error so the dashboard can silently degrade
    # to a chip-less fallback rather than spamming a 500-level alert.
    assert resp.status_code == 404
    assert resp.json()["error"] == "runtime_fields_endpoint_disabled"


# ---------------------------------------------------------------------------
# param validation
# ---------------------------------------------------------------------------


def test_runtime_fields_requires_lifecycle_and_condition(monkeypatch) -> None:
    monkeypatch.setenv(_FLAG, "1")
    client = _client()
    resp = client.get("/v1/app/customize/runtime-fields")
    assert resp.status_code == 400
    assert resp.json()["error"] == "lifecycle_and_condition_required"

    resp = client.get(
        "/v1/app/customize/runtime-fields",
        params={"lifecycle": "before_tool_use"},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# happy path — per (lifecycle, condition) tuple chip list
# ---------------------------------------------------------------------------


def test_before_tool_use_regex_returns_session_turn_tool_chips(monkeypatch) -> None:
    monkeypatch.setenv(_FLAG, "1")
    client = _client()
    resp = client.get(
        "/v1/app/customize/runtime-fields",
        params={"lifecycle": "before_tool_use", "condition": "regex"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["context"] == "before_tool_use/regex"
    assert body["source"] == "fields_for_context"
    names = {f["name"] for f in body["fields"]}
    for required in ("session_id", "turn_id", "tool_name", "tool_use_id"):
        assert required in names, names


def test_before_tool_use_domain_returns_url_alias_chips(monkeypatch) -> None:
    monkeypatch.setenv(_FLAG, "1")
    client = _client()
    resp = client.get(
        "/v1/app/customize/runtime-fields",
        params={"lifecycle": "before_tool_use", "condition": "domain"},
    )
    assert resp.status_code == 200
    names = {f["name"] for f in resp.json()["fields"]}
    for key in ("url", "uri", "href", "link", "address", "endpoint"):
        assert f"tool_input.{key}" in names, key


def test_before_tool_use_path_returns_path_alias_chips(monkeypatch) -> None:
    monkeypatch.setenv(_FLAG, "1")
    client = _client()
    resp = client.get(
        "/v1/app/customize/runtime-fields",
        params={"lifecycle": "before_tool_use", "condition": "path"},
    )
    assert resp.status_code == 200
    names = {f["name"] for f in resp.json()["fields"]}
    for key in ("path", "file", "filename", "filepath", "filePath", "pathRef"):
        assert f"tool_input.{key}" in names, key


def test_after_tool_use_regex_returns_tool_result_chips(monkeypatch) -> None:
    monkeypatch.setenv(_FLAG, "1")
    client = _client()
    resp = client.get(
        "/v1/app/customize/runtime-fields",
        params={"lifecycle": "after_tool_use", "condition": "regex"},
    )
    assert resp.status_code == 200
    names = {f["name"] for f in resp.json()["fields"]}
    assert "tool_result_text" in names
    assert "tool_result_truncated" in names


def test_pre_final_evidence_ref_returns_evidence_field_chips(monkeypatch) -> None:
    monkeypatch.setenv(_FLAG, "1")
    client = _client()
    resp = client.get(
        "/v1/app/customize/runtime-fields",
        params={"lifecycle": "pre_final", "condition": "evidence_ref"},
    )
    assert resp.status_code == 200
    names = {f["name"] for f in resp.json()["fields"]}
    # Real producer fields from _BUILTIN_FIELD_HINTS (TestRun is verified).
    assert "evidence:TestRun.fields.command" in names
    assert "evidence:TestRun.fields.exitCode" in names


def test_pre_final_llm_criterion_returns_final_text_and_turn_summary(
    monkeypatch,
) -> None:
    monkeypatch.setenv(_FLAG, "1")
    client = _client()
    resp = client.get(
        "/v1/app/customize/runtime-fields",
        params={"lifecycle": "pre_final", "condition": "llm_criterion"},
    )
    assert resp.status_code == 200
    names = {f["name"] for f in resp.json()["fields"]}
    assert "final_text" in names
    assert "turn_summary" in names


def test_on_user_prompt_submit_returns_user_prompt_text_chip(monkeypatch) -> None:
    monkeypatch.setenv(_FLAG, "1")
    client = _client()
    resp = client.get(
        "/v1/app/customize/runtime-fields",
        params={
            "lifecycle": "on_user_prompt_submit",
            "condition": "llm_criterion",
        },
    )
    assert resp.status_code == 200
    names = {f["name"] for f in resp.json()["fields"]}
    assert "user_prompt_text" in names


def test_on_subagent_stop_returns_child_final_text_chip(monkeypatch) -> None:
    monkeypatch.setenv(_FLAG, "1")
    client = _client()
    resp = client.get(
        "/v1/app/customize/runtime-fields",
        params={"lifecycle": "on_subagent_stop", "condition": "llm_criterion"},
    )
    assert resp.status_code == 200
    names = {f["name"] for f in resp.json()["fields"]}
    assert "child_final_text" in names


# ---------------------------------------------------------------------------
# tool query param threads through to ToolRegistry.resolve
# ---------------------------------------------------------------------------


def test_tool_param_expands_input_schema_via_real_registry(monkeypatch) -> None:
    """A real tool from the bound registry surfaces its input_schema properties.

    The runtime's tool registry is populated by ``register_core_tool_manifests``
    so we can target a known core tool ("FileRead" — see file_tool_manifests.py)
    and assert its real input_schema keys appear as chips.
    """
    monkeypatch.setenv(_FLAG, "1")
    client = _client()
    resp = client.get(
        "/v1/app/customize/runtime-fields",
        params={
            "lifecycle": "before_tool_use",
            "condition": "regex",
            "tool": "FileRead",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["context"] == "before_tool_use/regex/FileRead"
    names = {f["name"] for f in body["fields"]}
    # FileRead's manifest declares a path-like argument; the chip surfaces it.
    assert any(name.startswith("tool_input.") for name in names), names


# ---------------------------------------------------------------------------
# fail-open contract — unknown (lifecycle, condition) tuples return 200 + empty
# ---------------------------------------------------------------------------


def test_unknown_lifecycle_returns_200_with_empty_fields(monkeypatch) -> None:
    monkeypatch.setenv(_FLAG, "1")
    client = _client()
    resp = client.get(
        "/v1/app/customize/runtime-fields",
        params={"lifecycle": "made_up", "condition": "regex"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["fields"] == []
    assert body["source"] == "unknown"


def test_unknown_condition_returns_200_with_empty_fields(monkeypatch) -> None:
    monkeypatch.setenv(_FLAG, "1")
    client = _client()
    resp = client.get(
        "/v1/app/customize/runtime-fields",
        params={"lifecycle": "before_tool_use", "condition": "made_up"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["fields"] == []


def test_runtime_fields_fails_open_on_derivation_error(monkeypatch) -> None:
    """If ``fields_for_context`` raises, the route returns 200 + empty list."""
    monkeypatch.setenv(_FLAG, "1")

    from magi_agent.customize import runtime_fields as rf_mod

    def _boom(*args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(rf_mod, "fields_for_context", _boom)

    client = _client()
    resp = client.get(
        "/v1/app/customize/runtime-fields",
        params={"lifecycle": "before_tool_use", "condition": "regex"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["fields"] == []
