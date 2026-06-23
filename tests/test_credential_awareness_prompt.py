"""WS1: agent awareness of registered Agent Vault credentials.

The system prompt gains a redacted credential block so the agent can acknowledge
that a credential EXISTS (service / auth scheme / approval requirement) without
ever seeing the secret value. This fixes the user-reported behaviour where the
agent answered "no record / no vault access" for a credential they had just
registered.

Contract:
* flag ON + credentials present  -> block lists service/label/auth/approval
* the block NEVER contains a secret value (only redacted metadata exists)
* empty store                     -> "" (no section)
* flag OFF                        -> "" (byte-identical to before)
* revoked credentials are not advertised
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from magi_agent.runtime.message_builder import _credentials_awareness_block


def _write_store(tmp_path: Path, credentials: list[dict]) -> Path:
    target = tmp_path / "credentials.json"
    target.write_text(json.dumps({"credentials": credentials}), encoding="utf-8")
    return target


def test_block_lists_registered_credential_when_flag_on(tmp_path, monkeypatch):
    store = _write_store(
        tmp_path,
        [
            {
                "id": "c1",
                "service": "test-vault-service",
                "label": "test_key",
                "auth_scheme": "api_key",
                "status": "active",
                "vault_ref": "vault://ref-1",
                "requires_approval": True,
                "host": None,
                "created_at": "2026-06-23T00:00:00Z",
            }
        ],
    )
    monkeypatch.setenv("MAGI_CREDENTIALS", str(store))
    monkeypatch.setenv("MAGI_CREDENTIAL_AWARENESS_ENABLED", "1")

    block = _credentials_awareness_block()

    assert "test-vault-service" in block
    assert "test_key" in block
    assert "api_key" in block
    # approval requirement must be surfaced
    assert "approval" in block.lower()
    # the secret value / vault_ref must NEVER leak into the prompt
    assert "vault://ref-1" not in block


def test_block_empty_when_no_credentials(tmp_path, monkeypatch):
    store = _write_store(tmp_path, [])
    monkeypatch.setenv("MAGI_CREDENTIALS", str(store))
    monkeypatch.setenv("MAGI_CREDENTIAL_AWARENESS_ENABLED", "1")

    assert _credentials_awareness_block() == ""


def test_block_empty_when_flag_off(tmp_path, monkeypatch):
    store = _write_store(
        tmp_path,
        [
            {
                "id": "c1",
                "service": "test-vault-service",
                "label": "test_key",
                "auth_scheme": "api_key",
                "status": "active",
                "vault_ref": "vault://ref-1",
                "requires_approval": False,
                "host": None,
                "created_at": "2026-06-23T00:00:00Z",
            }
        ],
    )
    monkeypatch.setenv("MAGI_CREDENTIALS", str(store))
    monkeypatch.setenv("MAGI_CREDENTIAL_AWARENESS_ENABLED", "0")

    assert _credentials_awareness_block() == ""


def test_revoked_credentials_not_advertised(tmp_path, monkeypatch):
    store = _write_store(
        tmp_path,
        [
            {
                "id": "c1",
                "service": "stripe",
                "label": "old",
                "auth_scheme": "bearer",
                "status": "revoked",
                "vault_ref": None,
                "requires_approval": False,
                "host": None,
                "created_at": "2026-06-23T00:00:00Z",
            }
        ],
    )
    monkeypatch.setenv("MAGI_CREDENTIALS", str(store))
    monkeypatch.setenv("MAGI_CREDENTIAL_AWARENESS_ENABLED", "1")

    assert _credentials_awareness_block() == ""


def test_block_is_in_dynamic_prompt_sections(tmp_path, monkeypatch):
    """The block must actually be wired into the assembled prompt, not orphaned."""
    from datetime import UTC, datetime

    from magi_agent.runtime.message_builder import _assemble_prompt_sections

    store = _write_store(
        tmp_path,
        [
            {
                "id": "c1",
                "service": "test-vault-service",
                "label": "test_key",
                "auth_scheme": "api_key",
                "status": "active",
                "vault_ref": "vault://ref-1",
                "requires_approval": True,
                "host": None,
                "created_at": "2026-06-23T00:00:00Z",
            }
        ],
    )
    monkeypatch.setenv("MAGI_CREDENTIALS", str(store))
    monkeypatch.setenv("MAGI_CREDENTIAL_AWARENESS_ENABLED", "1")

    _static, dynamic = _assemble_prompt_sections(
        session_key="s",
        turn_id="t",
        identity={},
        channel=None,
        user_message=None,
        runtime_now=datetime(2026, 6, 23, tzinfo=UTC),
        timezone="UTC",
        coding_agent=False,
        model="",
        model_aware_prompts_enabled=False,
    )

    assert any("test-vault-service" in part for part in dynamic)
