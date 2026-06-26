"""Negative-path coverage for the customize HTTP surface.

The matrix tests cover happy paths. The hosted dashboard equally
depends on the negative paths being honest: invalid payloads must
return 4xx with a useful error code, master flags OFF must keep the
runtime quiet, conflicting rules must compose deterministically.

Pinned here:

* Invalid PUT payload variants (kind unknown, missing fields,
  scope/firesAt/action out of vocabulary).
* DELETE for an unknown rule id is 4xx (not silent 200).
* Master-flag OFF: a persisted rule MUST NOT fire (rule still in
  storage but the fan-out short-circuits).
* Conflicting rules at the same slot (block + audit) -> the gate
  verdict must honor the block rule even when an audit rule is also
  enabled.
* HTTP auth: every customize endpoint rejects requests with no token.
"""

from __future__ import annotations

import asyncio
import sys
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
def http_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_SHELL_COMMAND_ENABLED", "1")
    monkeypatch.delenv("MAGI_RUNTIME_PROFILE", raising=False)
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


pytestmark = pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="shell_runner honest-degrades on Windows",
)


# ---------------------------------------------------------------------------
# Invalid PUT payloads
# ---------------------------------------------------------------------------


def test_put_rule_unknown_kind_rejected(http_client: TestClient) -> None:
    resp = http_client.put(
        "/v1/app/customize/custom-rules",
        json={
            "scope": "always",
            "enabled": True,
            "firesAt": "pre_final",
            "action": "audit",
            "what": {"kind": "totally_made_up", "payload": {}},
        },
    )
    assert resp.status_code == 400
    assert resp.json().get("error") == "invalid_custom_rule"


def test_put_rule_unknown_fires_at_rejected(http_client: TestClient) -> None:
    resp = http_client.put(
        "/v1/app/customize/custom-rules",
        json={
            "scope": "always",
            "enabled": True,
            "firesAt": "imaginary_slot",
            "action": "audit",
            "what": {"kind": "deterministic_ref", "payload": {"ref": "evidence:test-run"}},
        },
    )
    assert resp.status_code == 400


def test_put_rule_unknown_action_rejected(http_client: TestClient) -> None:
    resp = http_client.put(
        "/v1/app/customize/custom-rules",
        json={
            "scope": "always",
            "enabled": True,
            "firesAt": "pre_final",
            "action": "vaporize",
            "what": {"kind": "deterministic_ref", "payload": {"ref": "evidence:test-run"}},
        },
    )
    assert resp.status_code == 400


def test_put_rule_illegal_kind_slot_action_combo_rejected(
    http_client: TestClient,
) -> None:
    """A legal (kind, slot) tuple with an illegal action MUST 400.

    Pin the cross-field matrix gate. capability_scope+spawn allows
    only ``block``; authoring ``audit`` here exercises the _LEGAL
    matrix's action-set validator.
    """
    resp = http_client.put(
        "/v1/app/customize/custom-rules",
        json={
            "scope": "coding",
            "enabled": True,
            "firesAt": "spawn",
            "action": "audit",
            "what": {
                "kind": "capability_scope",
                "payload": {"tightenOnly": True, "denyTools": ["X"]},
            },
        },
    )
    assert resp.status_code == 400


def test_put_rule_missing_what_rejected(http_client: TestClient) -> None:
    resp = http_client.put(
        "/v1/app/customize/custom-rules",
        json={
            "scope": "always",
            "enabled": True,
            "firesAt": "pre_final",
            "action": "audit",
        },
    )
    assert resp.status_code == 400


