from __future__ import annotations

import json
import logging

import pytest
from fastapi.testclient import TestClient

from magi_agent.app import create_app
from magi_agent.config.models import BuildInfo, RuntimeConfig
from magi_agent.credentials_admin import approvals_store, store, vault_local
from magi_agent.credentials_admin.store import credentials_path
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime


@pytest.fixture(autouse=True)
def _clean_vault_env(monkeypatch):
    # Isolate from global os.environ leak of MAGI_LOCAL_VAULT_ENABLED (other test
    # files setdefault it via the local-runtime defaults). Default-OFF assertions
    # must start from a clean env; tests needing it ON set it explicitly.
    monkeypatch.delenv("MAGI_LOCAL_VAULT_ENABLED", raising=False)
    monkeypatch.delenv("MAGI_VAULT_ADMIN_ENABLED", raising=False)
    monkeypatch.delenv("MAGI_VAULT_ADMIN_URL", raising=False)

# A realistic-looking secret used across tests. It is intentionally
# "secret-shaped" so we can prove no approval record ever carries it.
SECRET_VALUE = "sk-live-abcd1234EFGH5678ijkl9012MNOP3456"


def _runtime(gateway_token: str = "local-token") -> OpenMagiRuntime:
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


def _client(gateway_token: str = "local-token") -> TestClient:
    return TestClient(create_app(_runtime(gateway_token)))


def _isolate_store(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))
    for target in (credentials_path(), approvals_store.approvals_path()):
        if target.exists():
            target.unlink()


def _seed_approval(**overrides) -> dict:
    """Enqueue a pending approval directly via the store (mimics the vault)."""
    fields = {
        "credential_id": "cred-123",
        "requested_action": "use",
        "target_host": "api.openai.com",
        "reason": "agent wants to call the OpenAI API",
    }
    fields.update(overrides)
    return approvals_store.add_approval(**fields)


# --- auth ----------------------------------------------------------------


def test_list_approvals_requires_gateway_token(monkeypatch, tmp_path) -> None:
    _isolate_store(monkeypatch, tmp_path)
    assert _client().get("/v1/admin/credentials/approvals").status_code == 401


def test_decide_approval_requires_gateway_token(monkeypatch, tmp_path) -> None:
    _isolate_store(monkeypatch, tmp_path)
    approval = _seed_approval()
    response = _client().post(
        f"/v1/admin/credentials/approvals/{approval['id']}",
        json={"decision": "approved"},
    )
    assert response.status_code == 401


# --- list ----------------------------------------------------------------


