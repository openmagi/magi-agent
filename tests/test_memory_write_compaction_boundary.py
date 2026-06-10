from __future__ import annotations

import ast
import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest


class SpyMemoryAdapter:
    openmagi_local_fake_provider = True

    def __init__(self) -> None:
        self.calls = 0

    async def write(self, *_args: object, **_kwargs: object) -> None:
        self.calls += 1
        raise AssertionError("write adapter must not be called in PR6")

    async def compact(self, *_args: object, **_kwargs: object) -> None:
        self.calls += 1
        raise AssertionError("compaction adapter must not be called in PR6")


def test_memory_write_disabled_by_default_and_does_not_call_adapter() -> None:
    from magi_agent.harness.memory_write import (
        MemoryWriteHarness,
        MemoryWritePolicy,
        MemoryWriteRequest,
    )

    adapter = SpyMemoryAdapter()
    result = asyncio.run(
        MemoryWriteHarness(adapter=adapter).write(
            request=MemoryWriteRequest(
                providerId="agentmemory",
                turnId="turn-default-off",
                operation="remember",
                content="remember safe launch note",
                evidenceRefs=("evidence:launch-note",),
                approvalRef="approval:memory-write",
                pathRefs=(
                    "raw-source-text-must-not-appear",
                    "memory:raw-policy-snapshot-text-must-not-appear",
                    "/Users/kevin/private/path.txt",
                ),
            ),
            policy=MemoryWritePolicy(
                policyRef="policy:memory-write",
                policySnapshotRef="policy-snapshot:pr6",
                approvalRequired=True,
            ),
        )
    )

    assert adapter.calls == 0
    assert result.status == "disabled"
    assert result.receipt is not None
    assert result.receipt.status in {"blocked", "approval_required"}
    assert result.receipt.executed is False
    assert result.receipt.production_write_enabled is False
    assert result.receipt.provider_call_attempted is False
    assert result.receipt.filesystem_mutation_attempted is False
    assert result.receipt.authority_flags.production_write_enabled is False
    assert "memory_write_boundary_disabled" in result.reason_codes
    encoded = json.dumps(
        [
            result.public_projection(),
            result.model_dump(by_alias=True, mode="json"),
        ],
        sort_keys=True,
    )
    for forbidden in (
        "raw-source-text-must-not-appear",
        "memory:raw-policy-snapshot-text-must-not-appear",
        "/Users/kevin",
        "path.txt",
    ):
        assert forbidden not in encoded


def test_memory_write_requires_explicit_policy_evidence_and_approval_when_configured() -> None:
    from magi_agent.harness.memory_write import (
        MemoryWriteHarness,
        MemoryWriteHarnessConfig,
        MemoryWritePolicy,
        MemoryWriteRequest,
    )

    harness = MemoryWriteHarness(
        MemoryWriteHarnessConfig(enabled=True, localFakeAdapterEnabled=True),
        adapter=SpyMemoryAdapter(),
    )
    base_request = MemoryWriteRequest(
        providerId="agentmemory",
        turnId="turn-policy",
        operation="remember",
        content="remember safe launch note",
        evidenceRefs=("evidence:launch-note",),
    )
    policy = MemoryWritePolicy(
        policyRef="policy:memory-write",
        policySnapshotRef="policy-snapshot:pr6",
        approvalRequired=True,
        localFakeSuccessAllowed=True,
    )

    missing_policy = asyncio.run(harness.write(request=base_request, policy=None))
    missing_evidence = asyncio.run(
        harness.write(
            request=base_request.model_copy(update={"evidenceRefs": ()}),
            policy=policy,
        )
    )
    missing_approval = asyncio.run(harness.write(request=base_request, policy=policy))

    assert missing_policy.status == "blocked"
    assert "missing_memory_write_policy" in missing_policy.reason_codes
    assert missing_evidence.status == "blocked"
    assert "missing_memory_write_evidence" in missing_evidence.reason_codes
    assert missing_approval.status == "approval_required"
    assert "missing_memory_write_approval" in missing_approval.reason_codes


