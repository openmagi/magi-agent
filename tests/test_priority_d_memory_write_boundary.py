from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


def _boundary_module() -> Any:
    return importlib.import_module("openmagi_core_agent.memory.write_boundary")


def test_memory_write_redact_delete_intents_are_denied_by_default() -> None:
    boundary = _boundary_module()

    for operation in ("remember", "write", "redact", "delete"):
        intent = boundary.MemoryMutationIntent(
            provider_id="hipocampus-qmd-readonly",
            turn_id="turn-1",
            operation=operation,
            target_text="raw memory target must never survive projection",
            path_refs=("/Users/kevin/private-bot/memory/ROOT.md", "memory/daily/2026-05-20.md"),
            content="Authorization: Bearer unsafe-token",
        )

        receipt = boundary.plan_memory_mutation(intent)
        dumped = receipt.model_dump(by_alias=True)
        public_dumped = receipt.public_projection()
        encoded = json.dumps(dumped, sort_keys=True)
        public_encoded = json.dumps(public_dumped, sort_keys=True)

        assert receipt.status in {"blocked", "approval_required", "unsupported"}
        assert receipt.executed is False
        assert receipt.memory_write_allowed is False
        assert receipt.production_write_enabled is False
        assert dumped["memoryWriteAllowed"] is False
        assert dumped["productionWriteEnabled"] is False
        assert receipt.target.target_sha256 == boundary.sha256_hex(
            "raw memory target must never survive projection"
        )
        assert receipt.target.target_byte_length == len(
            "raw memory target must never survive projection".encode("utf-8")
        )
        assert receipt.target.raw_target_text is None
        assert "/Users/kevin" not in encoded
        assert "raw memory target" not in encoded
        assert "unsafe-token" not in encoded
        assert "Bearer" not in public_encoded
        assert "memory/ROOT.md" in receipt.target.path_refs
        assert "memory/daily/2026-05-20.md" in receipt.target.path_refs


def test_all_memory_operations_have_pure_planning_receipts_without_provider_calls() -> None:
    boundary = _boundary_module()

    receipts = [
        boundary.plan_memory_mutation(
            {
                "providerId": "agentmemory",
                "turnId": "turn-ops",
                "operation": operation,
                "targetSha256": f"sha256:{operation}",
                "pathRefs": ("memory/ROOT.md",),
            }
        )
        for operation in ("remember", "write", "redact", "delete", "compact", "decay", "export")
    ]

    assert [receipt.operation for receipt in receipts] == [
        "remember",
        "write",
        "redact",
        "delete",
        "compact",
        "decay",
        "export",
    ]
    for receipt in receipts:
        assert receipt.executed is False
        assert receipt.provider_call_attempted is False
        assert receipt.filesystem_mutation_attempted is False
        assert receipt.production_receipt is False
        assert receipt.error_code.startswith("memory_")
        assert "disabled" in receipt.message or "unsupported" in receipt.message


def test_write_claim_requires_matching_successful_local_test_only_receipt() -> None:
    boundary = _boundary_module()
    target_sha = boundary.sha256_hex("delete this memory")
    claim = boundary.MemoryWriteClaim(
        provider_id="agentmemory",
        turn_id="turn-claim",
        operation="redact",
        target_sha256=target_sha,
    )

    no_receipt = boundary.evaluate_memory_write_claim(claim, receipts=())
    wrong_target = boundary.evaluate_memory_write_claim(
        claim,
        receipts=(
            boundary.fake_successful_test_receipt(
                provider_id="agentmemory",
                turn_id="turn-claim",
                operation="redact",
                target_sha256=boundary.sha256_hex("different target"),
                matched_count=1,
                target_still_present=False,
            ),
        ),
    )
    matching = boundary.evaluate_memory_write_claim(
        claim,
        receipts=(
            boundary.fake_successful_test_receipt(
                provider_id="agentmemory",
                turn_id="turn-claim",
                operation="redact",
                target_sha256=target_sha,
                matched_count=1,
                target_still_present=False,
            ),
        ),
        allow_local_test_receipts=True,
    )

    assert no_receipt.allowed is False
    assert no_receipt.reason_code == "missing_successful_receipt"
    assert wrong_target.allowed is False
    assert wrong_target.reason_code == "missing_successful_receipt"
    assert matching.allowed is True
    assert matching.reason_code == "local_test_only_receipt_matched"
    assert matching.receipt is not None
    assert matching.receipt.local_test_only is True
    assert matching.receipt.production_write_enabled is False
    assert matching.receipt.production_receipt is False

    local_without_test_gate = boundary.evaluate_memory_write_claim(
        claim,
        receipts=(matching.receipt,),
    )
    assert local_without_test_gate.allowed is False
    assert local_without_test_gate.reason_code == "missing_successful_receipt"


