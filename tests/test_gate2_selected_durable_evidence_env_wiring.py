"""End-to-end regression: REAL env-builder durable evidence wiring for Gate 2.

PR #1311 tests exercised durable evidence by MANUALLY constructing and injecting
a ``Gate2DurableEvidenceStore`` into the runtime config via
``_runtime_with_evidence()``. They never exercised the live path where the
handler calls ``build_gate2_sandbox_workspace_canary_config_from_env(os.environ,
...)`` to build the store. The live Gate 2 Activation Loop A therefore failed:
the durable evidence file at ``<sandbox_root>/.gate2-evidence/durable-evidence.json``
did not exist after a successful HTTP 200.

These tests:
1. Use ONLY the real env-builder path (no manual store injection). The handler
   reads ``os.environ`` directly (chat.py line 991), so the gate2 env vars are
   set on the real process environment via ``monkeypatch.setenv``.
2. Assert the durable evidence FILE is actually created on disk at the
   configured ``<sandbox_root>/.gate2-evidence/durable-evidence.json`` path.
3. Assert the file contains all 5 evidence categories.
4. Assert the file survives the sandbox canary mutation+rollback lifecycle.
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


def _gate2_activation_env(sandbox_root: Path, **overrides: str) -> dict[str, str]:
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
        "CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_ROOT": str(sandbox_root),
    }
    env.update(overrides)
    return env


def _expected_evidence_path(sandbox_root: Path) -> Path:
    return sandbox_root / ".gate2-evidence" / "durable-evidence.json"


def _apply_env(monkeypatch, env: dict[str, str]) -> None:
    """Set the env vars on the real process environment.

    The live handler reads ``os.environ`` directly via
    ``build_gate2_sandbox_workspace_canary_config_from_env(os.environ, ...)``,
    so the only way to exercise the real wiring is to set these on os.environ.
    """
    for key, value in env.items():
        monkeypatch.setenv(key, value)


def _canary_body(suffix: str = "test") -> dict[str, object]:
    return {
        "gate": "gate2_sandbox_workspace_canary",
        "gate2Canary": {
            "action": "FileCreate",
            "relativePath": f"gate2-loop-a/src/evidence-{suffix}.txt",
            "content": f"evidence content {suffix}",
            "idempotencyKey": f"evidence-{suffix}",
        },
    }


def _env_wiring_canary_body() -> dict[str, object]:
    return {
        "gate": "gate2_sandbox_workspace_canary",
        "gate2Canary": {
            "action": "FileCreate",
            "relativePath": "gate2-loop-a/src/env-wiring.txt",
            "content": "safe synthetic env wiring canary",
            "idempotencyKey": "gate2-loop-a-env-wiring",
        },
    }


def _post_gate2_env_built(
    monkeypatch,
    env: dict[str, str],
    body: dict[str, object],
) -> tuple[int, dict[str, object]]:
    """POST a Gate 2 request using ONLY the real env-builder path.

    The runtime is created with NO pre-attached
    ``gate2_sandbox_workspace_canary_config`` so the handler falls through to
    ``build_gate2_sandbox_workspace_canary_config_from_env(os.environ, ...)``.
    """
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    _apply_env(monkeypatch, env)

    runtime = OpenMagiRuntime(config=parse_runtime_env(env))
    # IMPORTANT: do NOT set runtime.gate2_sandbox_workspace_canary_config — force
    # the handler to use the real env-builder.
    assert getattr(runtime, "gate2_sandbox_workspace_canary_config", None) is None

    headers = {
        "Authorization": "Bearer gateway-token",
        "x-gate2-canary-request-digest": _gate2_js_compatible_request_digest(body),
        "x-gate2-canary-body-digest": _stable_json_digest(body),
    }
    response = TestClient(create_app(runtime)).post(
        "/v1/chat/completions",
        headers=headers,
        json=body,
    )
    return response.status_code, response.json()


def test_env_built_selected_path_writes_evidence_file_to_disk(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """REAL env-builder path: durable evidence FILE must exist on disk at the
    configured ``<sandbox_root>/.gate2-evidence/durable-evidence.json``.

    This reproduces the live Gate 2 Activation Loop A failure: HTTP 200 + SSE
    succeeded but the durable evidence file did not exist.
    """
    sandbox_root = tmp_path / "gate2-sandboxes" / "gate2-sandbox"
    env = _base_env(
        **_gate2_readiness_env(),
        **_gate2_activation_env(
            sandbox_root,
            CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_SELECTED_PROVIDER_ENABLED="1",
        ),
    )

    status, body = _post_gate2_env_built(monkeypatch, env, _canary_body("ondisk"))

    assert status == 200
    assert body["status"] == "gate2_selected_sandbox_canary_completed"
    assert body["responseAuthority"] == "python"
    assert body["routeDecision"] == "python_selected_gate2_sandbox"
    assert body["durableEvidence"]["present"] is True
    assert body["durableEvidence"]["error"] is None

    # The durable evidence FILE must exist on disk at the configured path.
    evidence_path = _expected_evidence_path(sandbox_root)
    assert evidence_path.exists(), (
        f"durable evidence file missing at {evidence_path} — live wiring gap"
    )

    data = json.loads(evidence_path.read_text(encoding="utf-8"))
    records = data.get("records", {})
    assert len(records) == 1
    record = next(iter(records.values()))

    # All 5 categories present in the persisted record.
    assert record["requestDigest"] == body["requestDigest"]
    assert record.get("requestDigest", "").startswith("sha256:")  # request_record
    assert record.get("counterIncrementedAtMs", 0) > 0  # counter_increment
    assert record.get("deliveryReceipt") is not None  # delivery_receipt
    assert record.get("mutationReceipt") is not None  # mutation_receipt
    assert record.get("rollbackReceipt") is not None  # rollback_receipt
    assert record["mutationReceipt"]["receiptDigest"].startswith("sha256:")
    assert record["rollbackReceipt"]["rollbackDigest"] is not None

    counters = data.get("counters", {})
    assert counters.get("gate2Records") == 1
    assert counters.get("deliveryReceipts") == 1


def test_env_built_selected_env_wiring_path_creates_parent_dirs_and_evidence(
    monkeypatch,
    tmp_path: Path,
) -> None:
    sandbox_root = tmp_path / "gate2-sandboxes" / "gate2-sandbox"
    env = _base_env(
        **_gate2_readiness_env(),
        **_gate2_activation_env(
            sandbox_root,
            CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_SELECTED_PROVIDER_ENABLED="1",
        ),
    )

    assert not sandbox_root.exists()

    status, body = _post_gate2_env_built(monkeypatch, env, _env_wiring_canary_body())

    assert status == 200
    assert body["status"] == "gate2_selected_sandbox_canary_completed"
    assert body["responseAuthority"] == "python"
    assert body["routeDecision"] == "python_selected_gate2_sandbox"
    assert body["durableEvidence"]["present"] is True
    assert body["durableEvidence"]["error"] is None
    assert body["productionWorkspaceMutationAllowed"] is False
    assert body["writeMutationAuthorityAllowed"] is False
    assert body["toolHostDispatchAllowed"] is False
    assert body["rollbackReceipt"]["rollbackVerified"] is True
    assert not (sandbox_root / "gate2-loop-a" / "src" / "env-wiring.txt").exists()
    assert _expected_evidence_path(sandbox_root).exists()


def test_env_built_selected_env_wiring_path_accepts_safe_root_namespace(
    monkeypatch,
    tmp_path: Path,
) -> None:
    sandbox_root = (
        tmp_path
        / "openmagi-gate2-sandboxes"
        / "bot-gate2"
        / "gate2-sandbox"
    )
    env = _base_env(
        **_gate2_readiness_env(),
        **_gate2_activation_env(
            sandbox_root,
            CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_SELECTED_PROVIDER_ENABLED="1",
        ),
    )

    status, body = _post_gate2_env_built(monkeypatch, env, _env_wiring_canary_body())

    assert status == 200
    assert body["status"] == "gate2_selected_sandbox_canary_completed"
    assert body["responseAuthority"] == "python"
    assert body["routeDecision"] == "python_selected_gate2_sandbox"
    assert body["durableEvidence"]["present"] is True
    assert body["durableEvidence"]["error"] is None
    assert body["productionWorkspaceMutationAllowed"] is False
    assert body["writeMutationAuthorityAllowed"] is False
    assert body["toolHostDispatchAllowed"] is False
    assert body["rollbackReceipt"]["rollbackVerified"] is True
    assert not (sandbox_root / "gate2-loop-a" / "src" / "env-wiring.txt").exists()
    assert _expected_evidence_path(sandbox_root).exists()


def test_env_built_evidence_file_survives_sandbox_rollback(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """The evidence file must persist AFTER the sandbox canary mutation+rollback.

    The canary writes a file under the sandbox root then rolls it back (deleting
    or restoring), pruning empty dirs. The ``.gate2-evidence`` directory must NOT
    be pruned/cleaned away — the evidence must survive.
    """
    sandbox_root = tmp_path / "gate2-sandboxes" / "gate2-sandbox"
    env = _base_env(
        **_gate2_readiness_env(),
        **_gate2_activation_env(
            sandbox_root,
            CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_SELECTED_PROVIDER_ENABLED="1",
        ),
    )

    status, body = _post_gate2_env_built(monkeypatch, env, _canary_body("survive"))
    assert status == 200

    evidence_path = _expected_evidence_path(sandbox_root)
    # The mutated sandbox target file must have been rolled back (deleted)...
    mutated = sandbox_root / "gate2-loop-a" / "src" / "evidence-survive.txt"
    assert not mutated.exists(), "sandbox mutation should have been rolled back"
    # ...but the durable evidence must still be present.
    assert evidence_path.exists(), "evidence must survive sandbox rollback/cleanup"
    data = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert len(data.get("records", {})) == 1


def test_env_built_selected_provider_without_root_fails_closed(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Selected provider enabled but no sandbox root → clean 503, not a 500/crash.

    With no ``CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_ROOT`` the env-builder
    produces ``sandbox_root=None`` and ``durable_evidence_store=None``. The gate
    must fail-closed rather than raise an AssertionError (which would also be
    stripped under ``python -O``)."""
    env = _base_env(
        **_gate2_readiness_env(),
        **_gate2_activation_env(
            tmp_path / "gate2-sandboxes" / "gate2-sandbox",
            CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_SELECTED_PROVIDER_ENABLED="1",
        ),
    )
    # Drop the root so the selected provider has no place to persist evidence.
    env.pop("CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_ROOT", None)
    monkeypatch.delenv("CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_ROOT", raising=False)

    status, body = _post_gate2_env_built(monkeypatch, env, _canary_body("noroot"))

    # Must be a clean fail-closed, never a 200 success without durable evidence,
    # and never an uncaught AssertionError (500).
    assert status == 503
    assert body["status"] in {"python_error", "python_disabled"}
    assert body["reason"] in {
        "gate2_sandbox_root_unavailable",
        "canary_gate_disabled",
        "python_disabled",
    }