def test_approved_local_fake_memory_write_returns_local_test_receipt_only() -> None:
    from magi_agent.harness.memory_write import (
        MemoryWriteHarness,
        MemoryWriteHarnessConfig,
        MemoryWritePolicy,
        MemoryWriteRequest,
    )
    from magi_agent.memory.write_boundary import evaluate_memory_write_claim

    adapter = SpyMemoryAdapter()
    result = asyncio.run(
        MemoryWriteHarness(
            MemoryWriteHarnessConfig(enabled=True, localFakeAdapterEnabled=True),
            adapter=adapter,
        ).write(
            request=MemoryWriteRequest(
                providerId="agentmemory",
                turnId="turn-approved",
                operation="remember",
                content="launch note safe enough for digest only",
                evidenceRefs=("evidence:launch-note",),
                approvalRef="approval:memory-write",
            ),
            policy=MemoryWritePolicy(
                policyRef="policy:memory-write",
                policySnapshotRef="policy-snapshot:pr6",
                approvalRequired=True,
                localFakeSuccessAllowed=True,
            ),
        )
    )

    assert adapter.calls == 0
    assert result.status == "success"
    assert result.receipt is not None
    assert result.receipt.local_test_only is True
    assert result.receipt.is_successful_local_test_receipt is True
    assert result.receipt.memory_write_allowed is False
    assert result.receipt.production_write_enabled is False
    assert result.receipt.provider_call_attempted is False
    assert result.receipt.filesystem_mutation_attempted is False
    assert result.receipt.authority_flags.provider_call_allowed is False
    assert result.receipt.authority_flags.filesystem_write_allowed is False
    claim = result.write_claim()
    assert evaluate_memory_write_claim(
        claim,
        receipts=(result.receipt,),
        allow_local_test_receipts=True,
    ).allowed is True


def test_memory_write_denies_erase_delete_retention_unredacted_private_and_child_cases() -> None:
    from magi_agent.harness.memory_write import (
        MemoryWriteHarness,
        MemoryWriteHarnessConfig,
        MemoryWritePolicy,
        MemoryWriteRequest,
    )

    harness = MemoryWriteHarness(
        MemoryWriteHarnessConfig(enabled=True, localFakeAdapterEnabled=True),
        adapter=SpyMemoryAdapter(),
    )
    base_policy = MemoryWritePolicy(
        policyRef="policy:memory-write",
        policySnapshotRef="policy-snapshot:pr6",
        localFakeSuccessAllowed=True,
    )
    cases = (
        (
            MemoryWriteRequest(
                providerId="agentmemory",
                turnId="turn-erase",
                operation="erase",
                targetSha256="sha256:target",
                evidenceRefs=("evidence:erase",),
                approvalRef="approval:erase",
            ),
            base_policy,
            "memory_erase_denied",
        ),
        (
            MemoryWriteRequest(
                providerId="agentmemory",
                turnId="turn-delete",
                operation="delete",
                targetSha256="sha256:target",
                evidenceRefs=("evidence:delete",),
                approvalRef="approval:delete",
            ),
            base_policy,
            "memory_delete_denied",
        ),
        (
            MemoryWriteRequest(
                providerId="agentmemory",
                turnId="turn-retention",
                operation="remember",
                content="expired retention write",
                evidenceRefs=("evidence:retention",),
                approvalRef="approval:retention",
            ),
            base_policy.model_copy(update={"retentionState": "expired"}),
            "memory_retention_expired_denied",
        ),
        (
            MemoryWriteRequest(
                providerId="agentmemory",
                turnId="turn-unredacted",
                operation="remember",
                content="unredacted memory write",
                evidenceRefs=("evidence:unredacted",),
                approvalRef="approval:unredacted",
            ),
            base_policy.model_copy(update={"redactionStatus": "unverified"}),
            "memory_redaction_not_verified",
        ),
        (
            MemoryWriteRequest(
                providerId="agentmemory",
                turnId="turn-private",
                operation="remember",
                content="private memory payload must not be written",
                evidenceRefs=("evidence:private",),
                approvalRef="approval:private",
                privatePayload=True,
            ),
            base_policy,
            "private_memory_payload_denied",
        ),
        (
            MemoryWriteRequest(
                providerId="agentmemory",
                turnId="turn-child",
                operation="remember",
                content="child memory payload",
                evidenceRefs=("evidence:child",),
                approvalRef="approval:child",
                childMemoryIsolated=True,
            ),
            base_policy,
            "child_memory_scope_isolated",
        ),
    )

    for request, policy, reason in cases:
        result = asyncio.run(harness.write(request=request, policy=policy))
        encoded = json.dumps(result.public_projection(), sort_keys=True)

        assert result.status == "blocked", reason
        assert reason in result.reason_codes
        assert "private memory payload" not in encoded
        assert "child memory payload" not in encoded
        assert "unredacted memory write" not in encoded
        assert result.receipt is None or result.receipt.executed is False