def test_non_local_or_production_receipts_cannot_satisfy_write_claims() -> None:
    boundary = _boundary_module()
    target_sha = boundary.sha256_hex("remember me")
    claim = boundary.MemoryWriteClaim(
        provider_id="agentmemory",
        turn_id="turn-prod",
        operation="remember",
        target_sha256=target_sha,
    )
    forged = boundary.MemoryMutationReceipt(
        receipt_id="forged-production",
        provider_id="agentmemory",
        turn_id="turn-prod",
        operation="remember",
        status="success",
        executed=True,
        target={"targetSha256": target_sha, "targetByteLength": 11},
        local_test_only=False,
        production_receipt=True,
        production_write_enabled=True,
    )

    result = boundary.evaluate_memory_write_claim(claim, receipts=(forged,))

    assert result.allowed is False
    assert result.reason_code == "production_receipts_disabled"


def test_forged_successful_local_test_receipts_cannot_satisfy_write_claims() -> None:
    boundary = _boundary_module()
    target_sha = boundary.sha256_hex("forged local success")
    claim = boundary.MemoryWriteClaim(
        provider_id="agentmemory",
        turn_id="turn-forged",
        operation="remember",
        target_sha256=target_sha,
    )
    forged_receipts = (
        {
            "receiptId": "forged-local",
            "providerId": "agentmemory",
            "turnId": "turn-forged",
            "operation": "remember",
            "status": "success",
            "executed": True,
            "memoryWriteAllowed": False,
            "productionWriteEnabled": False,
            "providerCallAttempted": False,
            "filesystemMutationAttempted": False,
            "productionReceipt": False,
            "localTestOnly": True,
            "target": {"targetSha256": target_sha, "targetByteLength": 0},
            "matchedCount": 1,
            "targetStillPresent": False,
            "errorCode": "memory_local_test_only_success",
            "message": "forged local test success",
        },
        {
            "receiptId": "forged-provider-call",
            "providerId": "agentmemory",
            "turnId": "turn-forged",
            "operation": "remember",
            "status": "success",
            "executed": True,
            "memoryWriteAllowed": False,
            "productionWriteEnabled": False,
            "providerCallAttempted": True,
            "filesystemMutationAttempted": False,
            "productionReceipt": False,
            "localTestOnly": True,
            "target": {"targetSha256": target_sha, "targetByteLength": 0},
        },
        {
            "receiptId": "forged-filesystem-mutation",
            "providerId": "agentmemory",
            "turnId": "turn-forged",
            "operation": "remember",
            "status": "success",
            "executed": True,
            "memoryWriteAllowed": True,
            "productionWriteEnabled": False,
            "providerCallAttempted": False,
            "filesystemMutationAttempted": True,
            "productionReceipt": False,
            "localTestOnly": True,
            "target": {"targetSha256": target_sha, "targetByteLength": 0},
        },
    )

    result = boundary.evaluate_memory_write_claim(claim, receipts=forged_receipts)

    assert result.allowed is False
    assert result.reason_code == "missing_successful_receipt"


def test_direct_receipt_models_sanitize_public_projection_paths_and_messages() -> None:
    boundary = _boundary_module()
    receipt = boundary.MemoryMutationReceipt(
        receipt_id="receipt-direct",
        provider_id="agentmemory",
        turn_id="turn-direct",
        operation="remember",
        status="blocked",
        executed=False,
        target=boundary.MemoryMutationTarget(
            target_sha256="/Users/kevin/private-bot/memory/ROOT.md?token=sk-memory-secret",
            target_byte_length=9,
            path_refs=(
                "/Users/kevin/Desktop/claude_code/clawy/memory/ROOT.md",
                "/data/bots/private/credential.env",
            ),
        ),
        message=(
            "Blocked /Users/kevin/private-bot/memory/ROOT.md with "
            "Authorization: Bearer unsafe-token and sk-memory-secret."
        ),
    )

    public_projection = receipt.public_projection()
    encoded = json.dumps(public_projection, sort_keys=True)

    assert "/Users/kevin" not in encoded
    assert "/data/bots" not in encoded
    assert "unsafe-token" not in encoded
    assert "sk-memory-secret" not in encoded
    assert "Bearer" not in encoded
    assert "memory/ROOT.md" in encoded
    assert "[private-path-redacted]" in encoded


