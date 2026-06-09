from __future__ import annotations

import json
import logging

from fastapi.testclient import TestClient

from magi_agent.app import create_app
from magi_agent.config.models import BuildInfo, RuntimeConfig
from magi_agent.credentials_admin import vault_local
from magi_agent.credentials_admin.store import credentials_path
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime

# A realistic-looking secret used across tests. It is intentionally
# "secret-shaped" so the durable-store guard and redaction helpers are exercised.
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
    # Make sure no prior file leaks in.
    target = credentials_path()
    if target.exists():
        target.unlink()


def test_list_requires_gateway_token(monkeypatch, tmp_path) -> None:
    _isolate_store(monkeypatch, tmp_path)
    assert _client().get("/v1/admin/credentials").status_code == 401


def test_register_requires_gateway_token(monkeypatch, tmp_path) -> None:
    _isolate_store(monkeypatch, tmp_path)
    response = _client().post(
        "/v1/admin/credentials",
        json={
            "service": "openai",
            "label": "Prod key",
            "auth_scheme": "bearer",
            "secret": SECRET_VALUE,
        },
    )
    assert response.status_code == 401


def test_list_returns_credentials_and_vault_status(monkeypatch, tmp_path) -> None:
    _isolate_store(monkeypatch, tmp_path)
    response = _client().get(
        "/v1/admin/credentials", headers={"x-gateway-token": "local-token"}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["credentials"] == []
    assert payload["vault_status"] == {"present": False, "healthy": False}


def test_register_with_vault_disabled_persists_pending_metadata_without_secret(
    monkeypatch, tmp_path
) -> None:
    _isolate_store(monkeypatch, tmp_path)
    # Default-OFF: MAGI_VAULT_ADMIN_ENABLED unset → vault seam is a no-op.
    monkeypatch.delenv("MAGI_VAULT_ADMIN_ENABLED", raising=False)
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
    created = response.json()["credential"]
    assert created["service"] == "openai"
    assert created["label"] == "Prod key"
    assert created["auth_scheme"] == "bearer"
    assert created["status"] == "pending"
    assert created["vault_ref"] is None

    # The response carries NO secret anywhere.
    body_text = json.dumps(response.json())
    assert SECRET_VALUE not in body_text

    # The persisted metadata file carries NO secret anywhere.
    persisted = credentials_path().read_text(encoding="utf-8")
    assert SECRET_VALUE not in persisted

    # The list endpoint reflects the new metadata, still without secret.
    listing = client.get(
        "/v1/admin/credentials", headers={"x-gateway-token": "local-token"}
    ).json()
    assert len(listing["credentials"]) == 1
    assert SECRET_VALUE not in json.dumps(listing)


def test_durable_record_contains_no_secret(monkeypatch, tmp_path) -> None:
    """The digest-anchored durable record never carries the plaintext secret."""
    _isolate_store(monkeypatch, tmp_path)
    record = vault_local.build_durable_metadata_record(
        credential_id="cred-test",
        service="openai",
        label="Prod key",
        auth_scheme="bearer",
        status="pending",
        vault_ref=None,
    )
    serialized = json.dumps(record.storage_payload())
    assert SECRET_VALUE not in serialized
    # The guard must NOT be bypassed: the record went through DurableRecord
    # validation (collection is the registered metadata collection).
    assert record.collection == "credential_lease_metadata"


def test_durable_guard_not_bypassed_rejects_secret_shaped_metadata() -> None:
    """Proof the guard is real: a secret-shaped metadata value is rejected."""
    import pytest

    from magi_agent.storage.durable_store import DurableRecord

    digest = "sha256:" + "0" * 64
    with pytest.raises(Exception) as exc_info:
        DurableRecord(
            collection="credential_lease_metadata",
            recordId="cred-meta:test",
            contentDigest=digest,
            policySnapshotDigest=digest,
            metadata={"apiKey": SECRET_VALUE},
        )
    # The secret-shaped value was rejected (guard not bypassed) and the secret
    # is NOT echoed back in the error message.
    assert "raw or sensitive" in str(exc_info.value)
    assert SECRET_VALUE not in str(exc_info.value)


def test_register_secret_never_logged(monkeypatch, tmp_path, caplog) -> None:
    _isolate_store(monkeypatch, tmp_path)
    monkeypatch.delenv("MAGI_VAULT_ADMIN_ENABLED", raising=False)
    client = _client()
    with caplog.at_level(logging.DEBUG):
        client.post(
            "/v1/admin/credentials",
            headers={"x-gateway-token": "local-token"},
            json={
                "service": "openai",
                "label": "Prod key",
                "auth_scheme": "bearer",
                "secret": SECRET_VALUE,
            },
        )
    for record in caplog.records:
        assert SECRET_VALUE not in record.getMessage()
        assert SECRET_VALUE not in str(record.args)


def test_vault_seam_never_logs_or_returns_secret(monkeypatch, tmp_path, caplog) -> None:
    """register_credential must never surface the secret in logs/return/exception."""
    _isolate_store(monkeypatch, tmp_path)
    monkeypatch.delenv("MAGI_VAULT_ADMIN_ENABLED", raising=False)
    with caplog.at_level(logging.DEBUG):
        result = vault_local.register_credential(
            service="openai",
            label="Prod key",
            auth_scheme="bearer",
            secret=SECRET_VALUE,
        )
    # Default-OFF → disabled, no network, no vault_ref.
    assert result == {"disabled": True}
    serialized = json.dumps(result)
    assert SECRET_VALUE not in serialized
    for record in caplog.records:
        assert SECRET_VALUE not in record.getMessage()


def test_vault_status_default_off(monkeypatch) -> None:
    monkeypatch.delenv("MAGI_VAULT_ADMIN_ENABLED", raising=False)
    assert vault_local.vault_status() == {"present": False, "healthy": False}


def test_revoke_marks_metadata_revoked(monkeypatch, tmp_path) -> None:
    _isolate_store(monkeypatch, tmp_path)
    monkeypatch.delenv("MAGI_VAULT_ADMIN_ENABLED", raising=False)
    client = _client()
    created = client.post(
        "/v1/admin/credentials",
        headers={"x-gateway-token": "local-token"},
        json={
            "service": "openai",
            "label": "Prod key",
            "auth_scheme": "bearer",
            "secret": SECRET_VALUE,
        },
    ).json()["credential"]
    credential_id = created["id"]

    revoke = client.post(
        f"/v1/admin/credentials/{credential_id}/revoke",
        headers={"x-gateway-token": "local-token"},
    )
    assert revoke.status_code == 200
    assert revoke.json()["credential"]["status"] == "revoked"

    listing = client.get(
        "/v1/admin/credentials", headers={"x-gateway-token": "local-token"}
    ).json()
    statuses = {c["id"]: c["status"] for c in listing["credentials"]}
    assert statuses[credential_id] == "revoked"


def test_revoke_unknown_credential_returns_404(monkeypatch, tmp_path) -> None:
    _isolate_store(monkeypatch, tmp_path)
    client = _client()
    response = client.post(
        "/v1/admin/credentials/does-not-exist/revoke",
        headers={"x-gateway-token": "local-token"},
    )
    assert response.status_code == 404