def test_list_approvals_returns_records(monkeypatch, tmp_path) -> None:
    _isolate_store(monkeypatch, tmp_path)
    _seed_approval()
    response = _client().get(
        "/v1/admin/credentials/approvals",
        headers={"x-gateway-token": "local-token"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["approvals"]) == 1
    record = payload["approvals"][0]
    assert record["credential_id"] == "cred-123"
    assert record["requested_action"] == "use"
    assert record["target_host"] == "api.openai.com"
    assert record["status"] == "pending"
    assert record["decided_at"] is None


def test_list_approvals_filters_by_status(monkeypatch, tmp_path) -> None:
    _isolate_store(monkeypatch, tmp_path)
    pending = _seed_approval(credential_id="cred-pending")
    decided = _seed_approval(credential_id="cred-decided")
    approvals_store.decide_approval(decided["id"], "denied")

    client = _client()
    listing = client.get(
        "/v1/admin/credentials/approvals?status=pending",
        headers={"x-gateway-token": "local-token"},
    ).json()
    ids = {a["id"] for a in listing["approvals"]}
    assert pending["id"] in ids
    assert decided["id"] not in ids


# --- decide --------------------------------------------------------------


def test_decide_approval_updates_status_and_decided_at(monkeypatch, tmp_path) -> None:
    _isolate_store(monkeypatch, tmp_path)
    monkeypatch.delenv("MAGI_VAULT_ADMIN_ENABLED", raising=False)
    approval = _seed_approval()
    client = _client()

    response = client.post(
        f"/v1/admin/credentials/approvals/{approval['id']}",
        headers={"x-gateway-token": "local-token"},
        json={"decision": "approved"},
    )
    assert response.status_code == 200
    decided = response.json()["approval"]
    assert decided["status"] == "approved"
    assert decided["decided_at"]

    listing = client.get(
        "/v1/admin/credentials/approvals",
        headers={"x-gateway-token": "local-token"},
    ).json()
    statuses = {a["id"]: a["status"] for a in listing["approvals"]}
    assert statuses[approval["id"]] == "approved"


def test_decide_approval_denied(monkeypatch, tmp_path) -> None:
    _isolate_store(monkeypatch, tmp_path)
    monkeypatch.delenv("MAGI_VAULT_ADMIN_ENABLED", raising=False)
    approval = _seed_approval()
    response = _client().post(
        f"/v1/admin/credentials/approvals/{approval['id']}",
        headers={"x-gateway-token": "local-token"},
        json={"decision": "denied"},
    )
    assert response.status_code == 200
    assert response.json()["approval"]["status"] == "denied"


def test_decide_unknown_approval_returns_404(monkeypatch, tmp_path) -> None:
    _isolate_store(monkeypatch, tmp_path)
    response = _client().post(
        "/v1/admin/credentials/approvals/does-not-exist",
        headers={"x-gateway-token": "local-token"},
        json={"decision": "approved"},
    )
    assert response.status_code == 404


def test_decide_invalid_decision_returns_400(monkeypatch, tmp_path) -> None:
    _isolate_store(monkeypatch, tmp_path)
    approval = _seed_approval()
    response = _client().post(
        f"/v1/admin/credentials/approvals/{approval['id']}",
        headers={"x-gateway-token": "local-token"},
        json={"decision": "maybe"},
    )
    assert response.status_code == 400


# --- requires_approval on registration -----------------------------------


def test_register_requires_approval_persists_in_metadata(monkeypatch, tmp_path) -> None:
    _isolate_store(monkeypatch, tmp_path)
    monkeypatch.delenv("MAGI_VAULT_ADMIN_ENABLED", raising=False)
    monkeypatch.setattr(
        vault_local,
        "vault_status",
        lambda: {"present": True, "healthy": True},
    )
    monkeypatch.setattr(
        vault_local,
        "register_credential",
        lambda **_: {"vault_ref": "vault://local/cred-approval"},
    )
    client = _client()

    response = client.post(
        "/v1/admin/credentials",
        headers={"x-gateway-token": "local-token"},
        json={
            "service": "openai",
            "label": "Prod key",
            "auth_scheme": "bearer",
            "secret": SECRET_VALUE,
            "requires_approval": True,
        },
    )
    assert response.status_code == 200
    created = response.json()["credential"]
    assert created["requires_approval"] is True

    listing = client.get(
        "/v1/admin/credentials", headers={"x-gateway-token": "local-token"}
    ).json()
    assert listing["credentials"][0]["requires_approval"] is True
    # No secret leaked anywhere.
    assert SECRET_VALUE not in json.dumps(listing)
    assert SECRET_VALUE not in credentials_path().read_text(encoding="utf-8")


def test_register_requires_approval_defaults_false(monkeypatch, tmp_path) -> None:
    _isolate_store(monkeypatch, tmp_path)
    monkeypatch.delenv("MAGI_VAULT_ADMIN_ENABLED", raising=False)
    monkeypatch.setattr(
        vault_local,
        "vault_status",
        lambda: {"present": True, "healthy": True},
    )
    monkeypatch.setattr(
        vault_local,
        "register_credential",
        lambda **_: {"vault_ref": "vault://local/cred-default"},
    )
    client = _client()
    response = client.post(
        "/v1/admin/credentials",
        headers={"x-gateway-token": "local-token"},
        json={
            "service": "openai",
            "label": "Prod key",
            "auth_scheme": "bearer",
            "secret": SECRET_VALUE,
        },
    )
    assert response.status_code == 200
    assert response.json()["credential"]["requires_approval"] is False


def test_register_credential_forwards_requires_approval_to_seam(
    monkeypatch, tmp_path
) -> None:
    """vault_local.register_credential accepts + forwards requires_approval."""
    _isolate_store(monkeypatch, tmp_path)
    monkeypatch.delenv("MAGI_VAULT_ADMIN_ENABLED", raising=False)
    # Default-OFF → disabled, no exception, requires_approval accepted in signature.
    result = vault_local.register_credential(
        service="openai",
        label="Prod key",
        auth_scheme="bearer",
        secret=SECRET_VALUE,
        requires_approval=True,
    )
    assert result == {"disabled": True}


# --- vault-disabled seam --------------------------------------------------


def test_resolve_approval_noop_when_vault_disabled(monkeypatch) -> None:
    monkeypatch.delenv("MAGI_VAULT_ADMIN_ENABLED", raising=False)
    result = vault_local.resolve_approval(approval_id="appr-1", decision="approved")
    assert result == {"disabled": True}


def test_decide_records_locally_even_when_vault_disabled(monkeypatch, tmp_path) -> None:
    _isolate_store(monkeypatch, tmp_path)
    monkeypatch.delenv("MAGI_VAULT_ADMIN_ENABLED", raising=False)
    approval = _seed_approval()
    client = _client()
    response = client.post(
        f"/v1/admin/credentials/approvals/{approval['id']}",
        headers={"x-gateway-token": "local-token"},
        json={"decision": "approved"},
    )
    assert response.status_code == 200
    # Even with the vault seam OFF, the decision is recorded locally.
    record = approvals_store.get_approval(approval["id"])
    assert record is not None
    assert record["status"] == "approved"
    assert record["decided_at"]


# --- no secret in approval records ---------------------------------------


def test_no_secret_in_approval_records(monkeypatch, tmp_path) -> None:
    _isolate_store(monkeypatch, tmp_path)
    # An approval is metadata only: even if a caller tries to smuggle a secret
    # into a free-text field, the projection drops unknown keys.
    approval = approvals_store.add_approval(
        credential_id="cred-123",
        requested_action="use",
        target_host="api.openai.com",
        reason="needs the key",
    )
    assert SECRET_VALUE not in json.dumps(approval)
    persisted = approvals_store.approvals_path().read_text(encoding="utf-8")
    assert SECRET_VALUE not in persisted
    # The projection has no secret-bearing fields at all.
    assert set(approval.keys()) == {
        "id",
        "credential_id",
        "requested_action",
        "target_host",
        "status",
        "reason",
        "created_at",
        "decided_at",
    }


def test_approval_reason_redacts_secret_text_before_persisting(
    monkeypatch, tmp_path
) -> None:
    _isolate_store(monkeypatch, tmp_path)
    tainted_reason = f"Authorization: Bearer {SECRET_VALUE}"

    approval = approvals_store.add_approval(
        credential_id="cred-123",
        requested_action="use",
        target_host="api.openai.com",
        reason=tainted_reason,
    )

    assert approval["reason"] == "[redacted]"
    persisted = approvals_store.approvals_path().read_text(encoding="utf-8")
    assert SECRET_VALUE not in persisted
    assert tainted_reason not in persisted

    response = _client().get(
        "/v1/admin/credentials/approvals",
        headers={"x-gateway-token": "local-token"},
    )
    assert response.status_code == 200
    payload = json.dumps(response.json())
    assert SECRET_VALUE not in payload
    assert tainted_reason not in payload


def test_decide_never_logs_secret(monkeypatch, tmp_path, caplog) -> None:
    _isolate_store(monkeypatch, tmp_path)
    monkeypatch.delenv("MAGI_VAULT_ADMIN_ENABLED", raising=False)
    approval = _seed_approval(reason=SECRET_VALUE)  # even a tainted reason
    client = _client()
    with caplog.at_level(logging.DEBUG):
        client.post(
            f"/v1/admin/credentials/approvals/{approval['id']}",
            headers={"x-gateway-token": "local-token"},
            json={"decision": "approved"},
        )
    for record in caplog.records:
        assert SECRET_VALUE not in record.getMessage()
        assert SECRET_VALUE not in str(record.args)
