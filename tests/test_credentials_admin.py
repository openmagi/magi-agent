from __future__ import annotations

import json
import logging
from pathlib import Path

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


def test_register_with_vault_disabled_rejects_without_persisting_metadata(
    monkeypatch, tmp_path
) -> None:
    _isolate_store(monkeypatch, tmp_path)
    # Default-OFF: no vault exists to retain the secret, so registration is blocked.
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
    assert response.status_code == 503
    assert response.json()["error"] == "vault_unavailable"

    # The response carries NO secret anywhere.
    body_text = json.dumps(response.json())
    assert SECRET_VALUE not in body_text

    # No metadata row is persisted because there is no retained secret to
    # forward later.
    assert not credentials_path().exists()
    listing = client.get(
        "/v1/admin/credentials", headers={"x-gateway-token": "local-token"}
    ).json()
    assert listing["credentials"] == []
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
    monkeypatch.setattr(
        vault_local,
        "vault_status",
        lambda: {"present": True, "healthy": True},
    )
    monkeypatch.setattr(
        vault_local,
        "register_credential",
        lambda **_: {"vault_ref": "vault://local/cred-1"},
    )
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


# --- Native local vault backend (Phase 1) -----------------------------------


def _enable_local_vault(monkeypatch, tmp_path) -> Path:
    """Point the local vault at a tmp dir and enable the native backend."""
    import os

    vault_dir = Path(tmp_path) / "vault"
    monkeypatch.setenv("MAGI_VAULT_DIR", str(vault_dir))
    monkeypatch.setenv("MAGI_LOCAL_VAULT_ENABLED", "1")
    monkeypatch.delenv("MAGI_VAULT_ADMIN_URL", raising=False)
    monkeypatch.delenv("MAGI_VAULT_ADMIN_ENABLED", raising=False)
    os.environ.pop("MAGI_VAULT_ADMIN_URL", None)
    return vault_dir


def test_vault_status_present_with_local_backend(monkeypatch, tmp_path) -> None:
    _isolate_store(monkeypatch, tmp_path)
    _enable_local_vault(monkeypatch, tmp_path)
    status = vault_local.vault_status()
    assert status == {"present": True, "healthy": True}


def test_register_with_local_vault_stores_ciphertext_only(
    monkeypatch, tmp_path
) -> None:
    """End-to-end: register → status active, no secret in response/metadata,
    secrets.enc exists and is ciphertext (plaintext absent)."""
    _isolate_store(monkeypatch, tmp_path)
    vault_dir = _enable_local_vault(monkeypatch, tmp_path)
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
    credential = response.json()["credential"]
    assert credential["status"] == "active"
    assert credential["vault_ref"]

    # The response carries NO secret.
    assert SECRET_VALUE not in json.dumps(response.json())

    # The metadata store holds NO secret.
    meta_text = credentials_path().read_text(encoding="utf-8")
    assert SECRET_VALUE not in meta_text

    # The encrypted store exists and is ciphertext (plaintext absent).
    enc_path = vault_dir / "secrets.enc"
    assert enc_path.is_file()
    enc_bytes = enc_path.read_bytes()
    assert SECRET_VALUE.encode("utf-8") not in enc_bytes
    assert credential["vault_ref"] in enc_bytes.decode("utf-8")


def test_local_vault_register_never_logs_secret(monkeypatch, tmp_path, caplog) -> None:
    _isolate_store(monkeypatch, tmp_path)
    _enable_local_vault(monkeypatch, tmp_path)
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


def test_local_vault_revoke_deletes_secret(monkeypatch, tmp_path) -> None:
    _isolate_store(monkeypatch, tmp_path)
    vault_dir = _enable_local_vault(monkeypatch, tmp_path)
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
    vault_ref = created["vault_ref"]
    credential_id = created["id"]

    # The ciphertext entry exists.
    from magi_agent.credentials_admin.local_vault import LocalVault

    vault = LocalVault(vault_dir=vault_dir)
    assert vault.get_secret(vault_ref) == SECRET_VALUE

    revoke = client.post(
        f"/v1/admin/credentials/{credential_id}/revoke",
        headers={"x-gateway-token": "local-token"},
    )
    assert revoke.status_code == 200
    assert revoke.json()["credential"]["status"] == "revoked"

    # The secret is deleted from the encrypted store.
    assert vault.get_secret(vault_ref) is None


def test_external_url_takes_precedence_over_local_vault(monkeypatch, tmp_path) -> None:
    """When MAGI_VAULT_ADMIN_URL is set, the external path is taken (LocalVault
    NOT used) even if the local flag is on."""
    _isolate_store(monkeypatch, tmp_path)
    _enable_local_vault(monkeypatch, tmp_path)
    monkeypatch.setenv("MAGI_VAULT_ADMIN_ENABLED", "1")
    monkeypatch.setenv("MAGI_VAULT_ADMIN_URL", "https://vault.example/admin")

    # Backend selection: local vault must be inert when an external URL is set.
    assert vault_local.local_vault_enabled() is False

    # vault_status reports present (external) but unprobed → not healthy.
    assert vault_local.vault_status() == {"present": True, "healthy": False}

    captured: dict[str, object] = {}

    def _fake_local_store(self, secret):  # pragma: no cover - must NOT be called
        captured["local_used"] = True
        return "should-not-happen"

    from magi_agent.credentials_admin.local_vault import LocalVault

    monkeypatch.setattr(LocalVault, "store_secret", _fake_local_store)

    # The external transport is unwired in the OSS scaffold → VaultSeamError →
    # register_credential raises, proving the external (not local) path ran.
    import pytest

    with pytest.raises(vault_local.VaultSeamError):
        vault_local.register_credential(
            service="openai",
            label="Prod key",
            auth_scheme="bearer",
            secret=SECRET_VALUE,
        )
    assert "local_used" not in captured


def test_disabled_when_neither_external_nor_local(monkeypatch, tmp_path) -> None:
    """Unchanged pending behavior when nothing is configured."""
    _isolate_store(monkeypatch, tmp_path)
    monkeypatch.delenv("MAGI_VAULT_ADMIN_URL", raising=False)
    monkeypatch.delenv("MAGI_VAULT_ADMIN_ENABLED", raising=False)
    monkeypatch.delenv("MAGI_LOCAL_VAULT_ENABLED", raising=False)
    import os

    os.environ.pop("MAGI_VAULT_ADMIN_URL", None)
    assert vault_local.vault_status() == {"present": False, "healthy": False}
    assert vault_local.register_credential(
        service="openai",
        label="Prod key",
        auth_scheme="bearer",
        secret=SECRET_VALUE,
    ) == {"disabled": True}
