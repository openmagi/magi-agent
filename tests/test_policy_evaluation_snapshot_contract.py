from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from openmagi_core_agent.runtime.policy_snapshot import (
    EffectivePolicySnapshot,
    PolicyDecisionBinding,
    PolicySourceRef,
    build_effective_policy_snapshot,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "deterministic_runtime"


def _digest(char: str) -> str:
    return "sha256:" + char * 64


def _source_ref(name: str, digest_char: str) -> PolicySourceRef:
    return PolicySourceRef(
        sourceName=name,
        sourceVersion="2026-05-21.1",
        sourceDigest=_digest(digest_char),
        authoritative=True,
    )


def test_effective_policy_snapshot_digest_is_stable_and_canonical() -> None:
    snapshot = build_effective_policy_snapshot(
        policyId="policy:research-cited-brief",
        policyVersion="1.0.0",
        sources=(
            _source_ref("platform hard safety policy", "1"),
            _source_ref("user agent.config.yaml", "2"),
        ),
        recipeRefs=("recipe:research.cited-brief@1.0.0",),
        validatorRefs=("validator:quoteExactMatch@1",),
        toolAllowlist=("SourceOpen", "CitationVerify"),
        projectionPolicyRef="projection:evidence-first@1",
        repairPolicyRef="repair:bounded-3@1",
        approvalPolicyRef="approval:research-readonly@1",
        modelTierPolicyRef="model-tier:cheap-research@1",
        gateRefs=("gate:research-readonly",),
    )
    clone = EffectivePolicySnapshot.model_validate(snapshot.model_dump(by_alias=True))

    assert clone.effective_policy_snapshot_digest == snapshot.effective_policy_snapshot_digest
    assert clone.policy_version == "1.0.0"
    assert clone.recipe_refs == ("recipe:research.cited-brief@1.0.0",)


def test_config_change_creates_new_snapshot_without_rewriting_old_decision() -> None:
    old_snapshot = build_effective_policy_snapshot(
        policyId="policy:backoffice-numeric",
        policyVersion="1.0.0",
        sources=(_source_ref("user agent.config.yaml", "3"),),
        recipeRefs=("recipe:backoffice.numeric-audit@1.0.0",),
        validatorRefs=("validator:numericClaimsMatchSource@1",),
        toolAllowlist=("Calculation",),
        projectionPolicyRef="projection:structured-ledger@1",
        repairPolicyRef="repair:bounded-2@1",
        approvalPolicyRef="approval:none@1",
        modelTierPolicyRef="model-tier:cheap-backoffice@1",
        gateRefs=("gate:backoffice-readonly",),
    )
    old_decision = PolicyDecisionBinding(
        decisionId="decision-001",
        effectivePolicySnapshotDigest=old_snapshot.effective_policy_snapshot_digest,
        selectedActionDigest=_digest("4"),
        verdict="allow",
    )

    new_snapshot = build_effective_policy_snapshot(
        policyId="policy:backoffice-numeric",
        policyVersion="1.0.1",
        sources=(_source_ref("user agent.config.yaml", "5"),),
        recipeRefs=("recipe:backoffice.numeric-audit@1.0.1",),
        validatorRefs=("validator:numericClaimsMatchSource@1", "validator:sheetRangeExists@1"),
        toolAllowlist=("Calculation", "SpreadsheetRead"),
        projectionPolicyRef="projection:structured-ledger@1",
        repairPolicyRef="repair:bounded-2@1",
        approvalPolicyRef="approval:none@1",
        modelTierPolicyRef="model-tier:cheap-backoffice@1",
        gateRefs=("gate:backoffice-readonly",),
    )

    assert old_snapshot.effective_policy_snapshot_digest != new_snapshot.effective_policy_snapshot_digest
    assert old_decision.effective_policy_snapshot_digest == old_snapshot.effective_policy_snapshot_digest
    assert old_decision.effective_policy_snapshot_digest != new_snapshot.effective_policy_snapshot_digest


def test_snapshot_rejects_missing_policy_sources_and_raw_config() -> None:
    with pytest.raises(ValidationError, match="sources"):
        build_effective_policy_snapshot(
            policyId="policy:bad",
            policyVersion="1.0.0",
            sources=(),
            recipeRefs=("recipe:research@1",),
            validatorRefs=("validator:quoteExactMatch@1",),
            toolAllowlist=("SourceOpen",),
            projectionPolicyRef="projection:evidence-first@1",
            repairPolicyRef="repair:bounded-3@1",
            approvalPolicyRef="approval:none@1",
            modelTierPolicyRef="model-tier:cheap@1",
            gateRefs=("gate:research",),
        )


def test_policy_decision_binding_requires_digest_and_closed_verdict() -> None:
    binding = PolicyDecisionBinding(
        decisionId="decision-002",
        effectivePolicySnapshotDigest=_digest("6"),
        selectedActionDigest=_digest("7"),
        verdict="deny",
        reasonCodes=("tool_not_allowlisted",),
    )
    assert binding.verdict == "deny"
    assert binding.reason_codes == ("tool_not_allowlisted",)


def test_policy_snapshot_fixture_is_digest_only_and_valid() -> None:
    fixture = json.loads((FIXTURE_DIR / "policy_snapshot.json").read_text())
    snapshot = EffectivePolicySnapshot.model_validate(fixture)

    assert snapshot.effective_policy_snapshot_digest.startswith("sha256:")
    encoded = json.dumps(fixture, sort_keys=True).lower()
    assert "authorization" not in encoded
    assert "cookie" not in encoded
    assert "raw prompt" not in encoded