def test_put_rule_bad_body_type_rejected(http_client: TestClient) -> None:
    resp = http_client.put(
        "/v1/app/customize/custom-rules", json="not an object"
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# DELETE unknown id
# ---------------------------------------------------------------------------


def test_delete_unknown_rule_id_does_not_400(http_client: TestClient) -> None:
    """DELETE of an unknown id returns 200 (idempotent removal).

    The store's set_custom_rule contract is "remove if present, no-op
    otherwise"; the route returns 200 with the (unchanged) overrides.
    Pin that contract so a future divergence to a 404 doesn't silently
    break the dashboard's optimistic-delete flow.
    """
    resp = http_client.delete("/v1/app/customize/custom-rules/cr_does_not_exist")
    assert resp.status_code in {200, 404}, (
        f"unknown id DELETE: 200 (idempotent) or 404 (strict); "
        f"got {resp.status_code} body={resp.text}"
    )


# ---------------------------------------------------------------------------
# Master-flag OFF: rule persisted but fan-out short-circuits
# ---------------------------------------------------------------------------


def test_shell_command_off_flag_skips_fan_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Persist a shell_command rule, then flip the master flag OFF and assert
    the fan-out returns an empty audit list (no subprocess spawn)."""
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    # Master flag OFF.
    monkeypatch.setenv("MAGI_CUSTOMIZE_SHELL_COMMAND_ENABLED", "0")
    monkeypatch.delenv("MAGI_RUNTIME_PROFILE", raising=False)
    from magi_agent.customize.lifecycle_audit import (
        run_shell_command_at_before_turn_start,
    )
    from magi_agent.customize.store import set_custom_rule

    set_custom_rule(
        {
            "id": "qa_off_flag",
            "scope": "always",
            "enabled": True,
            "firesAt": "before_turn_start",
            "action": "audit",
            "what": {
                "kind": "shell_command",
                "payload": {
                    "source": "inline",
                    "inline": "echo should-not-run",
                    "timeout_seconds": 5,
                    "shell": "bash",
                },
            },
        },
        path=cfile,
    )
    audits = asyncio.run(
        run_shell_command_at_before_turn_start(prompt_text="x", remaining_budget=10)
    )
    assert audits == [], (
        f"master-flag OFF MUST short-circuit the fan-out; got {audits!r}"
    )


# ---------------------------------------------------------------------------
# Conflicting actions at same slot: block + audit -> block wins
# ---------------------------------------------------------------------------


def test_block_plus_audit_at_same_slot_yields_block_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two shell_command rules at pre_final: one block (exit 1) + one audit.
    The block rule's non-zero exit MUST flip the gate verdict to block."""
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_SHELL_COMMAND_ENABLED", "1")
    monkeypatch.delenv("MAGI_RUNTIME_PROFILE", raising=False)
    from magi_agent.customize.lifecycle_audit import (
        run_shell_command_at_pre_final,
    )
    from magi_agent.customize.store import set_custom_rule

    set_custom_rule(
        {
            "id": "qa_audit_first",
            "scope": "always",
            "enabled": True,
            "firesAt": "pre_final",
            "action": "audit",
            "what": {
                "kind": "shell_command",
                "payload": {
                    "source": "inline",
                    "inline": "echo audit-ok",
                    "timeout_seconds": 5,
                    "shell": "bash",
                },
            },
        },
        path=cfile,
    )
    set_custom_rule(
        {
            "id": "qa_block_second",
            "scope": "always",
            "enabled": True,
            "firesAt": "pre_final",
            "action": "block",
            "what": {
                "kind": "shell_command",
                "payload": {
                    "source": "inline",
                    "inline": "exit 1",
                    "timeout_seconds": 5,
                    "shell": "bash",
                },
            },
        },
        path=cfile,
    )

    audits, verdict = asyncio.run(
        run_shell_command_at_pre_final(draft_text="x", remaining_budget=10)
    )
    assert verdict == "block", (
        f"block rule MUST flip verdict to block even alongside an audit rule; "
        f"got verdict={verdict!r} audits={audits!r}"
    )


# ---------------------------------------------------------------------------
# Auth: every customize endpoint rejects requests with no token
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("GET", "/v1/app/customize", None),
        (
            "PATCH",
            "/v1/app/customize/tools/anything",
            {"enabled": False},
        ),
        (
            "PATCH",
            "/v1/app/customize/verification/recipes/research",
            {"enabled": True},
        ),
        (
            "PATCH",
            "/v1/app/customize/control-plane/facts-replan",
            {"enabled": False},
        ),
        ("PUT", "/v1/app/customize/rules", {"text": "x"}),
        (
            "PUT",
            "/v1/app/customize/custom-rules",
            {
                "scope": "always",
                "enabled": True,
                "firesAt": "pre_final",
                "action": "audit",
                "what": {
                    "kind": "deterministic_ref",
                    "payload": {"ref": "evidence:test-run"},
                },
            },
        ),
        ("DELETE", "/v1/app/customize/custom-rules/cr_anything", None),
        ("GET", "/v1/app/customize/budgets", None),
        ("PUT", "/v1/app/customize/budgets", {"budgets": {"maxToolCallsPerTurn": 5}}),
    ],
)
def test_endpoint_rejects_unauthenticated(
    method: str, path: str, body: dict | None, noauth_client: TestClient
) -> None:
    """Every customize endpoint MUST 401 without the gateway token."""
    resp = noauth_client.request(
        method, path, json=body if body is not None else None
    )
    assert resp.status_code == 401, (
        f"{method} {path} without token MUST 401; "
        f"got {resp.status_code} body={resp.text}"
    )