def test_public_projection_hashes_sensitive_provider_ids_and_receipt_ids() -> None:
    boundary = _boundary_module()
    receipt = boundary.fake_successful_test_receipt(
        provider_id="sk-live-secretprovider12345",
        turn_id="turn-provider-secret",
        operation="remember",
        target_sha256=boundary.sha256_hex("safe target"),
        matched_count=1,
        target_still_present=False,
    )

    public_projection = receipt.public_projection()
    encoded = json.dumps(public_projection, sort_keys=True)

    assert public_projection["providerId"].startswith("provider:")
    assert public_projection["receiptId"].startswith("memory-receipt:")
    assert "sk-live-secretprovider12345" not in encoded


def test_plan_memory_mutation_public_projection_hashes_path_shaped_provider_ids() -> None:
    boundary = _boundary_module()
    receipt = boundary.plan_memory_mutation(
        {
            "providerId": "/var/lib/kubelet/pods/provider-token",
            "turnId": "turn-provider-path",
            "operation": "redact",
            "targetText": "safe target",
        }
    )

    public_projection = receipt.public_projection()
    encoded = json.dumps(public_projection, sort_keys=True)

    assert public_projection["providerId"].startswith("provider:")
    assert public_projection["receiptId"].startswith("memory-receipt:")
    assert "/var/lib/kubelet" not in encoded


def test_public_projection_hashes_raw_child_and_tool_shaped_provider_ids_and_receipts() -> None:
    boundary = _boundary_module()
    receipt = boundary.fake_successful_test_receipt(
        provider_id="raw_child_transcript: hidden",
        turn_id="turn-provider-raw-child",
        operation="remember",
        target_sha256=boundary.sha256_hex("safe target"),
        matched_count=1,
        target_still_present=False,
    )
    tool_tag_receipt = boundary.fake_successful_test_receipt(
        provider_id="<tool_log>secret</tool_log>",
        turn_id="turn-provider-tool-log",
        operation="remember",
        target_sha256=boundary.sha256_hex("safe target"),
        matched_count=1,
        target_still_present=False,
    )

    encoded = json.dumps(
        [receipt.public_projection(), tool_tag_receipt.public_projection()],
        sort_keys=True,
    )

    for public_projection in (receipt.public_projection(), tool_tag_receipt.public_projection()):
        assert public_projection["providerId"].startswith("provider:")
        assert public_projection["receiptId"].startswith("memory-receipt:")
    assert "raw_child_transcript" not in encoded
    assert "hidden" not in encoded
    assert "<tool_log>" not in encoded
    assert "secret" not in encoded


def test_public_projection_redacts_raw_child_and_tool_text_inside_messages() -> None:
    boundary = _boundary_module()
    receipt = boundary.MemoryMutationReceipt(
        receipt_id="receipt-message",
        provider_id="agentmemory",
        turn_id="turn-message",
        operation="remember",
        status="blocked",
        executed=False,
        target={"targetSha256": boundary.sha256_hex("safe target"), "targetByteLength": 0},
        message=(
            "prefix raw_child_transcript data suffix "
            "raw_subagent_transcript_secret "
            "<tool_log>secret</tool_log> "
            "<child_prompt>private prompt</child_prompt> "
            "raw_tool_args data "
            "tool log: internal command output "
            "tool args: private arguments "
            "child prompt: private instruction "
            "hidden reasoning: private trace "
            "https://storage.googleapis.com/private-bucket/object "
            "OBJECT_PAYLOAD_DO_NOT_LEAK "
            "private_memory_note diary secret "
            "private-memory-note\n"
            "raw_subagent_transcript_secret: TRANSCRIPT_PAYLOAD_DO_NOT_LEAK\n"
            "private_reasoning: COT_PAYLOAD_DO_NOT_LEAK"
        ),
    )

    encoded = json.dumps(receipt.public_projection(), sort_keys=True)

    assert "prefix" in encoded
    assert "raw_child_transcript" not in encoded
    assert "raw_subagent_transcript" not in encoded
    assert "raw_tool_args" not in encoded
    assert "<tool_log>" not in encoded
    assert "<child_prompt>" not in encoded
    assert "secret" not in encoded
    assert "private prompt" not in encoded
    assert "tool log" not in encoded
    assert "tool args" not in encoded
    assert "child prompt" not in encoded
    assert "hidden reasoning" not in encoded
    assert "storage.googleapis.com" not in encoded
    assert "private-bucket" not in encoded
    assert "OBJECT_PAYLOAD_DO_NOT_LEAK" not in encoded
    assert "private_memory" not in encoded
    assert "private-memory" not in encoded
    assert "diary secret" not in encoded
    assert "TRANSCRIPT_PAYLOAD_DO_NOT_LEAK" not in encoded
    assert "COT_PAYLOAD_DO_NOT_LEAK" not in encoded


