from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from openmagi_core_agent.config.models import BuildInfo, RuntimeConfig
from openmagi_core_agent.runtime.openmagi_runtime import OpenMagiRuntime
from openmagi_core_agent.transport.product_admin import (
    build_product_admin_contract_snapshot,
    register_product_admin_contract_routes,
)


PYTHON_ROOT = Path(__file__).parents[1]
MODULE_PATH = PYTHON_ROOT / "openmagi_core_agent" / "transport" / "product_admin.py"
ADMIN_SECTIONS = {
    "ops",
    "policy",
    "artifacts",
    "release_gates",
    "connector_registry",
    "security_compliance",
}


def make_config() -> RuntimeConfig:
    return RuntimeConfig(
        bot_id="bot-test",
        user_id="user-test",
        gateway_token="gateway-token",
        api_proxy_url="http://api-proxy.local",
        chat_proxy_url="http://chat-proxy.local",
        redis_url="redis://redis.local:6379/0",
        model="gpt-5.2",
        build=BuildInfo(version="0.1.0-adk-scaffold", build_sha="sha-test"),
    )


def make_admin_client(runtime: OpenMagiRuntime | None = None) -> TestClient:
    app = FastAPI()
    register_product_admin_contract_routes(app, runtime or OpenMagiRuntime(config=make_config()))
    return TestClient(app)


def admin_headers(token: str = "gateway-token") -> dict[str, str]:
    return {"x-gateway-token": token}


def assert_digest_only(value: object) -> None:
    encoded = json.dumps(value, sort_keys=True).lower()
    for forbidden in (
        "raw prompt",
        "raw output",
        "hidden reasoning",
        "authorization",
        "bearer ",
        "cookie",
        "session key",
        "credential value",
        "connector token",
        "/users/",
        "/private/",
        ".env",
        "sk-",
        "ghp_",
    ):
        assert forbidden not in encoded


def test_product_admin_contract_snapshot_is_default_off_and_digest_only() -> None:
    runtime = OpenMagiRuntime(config=make_config())

    snapshot = build_product_admin_contract_snapshot(runtime)

    assert snapshot["schemaVersion"] == "openmagi.product_admin.contract_snapshot.v1"
    assert snapshot["runtimeRef"] == "bot:bot-test"
    assert set(snapshot["sections"]) == ADMIN_SECTIONS
    assert snapshot["noLiveData"] is True
    assert snapshot["defaultOff"] is True
    assert set(snapshot["authorityFlags"].values()) == {False}
    assert snapshot["sections"]["ops"]["routeAttached"] is False
    assert snapshot["sections"]["ops"]["liveDataAttached"] is False
    assert snapshot["sections"]["policy"]["mutationAllowed"] is False
    assert snapshot["sections"]["artifacts"]["deliveryClaimAllowed"] is False
    assert snapshot["sections"]["release_gates"]["promotionAllowed"] is False
    assert snapshot["sections"]["connector_registry"]["credentialReadAllowed"] is False
    assert snapshot["sections"]["security_compliance"]["publicRouteAttached"] is False
    assert_digest_only(snapshot)


def test_product_admin_contract_routes_require_gateway_token() -> None:
    client = make_admin_client()

    for path in (
        "/v1/admin/product/contracts",
        "/v1/admin/product/contracts/ops",
        "/v1/admin/product/contracts/security_compliance",
    ):
        missing = client.get(path)
        assert missing.status_code == 401
        assert missing.json() == {"error": "unauthorized"}

        wrong = client.get(path, headers=admin_headers("wrong-token"))
        assert wrong.status_code == 401
        assert wrong.json() == {"error": "unauthorized"}


def test_product_admin_contract_routes_return_readonly_default_off_stubs() -> None:
    client = make_admin_client()

    response = client.get("/v1/admin/product/contracts", headers=admin_headers())
    assert response.status_code == 200
    body = response.json()
    assert set(body["sections"]) == ADMIN_SECTIONS
    assert body["liveDataAttached"] is False
    assert body["mutationRoutesAttached"] is False
    assert body["frontendRouteAttached"] is False
    assert set(body["authorityFlags"].values()) == {False}
    assert_digest_only(body)

    section = client.get("/v1/admin/product/contracts/release_gates", headers=admin_headers())
    assert section.status_code == 200
    payload = section.json()
    assert payload["section"] == "release_gates"
    assert payload["contract"]["promotionAllowed"] is False
    assert payload["contract"]["liveDataAttached"] is False
    assert_digest_only(payload)


def test_unknown_product_admin_contract_section_is_not_found() -> None:
    client = make_admin_client()

    response = client.get("/v1/admin/product/contracts/customer-plan", headers=admin_headers())

    assert response.status_code == 404
    assert response.json() == {
        "error": "not_found",
        "message": "product admin contract section not found",
    }

    private = client.get(
        "/v1/admin/product/contracts/sk-private-token",
        headers=admin_headers(),
    )
    assert private.status_code == 404
    encoded = json.dumps(private.json(), sort_keys=True).lower()
    assert "sk-private-token" not in encoded
    assert "token" not in encoded


def test_product_admin_contract_routes_do_not_register_mutating_admin_actions() -> None:
    client = make_admin_client()

    for method, path in (
        ("post", "/v1/admin/product/contracts"),
        ("put", "/v1/admin/product/contracts/policy"),
        ("delete", "/v1/admin/product/contracts/artifacts"),
        ("patch", "/v1/admin/product/contracts/release_gates"),
    ):
        response = getattr(client, method)(path, headers=admin_headers())
        assert response.status_code in {404, 405}


def test_product_admin_transport_import_boundary_has_no_live_runtime_imports() -> None:
    script = (
        "import sys\n"
        "import openmagi_core_agent.transport.product_admin\n"
        "forbidden=('google.adk','kubernetes','supabase','stripe','httpx','requests')\n"
        "loaded=[name for name in forbidden if name in sys.modules]\n"
        "raise SystemExit(1 if loaded else 0)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=PYTHON_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_product_admin_module_does_not_import_frontend_or_activation_paths() -> None:
    text = MODULE_PATH.read_text()

    forbidden_fragments = (
        "src/app",
        "supabase",
        "stripe",
        "kubernetes",
        "chat-proxy",
        "model.generate",
        "Runner(",
        "ToolHost",
        "subprocess",
        "httpx",
        "requests",
    )
    for fragment in forbidden_fragments:
        assert fragment not in text