def test_compaction_default_off_and_receipt_schema_is_digest_only() -> None:
    from magi_agent.harness.memory_compaction import (
        MemoryCompactionHarness,
        MemoryCompactionHarnessConfig,
        MemoryCompactionPolicy,
        MemoryCompactionRequest,
    )

    adapter = SpyMemoryAdapter()
    disabled = asyncio.run(
        MemoryCompactionHarness(adapter=adapter).compact(
            request=MemoryCompactionRequest(
                providerId="agentmemory",
                turnId="turn-compact-disabled",
                sourceRefs=("memory:daily.2026-05-20",),
                excludedRefs=("memory:excluded.2026-05-20",),
                sourceTexts=("raw source text must not appear",),
                outputText="raw compacted output must not appear",
                evidenceRefs=("evidence:compaction",),
                approvalRef="approval:compaction",
            ),
            policy=MemoryCompactionPolicy(
                policyRef="policy:memory-compaction",
                policySnapshotRef="policy-snapshot:pr6",
                localFakeCompactionAllowed=True,
            ),
        )
    )
    approved = asyncio.run(
        MemoryCompactionHarness(
            MemoryCompactionHarnessConfig(enabled=True, localFakeAdapterEnabled=True),
            adapter=adapter,
        ).compact(
            request=MemoryCompactionRequest(
                providerId="agentmemory",
                turnId="turn-compact-approved",
                sourceRefs=("memory:daily.2026-05-20", "/Users/kevin/private/raw.md"),
                excludedRefs=("memory:excluded.2026-05-20",),
                sourceTexts=("raw source text must not appear",),
                outputText="raw compacted output must not appear",
                evidenceRefs=("evidence:compaction",),
                approvalRef="approval:compaction",
            ),
            policy=MemoryCompactionPolicy(
                policyRef="policy:memory-compaction",
                policySnapshotRef="policy-snapshot:pr6",
                localFakeCompactionAllowed=True,
            ),
        )
    )

    assert adapter.calls == 0
    assert disabled.status == "disabled"
    assert disabled.receipt.status == "disabled"
    assert approved.status == "success"
    assert approved.receipt.schema_version == "memoryCompactionReceipt.v1"
    assert approved.receipt.source_refs == (
        "memory:daily.2026-05-20",
        "[private-path-redacted]",
    )
    assert approved.receipt.excluded_refs == ("memory:excluded.2026-05-20",)
    assert approved.receipt.redaction_status == "verified"
    assert approved.receipt.output_digest.startswith("sha256:")
    assert approved.receipt.policy_snapshot_digest.startswith("sha256:")
    assert approved.receipt.production_write_enabled is False
    assert approved.receipt.provider_call_attempted is False
    assert approved.receipt.filesystem_mutation_attempted is False
    assert approved.receipt.database_mutation_attempted is False
    assert approved.receipt.network_call_attempted is False
    encoded = json.dumps(approved.public_projection(), sort_keys=True)
    raw_dump = json.dumps(approved.model_dump(by_alias=True, mode="json"), sort_keys=True)
    for forbidden in (
        "raw source text",
        "raw compacted output",
        "/Users/kevin",
        "raw.md",
    ):
        assert forbidden not in encoded
        assert forbidden not in raw_dump


