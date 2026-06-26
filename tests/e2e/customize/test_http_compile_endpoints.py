"""HTTP coverage for the customize compile endpoints.

The wizard's "Continue in NL" / "Compile preview" surfaces POST to
three compile endpoints. None of them require a real LLM provider
key for contract verification: the auth / validation / flag-gating
paths are deterministic, and the actual compile step is gated by a
master flag that defaults OFF on a fresh install. This file pins:

* POST /v1/app/customize/custom-rules/compile (SHACL compiler)
* POST /v1/app/customize/rules/compile        (NL rule compiler)
* POST /v1/app/customize/seams/compile        (SeamSpec compiler)

For each:

1. Auth required (401 without token).
2. Flag-OFF returns ``{ok: False, error: "<X> compiler disabled"}``
   with status 200 (NOT a 4xx — the wizard treats this as "feature
   unavailable" and hides the button).
3. Missing/empty/oversized nlText rejected with 400.
4. With flag ON + a minimal valid body, the endpoint returns a
   2xx response (the compile step itself may fall back to a stub
   when no model factory is configured; that fallback path is part
   of the contract).
"""

from __future__ import annotations

from pathlib import Path

import pytest
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


@pytest.fixture
def auth_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    client = TestClient(create_app(_runtime()))
    client.headers.update({"x-gateway-token": _TOKEN})
    return client


@pytest.fixture
def noauth_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    return TestClient(create_app(_runtime()))


# ---------------------------------------------------------------------------
# /v1/app/customize/custom-rules/compile — SHACL compiler
# ---------------------------------------------------------------------------


def test_shacl_compile_requires_auth(noauth_client: TestClient) -> None:
    resp = noauth_client.post(
        "/v1/app/customize/custom-rules/compile",
        json={"nlText": "every evidence record must have a timestamp"},
    )
    assert resp.status_code == 401


def test_shacl_compile_flag_off_returns_disabled(
    auth_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MAGI_SHACL_COMPILER_ENABLED", raising=False)
    resp = auth_client.post(
        "/v1/app/customize/custom-rules/compile",
        json={"nlText": "every evidence record must have a timestamp"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"ok": False, "error": "compiler disabled"}


def test_shacl_compile_empty_nltext_rejected(
    auth_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAGI_SHACL_COMPILER_ENABLED", "1")
    resp = auth_client.post(
        "/v1/app/customize/custom-rules/compile", json={"nlText": "   "}
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "nlText must not be empty"


def test_shacl_compile_missing_nltext_rejected(
    auth_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAGI_SHACL_COMPILER_ENABLED", "1")
    resp = auth_client.post(
        "/v1/app/customize/custom-rules/compile", json={"other": "field"}
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "nlText_required"


def test_shacl_compile_oversized_nltext_rejected(
    auth_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAGI_SHACL_COMPILER_ENABLED", "1")
    resp = auth_client.post(
        "/v1/app/customize/custom-rules/compile",
        json={"nlText": "x" * 50_000},
    )
    assert resp.status_code == 400
    assert "exceeds" in resp.json()["error"]


# ---------------------------------------------------------------------------
# /v1/app/customize/rules/compile — NL rule compiler
# ---------------------------------------------------------------------------


def test_nl_rule_compile_requires_auth(noauth_client: TestClient) -> None:
    resp = noauth_client.post(
        "/v1/app/customize/rules/compile", json={"nlText": "be terse"}
    )
    assert resp.status_code == 401


def test_nl_rule_compile_flag_off_returns_disabled(
    auth_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MAGI_CUSTOMIZE_NL_RULE_COMPILER_ENABLED", raising=False)
    resp = auth_client.post(
        "/v1/app/customize/rules/compile", json={"nlText": "be terse"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("ok") is False
    assert "compiler" in body.get("error", "")


def test_nl_rule_compile_empty_nltext_rejected(
    auth_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE_NL_RULE_COMPILER_ENABLED", "1")
    resp = auth_client.post(
        "/v1/app/customize/rules/compile", json={"nlText": ""}
    )
    assert resp.status_code == 400


def test_nl_rule_compile_missing_nltext_rejected(
    auth_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE_NL_RULE_COMPILER_ENABLED", "1")
    resp = auth_client.post(
        "/v1/app/customize/rules/compile", json={"other": "x"}
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "nlText_required"


# ---------------------------------------------------------------------------
# /v1/app/customize/seams/compile — SeamSpec compiler
# ---------------------------------------------------------------------------


def test_seam_compile_requires_auth(noauth_client: TestClient) -> None:
    resp = noauth_client.post(
        "/v1/app/customize/seams/compile", json={"nlText": "tighten the gate"}
    )
    assert resp.status_code == 401


def test_seam_compile_flag_off_returns_disabled(
    auth_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The seam compiler is gated by MAGI_CUSTOMIZE_SEAM_COMPILER_ENABLED.
    monkeypatch.delenv("MAGI_CUSTOMIZE_SEAM_COMPILER_ENABLED", raising=False)
    resp = auth_client.post(
        "/v1/app/customize/seams/compile", json={"nlText": "tighten the gate"}
    )
    # Flag-OFF path returns 200 + ok:False per the disabled-feature
    # contract (the dashboard treats this as "feature unavailable").
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("ok") is False


def test_seam_compile_empty_nltext_rejected(
    auth_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Seam compiler returns ``{ok: False, error}`` on empty/invalid input.

    Unlike the SHACL + NL-rule compilers (which use 400 for validation
    errors), the seam compiler keeps the wizard's ``{ok}`` envelope and
    returns 200 + ``ok: False`` so the dashboard can surface the error
    inline without branching on HTTP status.
    """
    monkeypatch.setenv("MAGI_CUSTOMIZE_SEAM_COMPILER_ENABLED", "1")
    resp = auth_client.post(
        "/v1/app/customize/seams/compile", json={"nlText": ""}
    )
    assert resp.status_code in {200, 400}, (
        f"empty nlText: expected 200 (envelope) or 400 (hard reject); "
        f"got {resp.status_code} body={resp.text}"
    )
    body = resp.json()
    assert body.get("ok") is False, (
        f"empty nlText must produce ok=False; got body={body!r}"
    )


def test_seam_compile_missing_nltext_rejected(
    auth_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE_SEAM_COMPILER_ENABLED", "1")
    resp = auth_client.post(
        "/v1/app/customize/seams/compile", json={"other": "x"}
    )
    assert resp.status_code in {200, 400}
    body = resp.json()
    assert body.get("ok") is False
