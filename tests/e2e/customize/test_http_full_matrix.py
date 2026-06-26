"""HTTP-level full legal matrix.

``test_http_to_fire_roundtrip.py`` walks one representative
configuration per kind through PUT -> fire -> DELETE. The matrix
files (``test_matrix_*.py``) exhaustively cover the in-process
fan-out for every legal ``(kind, fires_at, action)`` tuple but skip
the HTTP transport. This file glues them: drive **every** combination
from :data:`magi_agent.customize.custom_rules._LEGAL` through the
PUT endpoint and assert it persists.

Why a separate file
-------------------

A wizard payload that the validator silently rejects (the hosted
500 pattern) bypasses every in-process test because those tests
inject rules directly into ``customize.json`` via
``set_custom_rule``. The PUT endpoint is the only place where the
exact wizard-shaped JSON is validated. Iterating all 70 legal
combinations against the live endpoint pins the per-kind/per-slot
payload contract.

Fire assertions are intentionally limited to "PUT returns 200 +
the rule appears in the catalog + DELETE removes it" — the in-process
matrix files already pin per-row firing. This file's value-add is
**every wizard payload validates and persists**.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from magi_agent.app import create_app
from magi_agent.config.models import BuildInfo, RuntimeConfig
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime

from tests.e2e.customize.matrix import iter_legal_combinations
from tests.e2e.customize.payload_factory import build_payload


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
def http_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_SHELL_COMMAND_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_SHELL_CHECK_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_LLM_CALL_HOOKS_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_PROMPT_INJECTION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_OUTPUT_REWRITE_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_LIFECYCLE_EXPANSION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_LIFECYCLE_TURN_HOOKS_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_LIFECYCLE_EXTRA_EMITTERS_ENABLED", "1")
    monkeypatch.setenv("MAGI_SHACL_VERIFIER_ENABLED", "1")
    monkeypatch.delenv("MAGI_RUNTIME_PROFILE", raising=False)
    client = TestClient(create_app(_runtime()))
    client.headers.update({"x-gateway-token": _TOKEN})
    return client


pytestmark = pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="shell_runner honest-degrades on Windows",
)


@pytest.mark.parametrize(
    "kind,slot,action",
    sorted(iter_legal_combinations()),
    ids=lambda v: v.replace("_", "-") if isinstance(v, str) else str(v),
)
def test_legal_combo_http_persists(
    kind: str, slot: str, action: str, http_client: TestClient
) -> None:
    """Every (kind, fires_at, action) in _LEGAL: PUT -> persist -> DELETE.

    Asserts the per-kind validator accepts a canonical wizard-shape
    payload AND the persisted shape round-trips through GET. A 400 or
    500 here is exactly the failure class the hosted dashboard surfaced
    (silent endpoint mis-shape).
    """
    rule = build_payload(kind, slot, action)
    # Strip the factory's pre-baked id; the PUT endpoint assigns its own
    # ``cr_*`` id (the wizard does the same — the dashboard never sends
    # a client-side id).
    rule.pop("id", None)

    resp = http_client.put("/v1/app/customize/custom-rules", json=rule)
    assert resp.status_code == 200, (
        f"PUT /custom-rules for ({kind!r}, {slot!r}, {action!r}) "
        f"expected 200; got {resp.status_code} body={resp.text}"
    )
    body = resp.json()
    rid = body["id"]
    assert rid.startswith("cr_")
    listed_after_put = body["overrides"]["verification"]["custom_rules"]
    assert any(r["id"] == rid for r in listed_after_put)

    # Confirm GET sees the persisted shape and the (kind, slot, action)
    # round-tripped verbatim.
    get_resp = http_client.get("/v1/app/customize")
    assert get_resp.status_code == 200
    rules = (
        get_resp.json()
        .get("overrides", {})
        .get("verification", {})
        .get("custom_rules", [])
    )
    persisted = next((r for r in rules if r["id"] == rid), None)
    assert persisted is not None, (
        f"after PUT, rule {rid!r} must appear in GET; got {rules!r}"
    )
    assert persisted.get("firesAt") == slot
    assert persisted.get("action") == action
    assert persisted.get("what", {}).get("kind") == kind

    # DELETE for isolation between parametrized rows.
    del_resp = http_client.delete(f"/v1/app/customize/custom-rules/{rid}")
    assert del_resp.status_code == 200
    remaining = (
        del_resp.json()
        .get("overrides", {})
        .get("verification", {})
        .get("custom_rules", [])
    )
    assert not any(r["id"] == rid for r in remaining), (
        f"after DELETE, rule {rid!r} must be gone; got {remaining!r}"
    )
