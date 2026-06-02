from __future__ import annotations

from datetime import UTC, datetime

from openmagi_core_agent.evidence.ledger_semantics import (
    ContentAddressedLedger,
    append_ledger_record,
    verify_ledger_chain,
)
from openmagi_core_agent.harness.approval_receipts import build_approval_receipt
from openmagi_core_agent.plugins.manifest import PluginManifest
from openmagi_core_agent.plugins.sandbox_policy import evaluate_plugin_sandbox
from openmagi_core_agent.runtime.policy_snapshot import (
    PolicySourceRef,
    build_effective_policy_snapshot,
)


PROTECTED_BINDING_ACCESS_KEY = "protectedBindingAccess"


def test_tool_decision_can_bind_policy_snapshot_approval_receipt_and_ledger_record() -> None:
    snapshot = build_effective_policy_snapshot(
        policyId="policy:gate1a-readonly",
        policyVersion="1.0.0",
        sources=(
            PolicySourceRef(
                sourceName="platform hard safety policy",
                sourceVersion="2026-05-21.1",
                sourceDigest="sha256:" + "1" * 64,
                authoritative=True,
            ),
        ),
        recipeRefs=("recipe:gate1a.readonly-tools@1",),
        validatorRefs=("validator:receiptSchema@1",),
        toolAllowlist=("FileRead",),
        projectionPolicyRef="projection:digest-only@1",
        repairPolicyRef="repair:bounded-1@1",
        approvalPolicyRef="approval:readonly-none@1",
        modelTierPolicyRef="model-tier:gemini-flash@1",
        gateRefs=("gate:1a",),
    )
    action_digest = "sha256:" + "2" * 64
    approval = build_approval_receipt(
        approvalId="approval-bind-001",
        approverRef="system-policy:readonly",
        approvalSource="platform_policy",
        approvedActionKind="tool_call",
        approvedActionDigest=action_digest,
        approvedScope="single_tool_call",
        policyDecisionId="decision-bind-001",
        effectivePolicySnapshotDigest=snapshot.effective_policy_snapshot_digest,
        issuedAt=datetime(2026, 5, 21, 12, 0, tzinfo=UTC),
        expiresAt=datetime(2026, 5, 21, 12, 5, tzinfo=UTC),
        constraints={"tool": "FileRead"},
    )
    ledger = append_ledger_record(
        ContentAddressedLedger(
            ledgerId="ledger-bind-001",
            sessionId="session-bind",
            turnId="turn-bind",
            mode="live",
            records=(),
            appendOnly=True,
            contentAddressed=True,
        ),
        kind="policy_snapshot",
        payloadDigest=snapshot.effective_policy_snapshot_digest,
        payloadRef="policy:snapshot:gate1a-readonly",
        policySnapshotDigest=snapshot.effective_policy_snapshot_digest,
    )
    ledger = append_ledger_record(
        ledger,
        kind="approval_receipt",
        payloadDigest=approval.approval_digest,
        payloadRef="approval:approval-bind-001",
        policySnapshotDigest=snapshot.effective_policy_snapshot_digest,
    )

    assert verify_ledger_chain(ledger).ok is True
    assert ledger.records[0].payload_digest == snapshot.effective_policy_snapshot_digest
    assert ledger.records[1].payload_digest == approval.approval_digest


def test_plugin_sandbox_decision_can_be_recorded_without_live_execution() -> None:
    manifest = PluginManifest.model_validate(
        {
            "id": "openmagi.readonly-validator",
            "kind": "native",
            "version": "1.0.0",
            "permissions": ["read"],
            "capabilities": [{"type": "verifier", "name": "receiptSchema"}],
            "trustLevel": "first_party",
            "supplyChainDigest": "sha256:" + "3" * 64,
            "sandbox": {
                "mode": "in_process_contract_only",
                "filesystem": "none",
                "network": "none",
                PROTECTED_BINDING_ACCESS_KEY: "none",
                "process": "none",
                "workspaceMutation": False,
                "channelDelivery": False,
            },
        }
    )
    decision = evaluate_plugin_sandbox(manifest)

    assert decision.ok is True
    assert decision.effective_permissions == ("read",)