def test_public_projection_redacts_standalone_object_url_message_payload_blocks() -> None:
    boundary = _boundary_module()
    receipt = boundary.MemoryMutationReceipt(
        receipt_id="receipt-object-url",
        provider_id="agentmemory",
        turn_id="turn-object-url",
        operation="remember",
        status="blocked",
        executed=False,
        target={"targetSha256": boundary.sha256_hex("safe target"), "targetByteLength": 0},
        message=(
            "Visible safe summary.\n"
            "https://storage.googleapis.com/private-bucket/object\n"
            "OBJECT_PAYLOAD_DO_NOT_LEAK\n"
            "OBJECT_SECOND_LINE_PAYLOAD_DO_NOT_LEAK"
        ),
    )

    encoded = json.dumps(receipt.public_projection(), sort_keys=True)

    assert "Visible safe summary" in encoded
    assert "storage.googleapis.com" not in encoded
    assert "private-bucket" not in encoded
    assert "OBJECT_PAYLOAD_DO_NOT_LEAK" not in encoded
    assert "OBJECT_SECOND_LINE_PAYLOAD_DO_NOT_LEAK" not in encoded


def test_public_projection_sanitizes_sensitive_turn_ids_and_error_codes() -> None:
    boundary = _boundary_module()
    receipt = boundary.MemoryMutationReceipt(
        receipt_id="receipt-safe",
        provider_id="agentmemory",
        turn_id="turn-/Users/kevin/private private_memory_note sk-live-secret12345",
        operation="remember",
        status="blocked",
        executed=False,
        target={"targetSha256": boundary.sha256_hex("safe target"), "targetByteLength": 0},
        error_code="private-memory-note /Users/kevin/private Cookie: session=unsafe",
    )

    projection = receipt.public_projection()
    encoded = json.dumps(projection, sort_keys=True)

    assert str(projection["turnId"]).startswith("turn:")
    assert str(projection["errorCode"]).startswith("memory-error:")
    assert "/Users/kevin" not in encoded
    assert "private_memory" not in encoded
    assert "private-memory" not in encoded
    assert "raw_tool_args" not in encoded
    assert "sk-live-secret" not in encoded
    assert "Cookie:" not in encoded
    assert "session=unsafe" not in encoded