def test_env_built_selected_path_fails_closed_if_file_vanishes(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """If the evidence file is deleted between write and readback (e.g. sandbox
    cleanup wiping the path), the disk-readback verification must fail-close."""
    import magi_agent.transport.chat as chat_module

    sandbox_root = tmp_path / "gate2-sandboxes" / "gate2-sandbox"
    env = _base_env(
        **_gate2_readiness_env(),
        **_gate2_activation_env(
            sandbox_root,
            CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_SELECTED_PROVIDER_ENABLED="1",
        ),
    )

    # Simulate ephemeral-path deletion: drop the file right after the in-memory
    # record reports success, before the handler's disk-readback runs.
    original_record_all = (
        chat_module.Gate2DurableEvidenceStore.record_all_evidence
    )

    def _record_then_vanish(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        outcome = original_record_all(self, *args, **kwargs)
        if self.store_path.exists():
            self.store_path.unlink()
        return outcome

    monkeypatch.setattr(
        chat_module.Gate2DurableEvidenceStore,
        "record_all_evidence",
        _record_then_vanish,
    )

    status, body = _post_gate2_env_built(monkeypatch, env, _canary_body("vanish"))

    assert status == 503
    assert body["status"] == "python_error"
    assert body["reason"] == "evidence_file_missing"
    assert not _expected_evidence_path(sandbox_root).exists()


def test_env_built_non_selected_path_creates_no_evidence_file(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Diagnostic-only (provider NOT enabled) path must not create an evidence
    file, and must still return 200 via the real env-builder path."""
    sandbox_root = tmp_path / "gate2-sandboxes" / "gate2-sandbox"
    env = _base_env(
        **_gate2_readiness_env(),
        **_gate2_activation_env(sandbox_root),
        # SELECTED_PROVIDER_ENABLED intentionally omitted
    )

    status, body = _post_gate2_env_built(monkeypatch, env, _canary_body("diag"))

    assert status == 200
    assert body["status"] == "gate2_sandbox_workspace_canary_completed"
    assert body["responseAuthority"] == "typescript"
    assert body["durableEvidence"]["present"] is False

    evidence_path = _expected_evidence_path(sandbox_root)
    assert not evidence_path.exists()
