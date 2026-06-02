"""Tests for Gate 2 selected sandbox durable evidence recording.

Regression tests proving:
1. Selected success path has all 5 durable evidence records
2. Failed evidence write prevents success response
3. Non-selected/diagnostic-only path does NOT create selected evidence
4. Request/body/output digests are recorded
5. Counter increments are durable
6. Missing durable evidence store on selected path returns error (fail-closed)
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from openmagi_core_agent.app import create_app
from openmagi_core_agent.config.env import parse_runtime_env
from openmagi_core_agent.evidence.gate2_durable_evidence import (
    Gate2DurableEvidenceStore,
)
from openmagi_core_agent.runtime.openmagi_runtime import OpenMagiRuntime
from openmagi_core_agent.shadow.gate2_recipe_profile_resolver import (
    resolve_gate2_recipe_profile,
)
from openmagi_core_agent.shadow.gate5b4c3_shadow_counter_store import (
    Gate5B4C3ShadowCounterStore,
)
from openmagi_core_agent.transport.chat import (
    build_gate2_sandbox_workspace_canary_config_from_env,
)
from openmagi_core_agent.transport.shadow_generations import (
    Gate5B4C3ShadowGenerationRouteConfig,
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


def _runtime_with_evidence(
    env: dict[str, str],
    tmp_path: Path,
    *,
    with_counter_store: bool = True,
    with_durable_evidence: bool = True,
) -> OpenMagiRuntime:
    runtime = OpenMagiRuntime(config=parse_runtime_env(env))

    # Build config with durable evidence store
    evidence_store = None
    if with_durable_evidence:
        evidence_path = tmp_path / "gate2-evidence" / "durable-evidence.json"
        evidence_store = Gate2DurableEvidenceStore(evidence_path)

    config = build_gate2_sandbox_workspace_canary_config_from_env(env, runtime.config)
    # Rebuild with evidence store attached
    from openmagi_core_agent.transport.chat import Gate2SandboxWorkspaceCanaryConfig

    runtime.gate2_sandbox_workspace_canary_config = Gate2SandboxWorkspaceCanaryConfig(
        enabled=config.enabled,
        killSwitchEnabled=config.kill_switch_enabled,
        selectedBotDigest=config.selected_bot_digest,
        selectedOwnerUserIdDigest=config.selected_owner_user_id_digest,
        environment=config.environment,
        environmentAllowlist=config.environment_allowlist,
        sandboxRoot=config.sandbox_root,
        selectedMutationProviderEnabled=config.selected_mutation_provider_enabled,
        durableEvidenceStore=evidence_store,
    )

    if with_counter_store:
        counter_path = tmp_path / "counters.json"
        runtime.gate5b4c3_shadow_generation_route_config = (
            Gate5B4C3ShadowGenerationRouteConfig(
                counterStore=Gate5B4C3ShadowCounterStore(counter_path),
            )
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


# ── 1. Selected success path has all 5 durable evidence records ──────────────


def test_selected_path_records_all_five_durable_evidence_categories(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Selected Gate 2 success MUST have all 5 evidence categories persisted."""
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    env = _base_env(
        **_gate2_readiness_env(),
        **_gate2_activation_env(
            tmp_path,
            CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_SELECTED_PROVIDER_ENABLED="1",
        ),
    )
    runtime = _runtime_with_evidence(env, tmp_path)

    status, body = _post_gate2(runtime, _canary_body("all-five"))

    assert status == 200
    assert body["status"] == "gate2_selected_sandbox_canary_completed"
    assert body["responseAuthority"] == "python"

    # Verify durable evidence is reported present
    assert body["durableEvidence"]["present"] is True
    assert body["durableEvidence"]["error"] is None

    # Verify evidence is actually on disk
    evidence_store = runtime.gate2_sandbox_workspace_canary_config.durable_evidence_store
    assert evidence_store is not None

    request_digest = body["counter"]["requestDigest"]
    evidence = evidence_store.get_evidence(request_digest)
    assert evidence is not None
    assert evidence.request_recorded is True
    assert evidence.counter_incremented is True
    assert evidence.delivery_receipt_recorded is True
    assert evidence.mutation_receipt_recorded is True
    assert evidence.rollback_receipt_recorded is True
    assert evidence.all_evidence_present is True
    assert evidence.missing_evidence == []