def test_public_projection_drops_private_marker_message_payloads() -> None:
    boundary = _boundary_module()
    receipt = boundary.MemoryMutationReceipt(
        receipt_id="receipt-private-payload",
        provider_id="agentmemory",
        turn_id="turn-private-payload",
        operation="remember",
        status="blocked",
        executed=False,
        target={"targetSha256": boundary.sha256_hex("safe target"), "targetByteLength": 0},
        message=(
            "raw_subagent_transcript_secret: TRANSCRIPT_PAYLOAD_DO_NOT_LEAK\n"
            "private_reasoning: COT_PAYLOAD_DO_NOT_LEAK\n"
            "raw_subagent_transcript_secret:\n"
            "MULTILINE_TRANSCRIPT_PAYLOAD_DO_NOT_LEAK\n"
            "private_reasoning:\n"
            "MULTILINE_COT_PAYLOAD_DO_NOT_LEAK\n"
            "private_reasoning:\n"
            "\n"
            "BLANK_LINE_COT_PAYLOAD_DO_NOT_LEAK\n"
            "raw_subagent_transcript_secret:\n"
            "MULTILINE_PAYLOAD_LINE_ONE_DO_NOT_LEAK\n"
            "MULTILINE_PAYLOAD_LINE_TWO_DO_NOT_LEAK"
        ),
    )

    encoded = json.dumps(receipt.public_projection(), sort_keys=True)

    assert "raw_subagent_transcript" not in encoded
    assert "private_reasoning" not in encoded
    assert "TRANSCRIPT_PAYLOAD_DO_NOT_LEAK" not in encoded
    assert "COT_PAYLOAD_DO_NOT_LEAK" not in encoded
    assert "MULTILINE_TRANSCRIPT_PAYLOAD_DO_NOT_LEAK" not in encoded
    assert "MULTILINE_COT_PAYLOAD_DO_NOT_LEAK" not in encoded
    assert "BLANK_LINE_COT_PAYLOAD_DO_NOT_LEAK" not in encoded
    assert "MULTILINE_PAYLOAD_LINE_ONE_DO_NOT_LEAK" not in encoded
    assert "MULTILINE_PAYLOAD_LINE_TWO_DO_NOT_LEAK" not in encoded


def test_model_construct_cannot_bypass_receipt_redaction() -> None:
    boundary = _boundary_module()
    receipt = boundary.MemoryMutationReceipt.model_construct(
        receiptId="receipt-construct",
        providerId="agentmemory",
        turnId="turn-construct",
        operation="remember",
        status="blocked",
        executed=False,
        target={
            "targetSha256": "/Users/kevin/private/token=sk-memory-secret",
            "targetByteLength": 9,
            "pathRefs": (
                "s3://private-bucket/object?X-Amz-Signature=unsafe",
                "/Users/kevin/private/memory/ROOT.md?token=sk-memory-secret",
            ),
        },
        message=(
            "Cookie: session=unsafe; "
            "s3://private-bucket/object?X-Amz-Signature=unsafe"
        ),
    )

    encoded = json.dumps(receipt.public_projection(), sort_keys=True)

    assert "/Users/kevin" not in encoded
    assert "sk-memory-secret" not in encoded
    assert "Cookie:" not in encoded
    assert "session=unsafe" not in encoded
    assert "s3://private-bucket" not in encoded
    assert "X-Amz-Signature" not in encoded
    assert "[private-ref-redacted]" in encoded


def test_model_copy_and_model_validate_cannot_project_production_authority_or_raw_message() -> None:
    boundary = _boundary_module()
    target_sha = boundary.sha256_hex("copy target")
    receipt = boundary.MemoryMutationReceipt(
        receipt_id="receipt-copy",
        provider_id="agentmemory",
        turn_id="turn-copy",
        operation="remember",
        status="blocked",
        executed=False,
        target={"targetSha256": target_sha, "targetByteLength": 0},
    )

    copied = receipt.model_copy(
        update={
            "status": "success",
            "executed": True,
            "memoryWriteAllowed": True,
            "productionWriteEnabled": True,
            "providerCallAttempted": True,
            "filesystemMutationAttempted": True,
            "productionReceipt": True,
            "message": (
                "Authorization: Bearer unsafe-token at /Users/kevin/private.txt "
                "AWS_ACCESS_KEY_ID=AKIAUNSAFEKEY"
            ),
        }
    )
    validated = boundary.MemoryMutationReceipt.model_validate(
        {
            "receiptId": "receipt-validated",
            "providerId": "agentmemory",
            "turnId": "turn-copy",
            "operation": "remember",
            "status": "success",
            "executed": True,
            "memoryWriteAllowed": True,
            "productionWriteEnabled": True,
            "providerCallAttempted": True,
            "filesystemMutationAttempted": True,
            "productionReceipt": True,
            "target": {"targetSha256": target_sha, "targetByteLength": 0},
            "message": (
                "Authorization: Bearer unsafe-token at /Users/kevin/private.txt "
                "AWS_ACCESS_KEY_ID=AKIAUNSAFEKEY"
            ),
        }
    )

    for candidate in (copied, validated):
        projected = candidate.public_projection()
        encoded = json.dumps(projected, sort_keys=True)

        assert projected["status"] == "blocked"
        assert projected["executed"] is False
        assert projected["memoryWriteAllowed"] is False
        assert projected["productionWriteEnabled"] is False
        assert projected["providerCallAttempted"] is False
        assert projected["filesystemMutationAttempted"] is False
        assert "unsafe-token" not in encoded
        assert "/Users/kevin" not in encoded
        assert "AWS_ACCESS_KEY_ID" not in encoded
        assert "AKIAUNSAFEKEY" not in encoded


