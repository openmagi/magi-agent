"""Tests for the selected Gate 2 sandbox canary response path.

Verifies that when selected Gate 2 sandbox canary metadata is valid,
Python emits:
  - status = "gate2_selected_sandbox_canary_completed"
  - responseAuthority = "python"
  - routeDecision = "python_selected_gate2_sandbox"
  - productionWorkspaceMutationAllowed = False
  - Durable sandbox mutation receipt
  - Durable rollback/delete receipt

And when metadata is missing/invalid/non-selected, emits diagnostic-only:
  - status = "gate2_sandbox_workspace_canary_completed"
  - responseAuthority = "typescript"
  - routeDecision = "python_diagnostic_only"
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient

from magi_agent.app import create_app
from magi_agent.config.env import parse_runtime_env
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime
from magi_agent.shadow.gate2_recipe_profile_resolver import (
    resolve_gate2_recipe_profile,
)
from magi_agent.transport.chat import (
    build_gate2_sandbox_workspace_canary_config_from_env,
)


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _stable_json_digest(value: object) -> str:
    return _digest(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    )


def _gate2_js_compatible_request_digest(body: dict[str, object]) -> str:
    canary = body.get("gate2Canary") if isinstance(body.get("gate2Canary"), dict) else {}
    content = canary.get("content") if isinstance(canary.get("content"), str) else ""
    relative_path = str(canary.get("relativePath") or "").strip()
    idempotency_key = str(canary.get("idempotencyKey") or "").strip()
    summary = {
        "gate": "gate2_sandbox_workspace_canary",
        "botId": "bot-gate2",
        "ownerUserId": "owner-gate2",
        "environment": "staging",
        "messages": [],
        "action": str(canary.get("action") or "").strip(),
        "relativePathDigest": _digest(relative_path) if relative_path else None,
        "contentDigest": _digest(content) if content else None,
        "idempotencyKeyDigest": _digest(idempotency_key) if idempotency_key else None,
        "patchDigest": None,
        "bodyDigest": _stable_json_digest(body),
    }
    return _stable_json_digest(summary)


def _base_env(**overrides: str) -> dict[str, str]:
    env = {
        "BOT_ID": "bot-gate2",
        "USER_ID": "owner-gate2",
        "GATEWAY_TOKEN": "gateway-token",
        "CORE_AGENT_API_PROXY_URL": "http://api-proxy.local",
        "CORE_AGENT_CHAT_PROXY_URL": "http://chat-proxy.local",
        "CORE_AGENT_REDIS_URL": "redis://redis.local:6379/0",
        "CORE_AGENT_MODEL": "gpt-5.2",
    }
    env.update(overrides)
    return env


def _gate2_readiness_env() -> dict[str, str]:
    profile = resolve_gate2_recipe_profile("openmagi.gate2.workspace-canary.v1")
    return {
        "CORE_AGENT_PYTHON_GATE2_READINESS_ENABLED": "1",
        "CORE_AGENT_PYTHON_GATE2_READINESS_KILL_SWITCH": "0",
        "CORE_AGENT_PYTHON_GATE2_READINESS_LOCAL_SANDBOX_HARNESS": "1",
        "CORE_AGENT_PYTHON_GATE2_READINESS_SELECTED_BOT_DIGEST": _digest("bot-gate2"),
        "CORE_AGENT_PYTHON_GATE2_READINESS_TRUSTED_OWNER_USER_ID_DIGEST": _digest(
            "owner-gate2"
        ),
        "CORE_AGENT_PYTHON_GATE2_READINESS_ENVIRONMENT": "staging",
        "CORE_AGENT_PYTHON_GATE2_READINESS_ENV_ALLOWLIST": "staging",
        "CORE_AGENT_PYTHON_GATE2_READINESS_PROFILE_REF": profile.profile_ref,
        "CORE_AGENT_PYTHON_GATE2_READINESS_PROFILE_DIGEST": profile.profile_digest,
    }


def _gate2_activation_env(tmp_path: Path, **overrides: str) -> dict[str, str]:
    env = {
        "CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_ENABLED": "1",
        "CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_KILL_SWITCH": "0",
        "CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_SELECTED_BOT_DIGEST": _digest(
            "bot-gate2"
        ),
        "CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_TRUSTED_OWNER_USER_ID_DIGEST": _digest(
            "owner-gate2"
        ),
        "CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_ENVIRONMENT": "staging",
        "CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_ENV_ALLOWLIST": "staging",
        "CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_ROOT": str(
            tmp_path / "gate2-sandboxes" / "gate2-sandbox"
        ),
    }
    env.update(overrides)
    return env


def _runtime(env: dict[str, str]) -> OpenMagiRuntime:
    runtime = OpenMagiRuntime(config=parse_runtime_env(env))
    runtime.gate2_sandbox_workspace_canary_config = (
        build_gate2_sandbox_workspace_canary_config_from_env(env, runtime.config)
    )
    return runtime


def _post_gate2(
    runtime: OpenMagiRuntime,
    body: dict[str, object],
    *,
    headers: dict[str, str] | None = None,
    auto_digest_header: bool = True,
) -> tuple[int, dict[str, object]]:
    request_headers = dict(headers or {})
    if (
        auto_digest_header
        and body.get("gate") == "gate2_sandbox_workspace_canary"
        and "gate2Canary" in body
    ):
        request_headers.setdefault(
            "x-gate2-canary-request-digest",
            _gate2_js_compatible_request_digest(body),
        )
        request_headers.setdefault("x-gate2-canary-body-digest", _stable_json_digest(body))
    response = TestClient(create_app(runtime)).post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer gateway-token", **request_headers},
        json=body,
    )
    return response.status_code, response.json()


# ── Selected path tests ──────────────────────────────────────────────────────


def test_selected_gate2_sandbox_canary_emits_selected_status_and_python_authority(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """When selected mutation provider is enabled, Python must emit
    gate2_selected_sandbox_canary_completed with python authority."""
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    env = _base_env(
        **_gate2_readiness_env(),
        **_gate2_activation_env(
            tmp_path,
            CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_SELECTED_PROVIDER_ENABLED="1",
        ),
    )
    runtime = _runtime(env)

    status, body = _post_gate2(
        runtime,
        {
            "gate": "gate2_sandbox_workspace_canary",
            "gate2Canary": {
                "action": "FileCreate",
                "relativePath": "gate2-loop-a/src/selected-test.txt",
                "content": "selected canary content must not leak",
                "idempotencyKey": "selected-test-1",
            },
        },
    )

    assert status == 200
    assert body["status"] == "gate2_selected_sandbox_canary_completed"
    assert body["responseAuthority"] == "python"
    assert body["routeDecision"] == "python_selected_gate2_sandbox"
    assert body["productionWorkspaceMutationAllowed"] is False
    assert body["diagnosticOnly"] is True
    assert body["localOnly"] is True
    assert body["fakeOnly"] is True
    assert body["gate"] == "gate2_sandbox_workspace_canary"
    assert body["choices"][0]["message"]["content"] == "Sandbox workspace check completed."

    # Mutation receipt present with digest-only values
    assert body["workspaceMutationReceipt"]["status"] == "simulated"
    assert body["workspaceMutationReceipt"]["receiptDigest"].startswith("sha256:")

    # Rollback receipt present
    assert body["rollbackReceipt"]["rollbackDigest"].startswith("sha256:")
    assert body["rollbackReceipt"]["rollbackVerified"] is True

    # No raw content leaked
    encoded = json.dumps(body, sort_keys=True)
    assert "selected-test.txt" not in encoded
    assert "selected canary content" not in encoded
    assert "must not leak" not in encoded


def test_selected_gate2_sandbox_canary_uses_sandbox_mutation_provider_receipts(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Selected path must produce durable mutation + rollback receipts
    from the sandbox mutation provider."""
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    env = _base_env(
        **_gate2_readiness_env(),
        **_gate2_activation_env(
            tmp_path,
            CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_SELECTED_PROVIDER_ENABLED="1",
        ),
    )
    runtime = _runtime(env)

    status, body = _post_gate2(
        runtime,
        {
            "gate": "gate2_sandbox_workspace_canary",
            "gate2Canary": {
                "action": "FileCreate",
                "relativePath": "gate2-loop-a/src/receipt-test.txt",
                "content": "receipt verification content",
                "idempotencyKey": "receipt-test-1",
            },
        },
    )

    assert status == 200
    assert body["status"] == "gate2_selected_sandbox_canary_completed"

    # Verify mutation receipt has all required digest fields
    receipt = body["workspaceMutationReceipt"]
    assert receipt["receiptDigest"].startswith("sha256:")
    assert receipt["requestDigest"].startswith("sha256:")
    assert receipt["pathDigest"].startswith("sha256:")

    # Verify rollback receipt has all required digest fields
    rollback = body["rollbackReceipt"]
    assert rollback["rollbackDigest"].startswith("sha256:")
    assert rollback["rollbackAction"] in ("delete", "restore")
    assert rollback["rollbackVerified"] is True
    assert rollback["postRollbackDigest"].startswith("sha256:")

    # Before/after digests must be present
    assert body["beforeDigest"].startswith("sha256:")
    assert body["afterDigest"].startswith("sha256:")
    assert body["readbackDigest"] == body["afterDigest"]

    # Sandbox file must NOT persist after rollback
    assert not (
        tmp_path
        / "gate2-sandboxes"
        / "gate2-sandbox"
        / "gate2-loop-a"
        / "src"
        / "receipt-test.txt"
    ).exists()