def test_compaction_denies_missing_policy_evidence_approval_and_unsafe_sources() -> None:
    from magi_agent.harness.memory_compaction import (
        MemoryCompactionHarness,
        MemoryCompactionHarnessConfig,
        MemoryCompactionPolicy,
        MemoryCompactionRequest,
    )

    harness = MemoryCompactionHarness(
        MemoryCompactionHarnessConfig(enabled=True, localFakeAdapterEnabled=True),
        adapter=SpyMemoryAdapter(),
    )
    base_request = MemoryCompactionRequest(
        providerId="agentmemory",
        turnId="turn-compact-policy",
        sourceRefs=("memory:daily.2026-05-20",),
        outputText="compacted digest source",
        evidenceRefs=("evidence:compaction",),
    )
    approval_policy = MemoryCompactionPolicy(
        policyRef="policy:memory-compaction",
        policySnapshotRef="policy-snapshot:pr6",
        approvalRequired=True,
        localFakeCompactionAllowed=True,
    )

    cases = (
        (base_request, None, "missing_memory_compaction_policy", "blocked"),
        (
            base_request.model_copy(update={"evidenceRefs": ()}),
            approval_policy,
            "missing_memory_compaction_evidence",
            "blocked",
        ),
        (
            base_request,
            approval_policy,
            "missing_memory_compaction_approval",
            "approval_required",
        ),
        (
            base_request.model_copy(update={"approvalRef": "approval:compaction"}),
            approval_policy.model_copy(update={"redactionStatus": "failed"}),
            "memory_compaction_redaction_not_verified",
            "blocked",
        ),
        (
            base_request.model_copy(update={"approvalRef": "approval:compaction"}),
            approval_policy.model_copy(update={"retentionState": "expired"}),
            "memory_compaction_retention_expired_denied",
            "blocked",
        ),
        (
            base_request.model_copy(
                update={"approvalRef": "approval:compaction", "privatePayload": True}
            ),
            approval_policy,
            "private_memory_payload_denied",
            "blocked",
        ),
        (
            base_request.model_copy(
                update={"approvalRef": "approval:compaction", "childMemoryIsolated": True}
            ),
            approval_policy,
            "child_memory_scope_isolated",
            "blocked",
        ),
    )

    for request, policy, reason, status in cases:
        result = asyncio.run(harness.compact(request=request, policy=policy))

        assert result.status == status, reason
        assert reason in result.reason_codes
        assert result.receipt.executed is False
        assert result.receipt.provider_call_attempted is False


def test_harness_config_defaults_are_inert_when_master_off() -> None:
    """PR1 governance: with the resolver master OFF (the default), a
    default-constructed harness config is fully inert — every write/provider/
    filesystem/network activation field is False — so the OFF path stays
    byte-identical to the pre-PR1 scaffold even though the relaxed fields are now
    plain ``bool`` (they can be turned ON by later PRs, see
    ``test_relaxed_authority_fields_reflect_explicit_activation`` below).

    The DB-mutation and ADK-memory-service-write fields plus ``traffic_attached``
    remain permanently-frozen ``Literal[False]``.
    """
    from magi_agent.harness.memory_compaction import (
        MemoryCompactionHarnessConfig,
        MemoryCompactionPolicy,
    )
    from magi_agent.harness.memory_write import (
        MemoryWriteHarnessConfig,
        MemoryWritePolicy,
    )

    write_config = MemoryWriteHarnessConfig.model_validate({})
    compaction_config = MemoryCompactionHarnessConfig.model_validate({})
    write_policy = MemoryWritePolicy.model_validate(
        {
            "policyRef": "policy:memory-write",
            "policySnapshotRef": "policy-snapshot:pr6",
        }
    )
    compaction_policy = MemoryCompactionPolicy.model_validate(
        {
            "policyRef": "policy:memory-compaction",
            "policySnapshotRef": "policy-snapshot:pr6",
        }
    )

    # Relaxed activation fields default OFF (inert when master is off).
    assert write_config.production_write_enabled is False
    assert write_config.provider_call_allowed is False
    assert write_config.filesystem_mutation_allowed is False
    assert write_config.network_call_allowed is False
    assert compaction_config.production_write_enabled is False
    assert compaction_config.provider_call_allowed is False
    assert compaction_config.filesystem_mutation_allowed is False
    assert compaction_config.network_call_allowed is False

    # Permanently-frozen authority fields stay False.
    assert write_config.database_mutation_allowed is False
    assert write_config.adk_memory_service_write_enabled is False
    assert write_config.traffic_attached is False
    assert compaction_config.database_mutation_allowed is False
    assert compaction_config.adk_memory_service_write_enabled is False
    assert compaction_config.traffic_attached is False

    # Policy snapshots are unchanged in PR1 — still locked False.
    assert write_policy.production_write_enabled is False
    assert write_policy.provider_call_allowed is False
    assert compaction_policy.production_write_enabled is False
    assert compaction_policy.provider_call_allowed is False