def test_redaction_failure_provider_unavailable_stale_conflict_and_child_isolation_receipts() -> None:
    boundary = _boundary_module()

    redaction_failed = boundary.plan_memory_mutation(
        {
            "providerId": "hipocampus-qmd-readonly",
            "turnId": "turn-redact",
            "operation": "redact",
            "targetText": "secret old value",
            "matchedCount": 2,
            "targetStillPresent": True,
            "failureKind": "redaction_failed",
        }
    )
    provider_unavailable = boundary.plan_memory_mutation(
        {
            "providerId": "agentmemory",
            "turnId": "turn-provider",
            "operation": "remember",
            "targetText": "new durable memory",
            "failureKind": "provider_unavailable",
        }
    )
    stale_conflict = boundary.plan_memory_mutation(
        {
            "providerId": "hipocampus-qmd-readonly",
            "turnId": "turn-stale",
            "operation": "delete",
            "targetSha256": "sha256:known-target",
            "failureKind": "stale_conflict",
        }
    )
    child_isolation = boundary.plan_memory_mutation(
        {
            "providerId": "hipocampus-qmd-readonly",
            "turnId": "turn-child",
            "operation": "write",
            "targetText": "child prompt content",
            "childMemoryIsolated": True,
            "childPrompt": "private child prompt",
            "toolLogs": "private tool log",
        }
    )

    assert redaction_failed.status == "blocked"
    assert redaction_failed.error_code == "memory_redaction_failed"
    assert redaction_failed.matched_count == 2
    assert redaction_failed.target_still_present is True
    assert provider_unavailable.status == "unsupported"
    assert provider_unavailable.error_code == "memory_provider_unavailable"
    assert stale_conflict.status == "blocked"
    assert stale_conflict.error_code == "memory_stale_conflict"
    assert child_isolation.status == "blocked"
    assert child_isolation.error_code == "memory_child_scope_isolated"

    encoded = json.dumps(
        [receipt.model_dump(by_alias=True) for receipt in (
            redaction_failed,
            provider_unavailable,
            stale_conflict,
            child_isolation,
        )],
        sort_keys=True,
    )
    assert "secret old value" not in encoded
    assert "new durable memory" not in encoded
    assert "child prompt content" not in encoded
    assert "private child prompt" not in encoded
    assert "private tool log" not in encoded


def test_provider_neutral_backend_descriptors_default_off_with_activation_blockers() -> None:
    boundary = _boundary_module()
    descriptors = {item.provider_id: item for item in boundary.provider_backend_descriptors()}

    assert set(descriptors) == {
        "hipocampus-qmd-readonly",
        "agentmemory",
        "external-vector",
    }
    agentmemory = descriptors["agentmemory"]
    external_vector = descriptors["external-vector"]
    hipocampus = descriptors["hipocampus-qmd-readonly"]

    for descriptor in descriptors.values():
        assert descriptor.enabled is False
        assert descriptor.provider_calls_enabled is False
        assert descriptor.provider_sdk_import_allowed is False
        assert descriptor.memory_write_allowed is False
        assert descriptor.production_write_enabled is False
        assert descriptor.activation_blockers

    assert hipocampus.optional_candidate is True
    assert agentmemory.optional_candidate is True
    assert agentmemory.kind == "agent_memory"
    assert agentmemory.capabilities.supports_remember is True
    assert agentmemory.capabilities.supports_redact is True
    assert "no AgentMemory SDK dependency attached" in agentmemory.activation_blockers
    assert external_vector.kind == "external_vector"
    assert "no vector provider SDK dependency attached" in external_vector.activation_blockers