def test_selected_gate2_sandbox_canary_does_not_fall_through_to_ts(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Successful selected Gate 2 sandbox canary must NOT fall through
    to normal TS core-agent handling — response must be terminal."""
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    env = _base_env(
        **_gate2_readiness_env(),
        **_gate2_activation_env(
            tmp_path,
            CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_SELECTED_PROVIDER_ENABLED="1",
        ),
    )
    runtime = _runtime(env)

    status, body = _post_gate2(
        runtime,
        {
            "gate": "gate2_sandbox_workspace_canary",
            "gate2Canary": {
                "action": "FileCreate",
                "relativePath": "gate2-loop-a/src/no-fallthrough.txt",
                "content": "must not reach TS",
                "idempotencyKey": "no-fallthrough-1",
            },
        },
    )

    # Must return 200, not 503 (which would indicate fallthrough to TS)
    assert status == 200
    # Must NOT have typescript authority
    assert body["responseAuthority"] != "typescript"
    assert body["routeDecision"] != "python_diagnostic_only"
    # Must have all authority fields disabled
    authority = body["authority"]
    for key in [
        "userVisibleOutputAllowed",
        "workspaceMutationAllowed",
        "memoryWriteAllowed",
        "channelWritesAllowed",
        "dbWritesAllowed",
        "transcriptWritesAllowed",
        "sseWritesAllowed",
        "childExecutionAllowed",
    ]:
        assert authority[key] is False, f"authority.{key} must be False"


# ── Non-selected / diagnostic-only path tests ────────────────────────────────


def test_non_selected_gate2_emits_diagnostic_only_with_typescript_authority(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """When selected provider is NOT enabled, completed canary must emit
    diagnostic-only with typescript authority."""
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    env = _base_env(
        **_gate2_readiness_env(),
        **_gate2_activation_env(tmp_path),
        # Explicitly NOT setting SELECTED_PROVIDER_ENABLED
    )
    runtime = _runtime(env)

    status, body = _post_gate2(
        runtime,
        {
            "gate": "gate2_sandbox_workspace_canary",
            "gate2Canary": {
                "action": "FileCreate",
                "relativePath": "gate2-loop-a/src/diagnostic-test.txt",
                "content": "diagnostic only content",
                "idempotencyKey": "diagnostic-test-1",
            },
        },
    )

    assert status == 200
    assert body["status"] == "gate2_sandbox_workspace_canary_completed"
    assert body["responseAuthority"] == "typescript"
    assert body["routeDecision"] == "python_diagnostic_only"
    assert body["productionWorkspaceMutationAllowed"] is False
    assert body["diagnosticOnly"] is True


def test_non_selected_gate2_with_explicit_off_emits_diagnostic_only(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """When selected provider is explicitly set to '0', must emit
    diagnostic-only with typescript authority."""
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    env = _base_env(
        **_gate2_readiness_env(),
        **_gate2_activation_env(
            tmp_path,
            CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_SELECTED_PROVIDER_ENABLED="0",
        ),
    )
    runtime = _runtime(env)

    status, body = _post_gate2(
        runtime,
        {
            "gate": "gate2_sandbox_workspace_canary",
            "gate2Canary": {
                "action": "FileCreate",
                "relativePath": "gate2-loop-a/src/explicit-off.txt",
                "content": "diagnostic content",
                "idempotencyKey": "explicit-off-1",
            },
        },
    )

    assert status == 200
    assert body["status"] == "gate2_sandbox_workspace_canary_completed"
    assert body["responseAuthority"] == "typescript"
    assert body["routeDecision"] == "python_diagnostic_only"


# ── Unsafe/missing metadata tests ────────────────────────────────────────────


def test_selected_gate2_denies_forbidden_path_still_uses_typescript_authority(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Even with selected provider enabled, forbidden paths must block
    and use typescript authority."""
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    env = _base_env(
        **_gate2_readiness_env(),
        **_gate2_activation_env(
            tmp_path,
            CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_SELECTED_PROVIDER_ENABLED="1",
        ),
    )
    runtime = _runtime(env)

    status, body = _post_gate2(
        runtime,
        {
            "gate": "gate2_sandbox_workspace_canary",
            "gate2Canary": {
                "action": "FileEdit",
                "relativePath": "../escape.txt",
                "content": "must not write",
                "idempotencyKey": "forbidden-path-1",
            },
        },
    )

    assert status == 409
    assert body["status"] == "gate2_sandbox_workspace_canary_blocked"
    assert body["responseAuthority"] == "typescript"
    assert body["workspaceMutationReceipt"]["status"] == "denied"


def test_selected_gate2_missing_digest_headers_still_uses_typescript(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Missing digest headers must reject even with selected provider enabled."""
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    env = _base_env(
        **_gate2_readiness_env(),
        **_gate2_activation_env(
            tmp_path,
            CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_SELECTED_PROVIDER_ENABLED="1",
        ),
    )
    runtime = _runtime(env)

    status, body = _post_gate2(
        runtime,
        {
            "gate": "gate2_sandbox_workspace_canary",
            "gate2Canary": {
                "action": "FileCreate",
                "relativePath": "gate2-loop-a/src/no-header.txt",
                "content": "no header content",
                "idempotencyKey": "no-header-1",
            },
        },
        auto_digest_header=False,
    )

    assert status == 400
    assert body["responseAuthority"] == "typescript"
    assert body["reason"] == "gate2_request_digest_mismatch"


# ── Gate 1A / Gate 8 preservation tests ──────────────────────────────────────


def test_gate1a_behavior_preserved_with_selected_provider_enabled(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Gate 1A readonly tools should work independently of Gate 2 selected
    provider. Non-gate2 requests should not be intercepted."""
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    env = _base_env(
        **_gate2_readiness_env(),
        **_gate2_activation_env(
            tmp_path,
            CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_SELECTED_PROVIDER_ENABLED="1",
        ),
    )
    runtime = _runtime(env)

    # Non-gate2 request should not be intercepted by gate2 handler
    status, body = _post_gate2(
        runtime,
        {
            "messages": [{"role": "user", "content": "hello"}],
        },
    )

    # Should fall through to non-gate2 path (503 for disabled canary, not gate2)
    assert status == 503
    assert body["responseAuthority"] == "typescript"
    assert "gate2_selected_sandbox_canary" not in json.dumps(body, sort_keys=True)


def test_prod_mutation_always_false_in_selected_path(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """productionWorkspaceMutationAllowed must always be False regardless
    of selected provider state."""
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    env = _base_env(
        **_gate2_readiness_env(),
        **_gate2_activation_env(
            tmp_path,
            CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_SELECTED_PROVIDER_ENABLED="1",
        ),
    )
    runtime = _runtime(env)

    status, body = _post_gate2(
        runtime,
        {
            "gate": "gate2_sandbox_workspace_canary",
            "gate2Canary": {
                "action": "FileCreate",
                "relativePath": "gate2-loop-a/src/prod-check.txt",
                "content": "production check content",
                "idempotencyKey": "prod-check-1",
            },
        },
    )

    assert status == 200
    assert body["productionWorkspaceMutationAllowed"] is False
    assert body["writeMutationAuthorityAllowed"] is False
    assert body["toolHostDispatchAllowed"] is False
    assert body["authority"]["workspaceMutationAllowed"] is False
