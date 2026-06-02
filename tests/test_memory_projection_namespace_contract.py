from __future__ import annotations

import json
import subprocess
import sys

from magi_agent.memory.contracts import MemoryRecord, RecallResult
from magi_agent.memory.namespaces import (
    MemoryNamespacePolicy,
    admit_recall_result_to_namespace,
    evaluate_memory_record_namespace,
)
from magi_agent.memory.projection import project_namespaced_memory_boundary


NAMESPACE_A = "memory-ns:tenant-a.bot-a"
NAMESPACE_B = "memory-ns:tenant-b.bot-b"


def _record(
    record_id: str,
    *,
    namespace: str = NAMESPACE_A,
    visibility: str = "public-safe",
    scope: str = "bot",
    body: str = "Launch note: prefer short status updates.",
    metadata: dict[str, object] | None = None,
) -> MemoryRecord:
    custom_metadata = {"namespaceRef": namespace, **(metadata or {})}
    return MemoryRecord(
        id=record_id,
        scope=scope,
        kind="note",
        body=body,
        sourceRef=f"memory://safe/{record_id}",
        providerId="memory-provider:local",
        confidence="observed",
        visibility=visibility,
        score=0.9,
        customMetadata=custom_metadata,
    )


def _result(*records: MemoryRecord) -> RecallResult:
    return RecallResult(
        providerId="memory-provider:local",
        records=records,
        recallAllowed=True,
        writeAllowed=False,
        promptProjectionAllowed=False,
        publicProjectionAllowed=True,
        reasonCodes=("local_fake_recall",),
    )


def test_namespace_admission_filters_private_mismatched_and_stale_records() -> None:
    policy = MemoryNamespacePolicy(namespaceRef=NAMESPACE_A)
    recall = _result(
        _record("allowed"),
        _record("private", visibility="private", body="Private user preference."),
        _record("wrong-namespace", namespace=NAMESPACE_B),
        _record("stale", metadata={"stale": True}),
    )

    projection = admit_recall_result_to_namespace(recall, policy)
    dumped = json.dumps(projection.public_projection(), sort_keys=True)

    assert [record.id for record in projection.result.records] == ["allowed"]
    assert projection.result.prompt_projection_allowed is False
    assert projection.result.write_allowed is False
    assert projection.prompt_projection_allowed is False
    assert projection.memory_write_allowed is False
    assert "Private user preference" not in dumped
    assert "wrong-namespace" not in dumped
    assert "stale" not in [record.id for record in projection.result.records]
    assert "private_memory_excluded" in projection.reason_codes
    assert "memory_namespace_mismatch" in projection.reason_codes
    assert "stale_memory_ref_denied" in projection.reason_codes


def test_namespace_policy_blocks_redaction_retention_and_erase_states() -> None:
    recall = _result(_record("blocked-by-policy"))

    redaction = admit_recall_result_to_namespace(
        recall,
        MemoryNamespacePolicy(namespaceRef=NAMESPACE_A, redactionState="failed"),
    )
    expired = admit_recall_result_to_namespace(
        recall,
        MemoryNamespacePolicy(namespaceRef=NAMESPACE_A, retentionState="expired"),
    )
    erased = admit_recall_result_to_namespace(
        recall,
        MemoryNamespacePolicy(namespaceRef=NAMESPACE_A, eraseState="erased"),
    )

    assert redaction.result.records == ()
    assert "memory_redaction_not_verified" in redaction.reason_codes
    assert expired.result.records == ()
    assert "memory_retention_not_active" in expired.reason_codes
    assert erased.result.records == ()
    assert "memory_erase_state_blocks_projection" in erased.reason_codes


def test_namespace_source_authority_background_only_projects_no_public_refs() -> None:
    recall = _result(_record("background-only"))
    namespace_policy = MemoryNamespacePolicy(
        namespaceRef=NAMESPACE_A,
        sourceAuthority="background_only",
    )

    admitted = admit_recall_result_to_namespace(recall, namespace_policy)
    boundary = project_namespaced_memory_boundary(
        recall,
        namespace_policy=namespace_policy,
        latest_user_text="What should we do next?",
    )
    public_projection = boundary.model_dump(by_alias=True, mode="json")

    assert admitted.result.records == ()
    assert admitted.result.recall_allowed is True
    assert admitted.result.public_projection_allowed is False
    assert admitted.decisions[0].status == "background_only"
    assert boundary.references == ()
    assert public_projection["promptProjectionAllowed"] is False
    assert public_projection["sessionInjectionAllowed"] is False
    assert public_projection["sourceAuthority"]["longTermMemoryPolicy"] == "background_only"
    assert "source_authority_background_only" in boundary.diagnostics.reason_codes