def test_public_projections_redact_sensitive_payloads_and_private_paths() -> None:
    boundary = _boundary_module()
    receipt = boundary.plan_memory_mutation(
        {
            "providerId": "agentmemory",
            "turnId": "turn-public",
            "operation": "remember",
            "targetText": "raw target with sk-memory-secret",
            "content": "memory body with Authorization: Bearer unsafe-token",
            "pathRefs": (
                "/Users/kevin/Desktop/claude_code/clawy/memory/ROOT.md",
                "/data/bots/private/memory/daily/2026-05-20.md",
            ),
            "childPrompt": "child prompt should not project",
            "toolLogs": "tool log should not project",
        }
    )

    public_projection = receipt.public_projection()
    encoded = json.dumps(public_projection, sort_keys=True)

    assert "raw target" not in encoded
    assert "sk-memory-secret" not in encoded
    assert "unsafe-token" not in encoded
    assert "memory body" not in encoded
    assert "child prompt" not in encoded
    assert "tool log" not in encoded
    assert "/Users/kevin" not in encoded
    assert "/data/bots" not in encoded
    assert "memory/ROOT.md" in encoded
    assert "memory/daily/2026-05-20.md" in encoded


def test_memory_write_boundary_import_and_source_are_provider_runtime_and_write_free() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys

import importlib.util
from pathlib import Path

module_path = Path("openmagi_core_agent/memory/write_boundary.py")
spec = importlib.util.spec_from_file_location(
    "_memory_write_boundary_import_check",
    module_path,
)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
assert module is not None

forbidden_prefixes = (
    "google.adk",
    "openmagi_core_agent.adk_bridge",
    "openmagi_core_agent.tools",
    "openmagi_core_agent.transport",
    "openmagi_core_agent.app",
    "openmagi_core_agent.main",
    "openmagi_core_agent.routes",
    "openmagi_core_agent.database",
    "openmagi_core_agent.db",
    "openmagi_core_agent.chat_proxy",
    "openmagi_core_agent.proxy",
    "openmagi_core_agent.frontend",
    "openmagi_core_agent.deploy",
    "openmagi_core_agent.plugins.agentmemory",
    "openmagi_core_agent.memory.providers",
    "openmagi_core_agent.services.memory",
    "openmagi_core_agent.hipocampus",
    "openmagi_core_agent.qmd",
    "agentmemory",
    "openai",
    "anthropic",
    "google.genai",
    "pinecone",
    "qdrant_client",
    "weaviate",
    "chromadb",
    "requests",
    "httpx",
    "aiohttp",
    "socket",
    "urllib",
    "http.client",
    "fastapi",
    "uvicorn",
    "subprocess",
)
loaded = [
    name
    for name in sys.modules
    if any(name == prefix or name.startswith(f"{prefix}.") for prefix in forbidden_prefixes)
]
if loaded:
    raise AssertionError(f"memory write boundary loaded forbidden modules: {loaded}")
""",
        ],
        check=False,
        cwd=Path(__file__).parents[1],
        text=True,
        capture_output=True,
    )
    assert completed.returncode == 0, completed.stderr

    module_path = (
        Path(__file__).parents[1]
        / "openmagi_core_agent"
        / "memory"
        / "write_boundary.py"
    )
    source = module_path.read_text(encoding="utf-8")
    forbidden_fragments = (
        "google.adk",
        "AgentMemory(",
        "agentmemory.",
        "openmagi_core_agent.adk_bridge",
        "openmagi_core_agent.tools",
        "openmagi_core_agent.transport",
        "openmagi_core_agent.app",
        "openmagi_core_agent.main",
        "openmagi_core_agent.routes",
        "openmagi_core_agent.database",
        "openmagi_core_agent.db",
        "openmagi_core_agent.chat_proxy",
        "openmagi_core_agent.proxy",
        "openmagi_core_agent.frontend",
        "openmagi_core_agent.deploy",
        "openmagi_core_agent.plugins.agentmemory",
        "openmagi_core_agent.memory.providers",
        "openmagi_core_agent.services.memory",
        "openmagi_core_agent.hipocampus",
        "openmagi_core_agent.qmd",
        "openai",
        "anthropic",
        "google.genai",
        "pinecone",
        "qdrant_client",
        "weaviate",
        "chromadb",
        "requests",
        "httpx",
        "aiohttp",
        "socket",
        "urllib.",
        "http.client",
        "subprocess",
        ".write(",
        "write_text(",
        "append(",
        "open(",
        "Path(",
        "APIRouter",
    )
    for fragment in forbidden_fragments:
        assert fragment not in source
