"""First-party ``ListCredentials`` tool (gate5b full toolhost).

A read-only, secret-free affordance so the agent (especially weaker models that
ignore prose) can actively check which Agent Vault credentials exist, instead of
hunting through files/memory or asking the user for a value it can never see.

Invariants exercised here:
* the projection is redacted (no secret, no opaque vault_ref),
* revoked credentials are dropped,
* the tool is wired into the full toolhost toolset + ADK tools,
* dispatch succeeds and its preview never carries the vault_ref,
* the orchestrator main-agent profile exposes it (it is read-only).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from magi_agent.credentials_admin import store
from magi_agent.gates.gate5b_full_toolhost import (
    GATE5B_FULL_TOOLHOST_TOOL_NAMES,
    Gate5BFullToolHostConfig,
    _list_credentials_output,
    build_gate5b_full_toolhost_bundle,
)


def _sha256(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


def _seed_store(tmp_path: Path) -> Path:
    creds = tmp_path / "credentials.json"
    store.add_credential(
        service="test-vault-service",
        label="test_key",
        auth_scheme="api_key",
        status=store.STATUS_ACTIVE,
        vault_ref="vault://opaque-ref-XYZ",
        requires_approval=True,
        host="api.example.com",
        path=creds,
    )
    store.add_credential(
        service="stripe",
        label="old",
        auth_scheme="bearer",
        status=store.STATUS_REVOKED,
        vault_ref="vault://revoked-ref",
        requires_approval=False,
        host=None,
        path=creds,
    )
    return creds


def test_output_is_redacted_and_drops_revoked(tmp_path, monkeypatch):
    creds = _seed_store(tmp_path)
    monkeypatch.setenv("MAGI_CREDENTIALS", str(creds))

    out = _list_credentials_output()
    rows = out["credentials"]

    # Only the active credential survives.
    assert [r["service"] for r in rows] == ["test-vault-service"]
    row = rows[0]
    assert row["label"] == "test_key"
    assert row["auth_scheme"] == "api_key"
    assert row["requires_approval"] is True
    assert row["host"] == "api.example.com"

    # The opaque vault_ref and the secret must NEVER be in the projection.
    serialized = json.dumps(out)
    assert "vault://" not in serialized
    assert "vault_ref" not in serialized


def test_empty_store_returns_empty_list(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_CREDENTIALS", str(tmp_path / "missing.json"))
    assert _list_credentials_output() == {"credentials": []}


def test_tool_is_in_full_toolhost_toolset():
    assert "ListCredentials" in GATE5B_FULL_TOOLHOST_TOOL_NAMES


def _build_bundle(tmp_path: Path):
    return build_gate5b_full_toolhost_bundle(
        config=Gate5BFullToolHostConfig.model_validate(
            {
                "enabled": True,
                "killSwitchEnabled": False,
                "routeAttachmentEnabled": True,
                "selectedBotDigest": _sha256("bot-test"),
                "selectedOwnerDigest": _sha256("user-test"),
                "environment": "production",
                "environmentAllowlist": ("production",),
                "allowedToolNames": GATE5B_FULL_TOOLHOST_TOOL_NAMES,
                "maxToolCallsPerTurn": 8,
            }
        ),
        scope={
            "selectedBotDigest": _sha256("bot-test"),
            "selectedOwnerDigest": _sha256("user-test"),
            "environment": "production",
        },
        workspace_root=tmp_path,
    )


def test_tool_is_exposed_as_an_adk_function_tool(tmp_path):
    bundle = _build_bundle(tmp_path)
    tool = next((t for t in bundle.tools if t.name == "ListCredentials"), None)
    assert tool is not None
    assert "ListCredentials" in bundle.host.exposed_tool_names


@pytest.mark.asyncio
async def test_dispatch_returns_redacted_list(tmp_path, monkeypatch):
    creds = _seed_store(tmp_path)
    monkeypatch.setenv("MAGI_CREDENTIALS", str(creds))

    bundle = _build_bundle(tmp_path)
    outcome = await bundle.host.dispatch(
        "ListCredentials",
        {},
        request_digest=_sha256("req-listcreds"),
        tool_call_id="call-listcreds",
    )

    assert outcome.status == "ok"
    preview = json.dumps(outcome.output_preview)
    assert "test-vault-service" in preview
    # The opaque vault_ref must never reach the model, even via the preview.
    assert "vault://" not in preview


def test_orchestrator_profile_exposes_list_credentials():
    from magi_agent.runtime.main_agent_profile import (
        apply_orchestrator_filter,
        orchestrator_tool_names,
    )

    assert "ListCredentials" in orchestrator_tool_names()
    restricted, _spawn_cap = apply_orchestrator_filter(GATE5B_FULL_TOOLHOST_TOOL_NAMES)
    assert "ListCredentials" in restricted