def test_namespace_source_authority_memory_redact_blocks_public_projection() -> None:
    recall = _result(_record("redact-authority"))
    namespace_policy = MemoryNamespacePolicy(
        namespaceRef=NAMESPACE_A,
        sourceAuthority="memory_redact_authority",
    )

    admitted = admit_recall_result_to_namespace(recall, namespace_policy)
    boundary = project_namespaced_memory_boundary(
        recall,
        namespace_policy=namespace_policy,
        latest_user_text="What changed?",
    )
    public_projection = boundary.model_dump(by_alias=True, mode="json")

    assert admitted.result.records == ()
    assert admitted.result.recall_allowed is False
    assert admitted.result.public_projection_allowed is False
    assert admitted.decisions[0].status == "blocked"
    assert boundary.references == ()
    assert public_projection["sourceAuthority"]["longTermMemoryPolicy"] == "disabled"
    assert "memory_redact_authority_supersedes_provider" in admitted.reason_codes
    assert "memory_redact_authority_supersedes_provider" in boundary.diagnostics.reason_codes


def test_namespace_decision_cannot_be_forged_to_enable_prompt_or_writes() -> None:
    decision = evaluate_memory_record_namespace(
        _record("forge"),
        MemoryNamespacePolicy(namespaceRef=NAMESPACE_A),
    )
    forged = decision.model_copy(
        update={
            "promptProjectionAllowed": True,
            "memoryWriteAllowed": True,
            "publicProjectionAllowed": True,
        }
    )

    assert forged.prompt_projection_allowed is False
    assert forged.memory_write_allowed is False
    assert forged.model_dump(by_alias=True)["promptProjectionAllowed"] is False
    assert forged.model_dump(by_alias=True)["memoryWriteAllowed"] is False


def test_namespace_admission_cannot_be_forged_to_enable_prompt_or_writes() -> None:
    admission = admit_recall_result_to_namespace(
        _result(_record("forge-admission")),
        MemoryNamespacePolicy(namespaceRef=NAMESPACE_A),
    )
    constructed = admission.model_copy(
        update={
            "promptProjectionAllowed": True,
            "memoryWriteAllowed": True,
        }
    )

    assert constructed.prompt_projection_allowed is False
    assert constructed.memory_write_allowed is False
    assert constructed.model_dump(by_alias=True)["promptProjectionAllowed"] is False
    assert constructed.model_dump(by_alias=True)["memoryWriteAllowed"] is False


def test_namespace_public_projection_hashes_secret_shaped_allowed_record_ids() -> None:
    admission = admit_recall_result_to_namespace(
        _result(_record("github_pat_1234567890abcdef")),
        MemoryNamespacePolicy(namespaceRef=NAMESPACE_A),
    )
    dumped = json.dumps(admission.public_projection(), sort_keys=True)

    assert "github_pat_1234567890abcdef" not in dumped
    assert "memory:" in dumped


def test_namespace_admission_treats_stale_ref_metadata_as_stale() -> None:
    for stale_value in ("memory:r0", {"ref": "memory:r0"}, ["memory:r0"], ("memory:r0",), 1):
        admission = admit_recall_result_to_namespace(
            _result(_record("stale-ref", metadata={"staleRef": stale_value})),
            MemoryNamespacePolicy(namespaceRef=NAMESPACE_A),
        )

        assert admission.result.records == ()
        assert admission.decisions[0].status == "blocked"
        assert "stale_memory_ref_denied" in admission.reason_codes


def test_namespace_admission_fails_closed_for_unknown_present_safety_states() -> None:
    cases = (
        ("redactionState", "pending", "memory_redaction_not_verified"),
        ("retentionState", "archived", "memory_retention_not_active"),
        ("eraseState", "deleted", "memory_erase_state_blocks_projection"),
        ("redactionState", {"state": "verified"}, "memory_redaction_not_verified"),
    )

    for key, value, reason in cases:
        admission = admit_recall_result_to_namespace(
            _result(_record(f"unknown-{key}", metadata={key: value})),
            MemoryNamespacePolicy(namespaceRef=NAMESPACE_A),
        )

        assert admission.result.records == ()
        assert admission.decisions[0].status == "blocked"
        assert reason in admission.reason_codes


def test_namespace_projection_import_boundary_has_no_live_memory_or_route_imports() -> None:
    code = """
import sys
import magi_agent.memory.namespaces
import magi_agent.memory.projection
for name in (
    'google.adk.runners',
    'google.adk.memory',
    'magi_agent.adk_bridge.runner_adapter',
    'magi_agent.adk_bridge.local_runner',
    'magi_agent.app',
    'magi_agent.transport.chat',
    'magi_agent.routes',
    'supabase',
    'psycopg',
    'asyncpg',
):
    if name in sys.modules:
        raise SystemExit(name)
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