def test_relaxed_authority_fields_reflect_explicit_activation() -> None:
    """PR1 governance: a flag gates *activation*, never *capability*.  The
    relaxed authority fields are now plain ``bool`` — when explicitly turned ON
    (the seam later PRs drive from ``resolve_memory_config``) they actually flip
    ON.  A dead flag-on path would be a governance violation, so we assert the
    fields are honoured here even though no later-PR wiring consumes them yet.

    The DB-mutation / ADK-memory-service-write / traffic fields stay
    permanently-frozen ``Literal[False]`` and coerce any forged ``True`` to
    ``False``.
    """
    from magi_agent.harness.memory_compaction import MemoryCompactionHarnessConfig
    from magi_agent.harness.memory_write import MemoryWriteHarnessConfig

    write_config = MemoryWriteHarnessConfig.model_validate(
        {
            "enabled": True,
            "localFakeAdapterEnabled": True,
            "productionWriteEnabled": True,
            "providerCallAllowed": True,
            "filesystemMutationAllowed": True,
            "networkCallAllowed": True,
            # Forged frozen fields must still coerce to False.
            "databaseMutationAllowed": True,
            "adkMemoryServiceWriteEnabled": True,
            "trafficAttached": True,
        }
    )
    compaction_config = MemoryCompactionHarnessConfig.model_validate(
        {
            "enabled": True,
            "localFakeAdapterEnabled": True,
            "productionWriteEnabled": True,
            "providerCallAllowed": True,
            "filesystemMutationAllowed": True,
            "networkCallAllowed": True,
            "databaseMutationAllowed": True,
            "adkMemoryServiceWriteEnabled": True,
            "trafficAttached": True,
        }
    )

    # Relaxed fields are now honoured (capability follows the flag).
    assert write_config.production_write_enabled is True
    assert write_config.provider_call_allowed is True
    assert write_config.filesystem_mutation_allowed is True
    assert write_config.network_call_allowed is True
    assert compaction_config.production_write_enabled is True
    assert compaction_config.provider_call_allowed is True
    assert compaction_config.filesystem_mutation_allowed is True
    assert compaction_config.network_call_allowed is True

    # Permanently-frozen authority fields reject the forged True.
    assert write_config.database_mutation_allowed is False
    assert write_config.adk_memory_service_write_enabled is False
    assert write_config.traffic_attached is False
    assert compaction_config.database_mutation_allowed is False
    assert compaction_config.adk_memory_service_write_enabled is False
    assert compaction_config.traffic_attached is False

    # model_construct goes through the same validation path.
    constructed_write = MemoryWriteHarnessConfig.model_construct(
        enabled=True,
        productionWriteEnabled=True,
        databaseMutationAllowed=True,
    )
    constructed_compaction = MemoryCompactionHarnessConfig.model_construct(
        enabled=True,
        productionWriteEnabled=True,
        databaseMutationAllowed=True,
    )
    assert constructed_write.production_write_enabled is True
    assert constructed_write.database_mutation_allowed is False
    assert constructed_compaction.production_write_enabled is True
    assert constructed_compaction.database_mutation_allowed is False