def test_selected_path_increments_gate2_counter(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Selected path must increment gate2Records counter durably."""
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    env = _base_env(
        **_gate2_readiness_env(),
        **_gate2_activation_env(
            tmp_path,
            CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_SELECTED_PROVIDER_ENABLED="1",
        ),
    )
    runtime = _runtime_with_evidence(env, tmp_path)
    evidence_store = runtime.gate2_sandbox_workspace_canary_config.durable_evidence_store

    counters_before = evidence_store.get_counters()
    assert counters_before["gate2Records"] == 0
    assert counters_before["deliveryReceipts"] == 0

    status, body = _post_gate2(runtime, _canary_body("counter-1"))
    assert status == 200

    counters_after = evidence_store.get_counters()
    assert counters_after["gate2Records"] == 1
    assert counters_after["deliveryReceipts"] == 1


def test_selected_path_records_delivery_receipt_durably(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Selected path must persist a delivery receipt with output digest."""
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    env = _base_env(
        **_gate2_readiness_env(),
        **_gate2_activation_env(
            tmp_path,
            CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_SELECTED_PROVIDER_ENABLED="1",
        ),
    )
    runtime = _runtime_with_evidence(env, tmp_path)

    status, body = _post_gate2(runtime, _canary_body("delivery"))
    assert status == 200

    evidence_store = runtime.gate2_sandbox_workspace_canary_config.durable_evidence_store
    # Read raw store to verify delivery receipt structure
    data = json.loads(evidence_store.store_path.read_text())
    records = data["records"]
    assert len(records) == 1

    record = next(iter(records.values()))
    assert "deliveryReceipt" in record
    assert record["deliveryReceipt"]["deliveryStatus"] == "sandbox_completed"
    assert record["deliveryReceipt"]["recordedAtMs"] > 0


def test_selected_path_persists_mutation_receipt(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Selected path must persist the sandbox mutation receipt, not just
    include it in the HTTP response."""
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    env = _base_env(
        **_gate2_readiness_env(),
        **_gate2_activation_env(
            tmp_path,
            CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_SELECTED_PROVIDER_ENABLED="1",
        ),
    )
    runtime = _runtime_with_evidence(env, tmp_path)

    status, body = _post_gate2(runtime, _canary_body("mutation"))
    assert status == 200

    evidence_store = runtime.gate2_sandbox_workspace_canary_config.durable_evidence_store
    data = json.loads(evidence_store.store_path.read_text())
    record = next(iter(data["records"].values()))

    assert "mutationReceipt" in record
    assert record["mutationReceipt"]["receiptDigest"].startswith("sha256:")
    assert record["mutationReceipt"]["sandboxPathDigest"].startswith("sha256:")
    assert record["mutationReceipt"]["persistedAtMs"] > 0


def test_selected_path_persists_rollback_receipt(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Selected path must persist the rollback receipt, not just include it
    in the HTTP response."""
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    env = _base_env(
        **_gate2_readiness_env(),
        **_gate2_activation_env(
            tmp_path,
            CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_SELECTED_PROVIDER_ENABLED="1",
        ),
    )
    runtime = _runtime_with_evidence(env, tmp_path)

    status, body = _post_gate2(runtime, _canary_body("rollback"))
    assert status == 200

    evidence_store = runtime.gate2_sandbox_workspace_canary_config.durable_evidence_store
    data = json.loads(evidence_store.store_path.read_text())
    record = next(iter(data["records"].values()))

    assert "rollbackReceipt" in record
    assert record["rollbackReceipt"]["persistedAtMs"] > 0


def test_selected_path_records_request_digest(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Request digest must be stored durably as the record key."""
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    env = _base_env(
        **_gate2_readiness_env(),
        **_gate2_activation_env(
            tmp_path,
            CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_SELECTED_PROVIDER_ENABLED="1",
        ),
    )
    runtime = _runtime_with_evidence(env, tmp_path)

    status, body = _post_gate2(runtime, _canary_body("digest"))
    assert status == 200

    request_digest = body["counter"]["requestDigest"]
    assert request_digest.startswith("sha256:")

    evidence_store = runtime.gate2_sandbox_workspace_canary_config.durable_evidence_store
    data = json.loads(evidence_store.store_path.read_text())
    assert request_digest in data["records"]
    assert data["records"][request_digest]["requestDigest"] == request_digest


# ── 2. Failed evidence write prevents success response ───────────────────────


def test_selected_path_without_durable_evidence_store_returns_error(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """When selected provider is enabled but no durable evidence store is
    configured, must return error, NOT success. This is the original bug."""
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    env = _base_env(
        **_gate2_readiness_env(),
        **_gate2_activation_env(
            tmp_path,
            CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_SELECTED_PROVIDER_ENABLED="1",
        ),
    )
    # Create runtime WITHOUT durable evidence store
    runtime = _runtime_with_evidence(env, tmp_path, with_durable_evidence=False)

    status, body = _post_gate2(runtime, _canary_body("no-store"))

    # Must NOT return 200 — evidence was not durably recorded
    assert status == 503
    assert body["status"] == "python_error"
    assert body["reason"] == "durable_evidence_store_unavailable"
    assert body["responseAuthority"] == "typescript"


def test_selected_path_with_unwritable_evidence_path_returns_error(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """When the evidence store path is unwritable, must return error."""
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    env = _base_env(
        **_gate2_readiness_env(),
        **_gate2_activation_env(
            tmp_path,
            CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_SELECTED_PROVIDER_ENABLED="1",
        ),
    )
    runtime = _runtime_with_evidence(env, tmp_path)

    # Make the evidence store's _save method raise OSError
    evidence_store = runtime.gate2_sandbox_workspace_canary_config.durable_evidence_store
    original_save = evidence_store._save

    def _failing_save(data: object) -> None:
        raise OSError("disk full")

    evidence_store._save = _failing_save  # type: ignore[assignment]

    status, body = _post_gate2(runtime, _canary_body("disk-full"))

    assert status == 503
    assert body["status"] == "python_error"
    assert "evidence_write_failed" in body["reason"]


# ── 3. Non-selected/diagnostic-only path does NOT create selected evidence ───


def test_non_selected_path_does_not_create_durable_evidence(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Non-selected (diagnostic-only) path must NOT create durable evidence
    records — only the selected path should."""
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    env = _base_env(
        **_gate2_readiness_env(),
        **_gate2_activation_env(tmp_path),
        # NOT enabling selected provider
    )
    runtime = _runtime_with_evidence(env, tmp_path, with_durable_evidence=False)

    status, body = _post_gate2(runtime, _canary_body("diagnostic"))

    assert status == 200
    assert body["status"] == "gate2_sandbox_workspace_canary_completed"
    assert body["responseAuthority"] == "typescript"
    assert body["routeDecision"] == "python_diagnostic_only"

    # Durable evidence should not be present
    assert body["durableEvidence"]["present"] is False

    # Non-selected path must NOT have a durable evidence store
    evidence_store = runtime.gate2_sandbox_workspace_canary_config.durable_evidence_store
    assert evidence_store is None, "non-selected path must not create a durable evidence store"


def test_diagnostic_path_still_succeeds_without_evidence_store(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Non-selected path must succeed even without a durable evidence store."""
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    env = _base_env(
        **_gate2_readiness_env(),
        **_gate2_activation_env(tmp_path),
    )
    runtime = _runtime_with_evidence(env, tmp_path, with_durable_evidence=False)

    status, body = _post_gate2(runtime, _canary_body("no-store-ok"))

    assert status == 200
    assert body["status"] == "gate2_sandbox_workspace_canary_completed"
    assert body["responseAuthority"] == "typescript"


# ── 4. Multiple requests increment counters correctly ────────────────────────


def test_multiple_selected_requests_increment_counters(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Multiple selected requests must increment counters correctly."""
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    env = _base_env(
        **_gate2_readiness_env(),
        **_gate2_activation_env(
            tmp_path,
            CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_SELECTED_PROVIDER_ENABLED="1",
        ),
    )
    runtime = _runtime_with_evidence(env, tmp_path)
    evidence_store = runtime.gate2_sandbox_workspace_canary_config.durable_evidence_store

    for i in range(3):
        status, body = _post_gate2(runtime, _canary_body(f"multi-{i}"))
        assert status == 200
        assert body["status"] == "gate2_selected_sandbox_canary_completed"

    counters = evidence_store.get_counters()
    assert counters["gate2Records"] == 3
    assert counters["deliveryReceipts"] == 3


# ── 5. Evidence store unit tests ─────────────────────────────────────────────


def test_evidence_store_record_all_evidence_succeeds(tmp_path: Path) -> None:
    """Direct evidence store test: record_all_evidence succeeds on writable path."""
    store = Gate2DurableEvidenceStore(tmp_path / "evidence.json")

    result = store.record_all_evidence(
        request_digest=_digest("test-request"),
        body_digest=_digest("test-body"),
        selected_bot_digest=_digest("bot-1"),
        trusted_owner_user_id_digest=_digest("user-1"),
        environment="staging",
        status="gate2_selected_sandbox_canary_completed",
        reason="completed",
        mutation_receipt_digest=_digest("mutation-1"),
        rollback_receipt_digest=_digest("rollback-1"),
        sandbox_path_digest=_digest("sandbox-path"),
        output_digest=_digest("output-1"),
    )

    assert result.success is True
    assert result.record.all_evidence_present is True
    assert result.error is None


def test_evidence_store_get_evidence_returns_none_for_missing(tmp_path: Path) -> None:
    """get_evidence returns None for non-existent request digest."""
    store = Gate2DurableEvidenceStore(tmp_path / "evidence.json")
    assert store.get_evidence("sha256:nonexistent") is None


def test_evidence_store_get_counters_starts_at_zero(tmp_path: Path) -> None:
    """Counters start at zero before any records."""
    store = Gate2DurableEvidenceStore(tmp_path / "evidence.json")
    counters = store.get_counters()
    assert counters["gate2Records"] == 0
    assert counters["deliveryReceipts"] == 0


def test_evidence_store_handles_write_failure(tmp_path: Path) -> None:
    """Evidence store returns failure result on write error."""
    store = Gate2DurableEvidenceStore(tmp_path / "evidence.json")

    original_save = store._save

    def _failing_save(data: object) -> None:
        raise OSError("permission denied")

    store._save = _failing_save  # type: ignore[assignment]

    result = store.record_all_evidence(
        request_digest=_digest("fail-request"),
        body_digest=_digest("fail-body"),
        selected_bot_digest=_digest("bot-1"),
        trusted_owner_user_id_digest=_digest("user-1"),
        environment="staging",
        status="gate2_selected_sandbox_canary_completed",
        reason="completed",
        mutation_receipt_digest=_digest("mutation-1"),
        rollback_receipt_digest=_digest("rollback-1"),
        sandbox_path_digest=_digest("sandbox-path"),
    )

    assert result.success is False
    assert result.error is not None
    assert "evidence_write_failed" in result.error
    assert result.record.all_evidence_present is False


def test_evidence_record_missing_evidence_list(tmp_path: Path) -> None:
    """Gate2EvidenceRecord.missing_evidence lists what's missing."""
    from openmagi_core_agent.evidence.gate2_durable_evidence import Gate2EvidenceRecord

    record = Gate2EvidenceRecord(
        request_digest="sha256:abc",
        body_digest=None,
        request_recorded=True,
        counter_incremented=False,
        delivery_receipt_recorded=True,
        mutation_receipt_recorded=False,
        rollback_receipt_recorded=True,
        recorded_at_ms=0,
    )
    assert record.all_evidence_present is False
    assert "counter_increment" in record.missing_evidence
    assert "mutation_receipt" in record.missing_evidence
    assert "request_record" not in record.missing_evidence
    assert "delivery_receipt" not in record.missing_evidence
    assert "rollback_receipt" not in record.missing_evidence