def test_public_projections_redact_sensitive_write_and_compaction_strings() -> None:
    from magi_agent.harness.memory_compaction import (
        MemoryCompactionHarness,
        MemoryCompactionHarnessConfig,
        MemoryCompactionPolicy,
        MemoryCompactionRequest,
    )
    from magi_agent.harness.memory_write import (
        MemoryWriteHarness,
        MemoryWriteHarnessConfig,
        MemoryWritePolicy,
        MemoryWriteRequest,
    )

    unsafe_write = asyncio.run(
        MemoryWriteHarness(MemoryWriteHarnessConfig(enabled=True, localFakeAdapterEnabled=True)).write(
            request=MemoryWriteRequest(
                providerId="provider-raw-policy-snapshot-text-must-not-appear",
                turnId="turn-raw-control-metadata-must-not-appear",
                operation="remember",
                targetSha256="raw source text must not appear",
                content=(
                    "raw_prompt: hidden\n"
                    "Authorization: Bearer unsafe-token\n"
                    "Cookie: session=unsafe\n"
                    "connector_token=unsafe\n"
                    "/Users/kevin/private/path.txt\n"
                    "private_memory_payload: do-not-leak"
                ),
                evidenceRefs=("evidence:secret",),
                approvalRef="approval:secret",
                childPrompt="child prompt secret",
                toolLogs="tool log secret",
            ),
            policy=MemoryWritePolicy(
                policyRef="policy:memory-write",
                policySnapshotRef="raw policy snapshot text must not appear",
                localFakeSuccessAllowed=True,
            ),
        )
    )
    unsafe_compaction = asyncio.run(
        MemoryCompactionHarness(
            MemoryCompactionHarnessConfig(enabled=True, localFakeAdapterEnabled=True)
        ).compact(
            request=MemoryCompactionRequest(
                providerId="agentmemory",
                turnId="turn-compact-secret",
                sourceRefs=(
                    "raw source text must not appear",
                    "source:raw-source-text-must-not-appear",
                    "memory:raw-policy-snapshot-text-must-not-appear",
                ),
                excludedRefs=(
                    "raw output text must not appear",
                    "memory:raw-output-text-must-not-appear",
                    "policy-snapshot:raw-policy-snapshot-text-must-not-appear",
                    "private_memory_payload: do-not-leak",
                ),
                sourceTexts=("raw transcript tool log secret",),
                outputText="raw output Cookie: session=unsafe",
                evidenceRefs=("evidence:secret",),
                approvalRef="approval:secret",
            ),
            policy=MemoryCompactionPolicy(
                policyRef="policy:memory-compaction",
                policySnapshotRef="raw compaction policy text must not appear",
                localFakeCompactionAllowed=True,
            ),
        )
    )
    encoded = json.dumps(
        [unsafe_write.public_projection(), unsafe_compaction.public_projection()],
        sort_keys=True,
    )
    raw_dump = json.dumps(
        [
            unsafe_write.model_dump(by_alias=True, mode="json"),
            unsafe_compaction.model_dump(by_alias=True, mode="json"),
        ],
        sort_keys=True,
    )

    for forbidden in (
        "raw_prompt",
        "provider-raw-source-text-must-not-appear",
        "provider-raw-policy-snapshot-text-must-not-appear",
        "turn-raw-control-metadata-must-not-appear",
        "source:raw-source-text-must-not-appear",
        "memory:raw-output-text-must-not-appear",
        "memory:raw-policy-snapshot-text-must-not-appear",
        "policy-snapshot:raw-policy-snapshot-text-must-not-appear",
        "raw source text must not appear",
        "raw policy snapshot text must not appear",
        "raw compaction policy text must not appear",
        "raw output text must not appear",
        "Authorization",
        "Bearer",
        "unsafe-token",
        "Cookie",
        "session=unsafe",
        "connector_token",
        "/Users/kevin",
        "session-key",
        "private_memory_payload",
        "do-not-leak",
        "child prompt",
        "tool log",
        "raw transcript",
        "raw output",
    ):
        assert forbidden not in encoded
        assert forbidden not in raw_dump


@pytest.mark.xfail(
    strict=False,
    reason=(
        "Subprocess-based import-boundary probe flakes on some hosts where the "
        "interpreter eagerly loads socket/subprocess/urllib at startup. Tracked "
        "in openmagi/magi-agent CI-baseline quarantine; do not fix in the CI "
        "bootstrap PR."
    ),
)
def test_memory_harness_import_boundary_has_no_live_adk_model_provider_or_network_imports() -> None:
    python_root = Path(__file__).resolve().parents[1]
    module_paths = [
        python_root / "magi_agent/harness/memory_write.py",
        python_root / "magi_agent/harness/memory_compaction.py",
    ]
    banned_roots = {
        "google",
        "openai",
        "anthropic",
        "httpx",
        "requests",
        "urllib",
        "socket",
        "supabase",
        "psycopg",
        "asyncpg",
    }

    for module_path in module_paths:
        tree = ast.parse(module_path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            assert not (banned_roots & {name.split(".")[0] for name in names}), (
                module_path,
                names,
            )

    code = """
import sys
import magi_agent.harness.memory_write
import magi_agent.harness.memory_compaction
for name in (
    'google.adk',
    'google.genai',
    'openai',
    'anthropic',
    'httpx',
    'requests',
    'socket',
    'supabase',
    'psycopg',
    'asyncpg',
    'magi_agent.runtime.adk_turn_runner',
    'magi_agent.runtime.provider_execution',
    'magi_agent.app',
):
    if name in sys.modules:
        raise SystemExit(name)
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        text=True,
        capture_output=True,
        cwd=python_root,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
