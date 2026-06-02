from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from openmagi_core_agent.recipes.composition import AdmittedRecipeSnapshot
from openmagi_core_agent.recipes.merge_algebra import (
    EffectiveRecipeMergeContract,
    RetryMergePolicy,
    merge_admitted_recipe_snapshots,
)

MATRIX_PATH = (
    Path(__file__).parent / "fixtures" / "recipe_composition" / "matrix.json"
)


def _snapshot(
    recipe_ref: str,
    *,
    hard_safety: bool = False,
    tool_grants: tuple[str, ...] = (),
    tool_denials: tuple[str, ...] = (),
    evidence_requirements: tuple[str, ...] = (),
    approval_requirements: tuple[str, ...] = (),
    context_requirements: tuple[str, ...] = (),
    retry_policy: str = "none",
    projection_rules: tuple[str, ...] = (),
    registry_admitted: bool = True,
) -> AdmittedRecipeSnapshot:
    payload: dict[str, object] = {
        "recipeRef": recipe_ref,
        "snapshotDigest": "sha256:" + "0" * 64,
        "version": "v1",
        "source": "fixture",
        "governed": True,
        "hardSafety": hard_safety,
        "toolGrants": tool_grants,
        "toolDenials": tool_denials,
        "evidenceRequirements": evidence_requirements,
        "approvalRequirements": approval_requirements,
        "contextRequirements": context_requirements,
        "hookContributions": (),
        "retryPolicy": retry_policy,
        "projectionRules": projection_rules,
    }
    payload["snapshotDigest"] = AdmittedRecipeSnapshot.compute_snapshot_digest(payload)
    if registry_admitted:
        return AdmittedRecipeSnapshot._from_registry_snapshot(payload)
    return AdmittedRecipeSnapshot(**payload)


def _merge_contract_payload(
    contract: EffectiveRecipeMergeContract,
    **overrides: object,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schemaVersion": contract.schema_version,
        "recipeRefs": contract.recipe_refs,
        "hardSafetyRefs": contract.hard_safety_refs,
        "hardSafetyMode": contract.hard_safety_mode,
        "toolGrants": contract.tool_grants,
        "toolDenials": contract.tool_denials,
        "approvalRequirements": contract.approval_requirements,
        "evidenceRequirements": contract.evidence_requirements,
        "contextRequirements": contract.context_requirements,
        "retryPolicy": contract.retry_policy,
        "conflicts": contract.conflicts,
        "blocked": contract.blocked,
        "defaultOff": contract.default_off,
        "trafficAttached": contract.traffic_attached,
        "executionAttached": contract.execution_attached,
        "liveActivation": contract.live_activation,
        "mergeDigest": contract.merge_digest,
    }
    payload.update(overrides)
    return payload


def test_grant_and_deny_resolves_denied_and_blocks_conflict() -> None:
    result = merge_admitted_recipe_snapshots(
        (
            _snapshot("recipe.granting", tool_grants=("tool.web.fetch",)),
            _snapshot("recipe.denying", tool_denials=("tool.web.fetch",)),
        )
    )

    assert result.blocked is True
    assert result.tool_grants == ()
    assert result.tool_denials == ("tool.web.fetch",)
    assert result.conflicts[0].code == "tool_denied_grant"
    assert result.conflicts[0].blocking is True
    assert result.conflicts[0].subject_ref == "tool.web.fetch"


def test_approval_requirements_merge_by_union() -> None:
    result = merge_admitted_recipe_snapshots(
        (
            _snapshot("recipe.alpha", approval_requirements=("approval.operator",)),
            _snapshot(
                "recipe.beta",
                approval_requirements=("approval.owner", "approval.operator"),
            ),
        )
    )

    assert result.blocked is False
    assert result.approval_requirements == ("approval.operator", "approval.owner")


def test_hard_safety_enforces_over_log_only_downgrade() -> None:
    result = merge_admitted_recipe_snapshots(
        (
            _snapshot(
                "recipe.regular",
                projection_rules=("hardSafety.mode:log_only",),
            ),
            _snapshot(
                "recipe.safety",
                hard_safety=True,
                projection_rules=("hardSafety.mode:enforce",),
            ),
        )
    )

    assert result.blocked is False
    assert result.hard_safety_mode == "enforce"
    assert result.hard_safety_refs == ("recipe.safety",)
    assert result.conflicts[0].code == "hard_safety_downgrade_rejected"
    assert result.conflicts[0].blocking is False


def test_hard_safety_recipe_cannot_declare_disabled_mode() -> None:
    result = merge_admitted_recipe_snapshots(
        (
            _snapshot(
                "recipe.safety",
                hard_safety=True,
                projection_rules=("hardSafety.mode:disabled",),
            ),
        )
    )

    assert result.blocked is True
    assert result.hard_safety_mode == "enforce"
    assert result.conflicts[0].code == "hard_safety_invalid_mode"


def test_evidence_requirements_merge_union_with_strictest_per_class() -> None:
    result = merge_admitted_recipe_snapshots(
        (
            _snapshot(
                "recipe.evidence.base",
                evidence_requirements=(
                    "evidence.claim:optional",
                    "evidence.audit:required",
                ),
            ),
            _snapshot(
                "recipe.evidence.strict",
                evidence_requirements=("evidence.claim:blocking",),
            ),
        )
    )

    assert result.blocked is False
    assert result.evidence_requirements == (
        "evidence.audit:required",
        "evidence.claim:blocking",
    )


def test_context_requirements_choose_least_privilege_per_class() -> None:
    result = merge_admitted_recipe_snapshots(
        (
            _snapshot(
                "recipe.context.broad",
                context_requirements=(
                    "context.workspace:full",
                    "context.history:summary",
                ),
            ),
            _snapshot(
                "recipe.context.narrow",
                context_requirements=("context.workspace:refs_only",),
            ),
        )
    )

    assert result.blocked is False
    assert result.context_requirements == (
        "context.history:summary",
        "context.workspace:refs_only",
    )


def test_unknown_context_strictness_conflicts_and_uses_least_privilege() -> None:
    result = merge_admitted_recipe_snapshots(
        (
            _snapshot(
                "recipe.context.unknown",
                context_requirements=("context.workspace:refsOnly",),
            ),
        )
    )

    assert result.blocked is True
    assert result.context_requirements == ("context.workspace:refsOnly:none",)
    assert result.conflicts[0].code == "context_requirement_unrecognized"


def test_unknown_evidence_strictness_conflicts_and_uses_strictest() -> None:
    result = merge_admitted_recipe_snapshots(
        (
            _snapshot(
                "recipe.evidence.unknown",
                evidence_requirements=("evidence.claim:custom",),
            ),
        )
    )

    assert result.blocked is True
    assert result.evidence_requirements == ("evidence.claim:custom:blocking",)
    assert result.conflicts[0].code == "evidence_requirement_unrecognized"


def test_retry_policy_is_bounded_by_global_cap_and_strictest_recipe_cap() -> None:
    matrix = json.loads(MATRIX_PATH.read_text())
    retry_row = next(row for row in matrix["rows"] if row["id"] == "global_retry_cap")
    expected_cap = retry_row["expectedOutcome"]["retryCap"]

    result = merge_admitted_recipe_snapshots(
        (
            _snapshot("recipe.retry.wide", retry_policy="retry:max:5"),
            _snapshot("recipe.retry.tight", retry_policy="retry:max:2:repair:1"),
        ),
        global_retry_cap=expected_cap,
    )

    assert result.blocked is False
    assert result.retry_policy.max_attempts == expected_cap
    assert result.retry_policy.repair_attempts == 1
    assert result.retry_policy.global_cap == expected_cap


def test_retry_policy_none_caps_repair_attempts_to_zero() -> None:
    result = merge_admitted_recipe_snapshots(
        (
            _snapshot("recipe.retry.none", retry_policy="none"),
            _snapshot("recipe.retry.repair", retry_policy="retry:max:3:repair:1"),
        ),
        global_retry_cap=3,
    )

    assert result.blocked is False
    assert result.retry_policy.max_attempts == 0
    assert result.retry_policy.repair_attempts == 0
    assert result.retry_policy.global_cap == 3


def test_retry_policy_zero_retry_caps_composed_repair_attempts_to_zero() -> None:
    result = merge_admitted_recipe_snapshots(
        (
            _snapshot("recipe.retry.zero", retry_policy="retry:max:0"),
            _snapshot("recipe.retry.repair", retry_policy="retry:max:3:repair:1"),
        ),
        global_retry_cap=3,
    )

    assert result.blocked is False
    assert result.retry_policy.max_attempts == 0
    assert result.retry_policy.repair_attempts == 0
    assert result.retry_policy.global_cap == 3


def test_retry_policy_model_rejects_repair_without_retry_attempts() -> None:
    with pytest.raises(ValidationError, match="repair attempts exceed retry max attempts"):
        RetryMergePolicy(maxAttempts=0, repairAttempts=1, globalCap=3)

    with pytest.raises(ValidationError, match="repair attempts exceed retry max attempts"):
        RetryMergePolicy.model_construct(
            max_attempts=0,
            repair_attempts=1,
            global_cap=3,
        )


def test_unbounded_retry_policy_conflicts_and_blocks() -> None:
    result = merge_admitted_recipe_snapshots(
        (_snapshot("recipe.retry.unbounded", retry_policy="retry:unbounded"),),
        global_retry_cap=3,
    )

    assert result.blocked is True
    assert result.retry_policy.max_attempts == 0
    assert result.conflicts[0].code == "retry_policy_unbounded"


def test_merge_digest_is_stable_independent_of_input_order() -> None:
    alpha = _snapshot(
        "recipe.alpha",
        approval_requirements=("approval.owner",),
        evidence_requirements=("evidence.claim:required",),
    )
    beta = _snapshot(
        "recipe.beta",
        approval_requirements=("approval.operator",),
        evidence_requirements=("evidence.claim:blocking",),
    )

    first = merge_admitted_recipe_snapshots((alpha, beta))
    second = merge_admitted_recipe_snapshots((beta, alpha))

    assert first.merge_digest == second.merge_digest
    assert first.public_projection() == second.public_projection()
    assert first.recipe_refs == ("recipe.alpha", "recipe.beta")


def test_non_registry_snapshot_is_rejected() -> None:
    with pytest.raises(ValueError, match="registry-resolved"):
        merge_admitted_recipe_snapshots(
            (_snapshot("recipe.untrusted", registry_admitted=False),)
        )


def test_public_projection_and_serialization_are_digest_safe() -> None:
    result = merge_admitted_recipe_snapshots(
        (
            _snapshot(
                "recipe.alpha",
                tool_grants=("tool.file.read",),
                tool_denials=("tool.shell.run",),
                approval_requirements=("approval.operator",),
                evidence_requirements=("evidence.claim:blocking",),
                context_requirements=("context.workspace:refs_only",),
                retry_policy="retry:max:1",
            ),
        )
    )

    projection = result.public_projection()
    dumped = result.model_dump(by_alias=True, mode="json")
    dumped_json = result.model_dump_json(by_alias=True)
    serialized = json.dumps(projection, sort_keys=True) + json.dumps(dumped) + dumped_json

    assert projection["toolGrantCount"] == 1
    assert projection["toolDenialCount"] == 1
    assert "tool.file.read" not in serialized
    assert "tool.shell.run" not in serialized
    assert "evidence.claim" not in serialized
    assert "context.workspace" not in serialized
    assert "approval.operator" not in serialized
    assert "mergeDigest" in dumped


def test_nested_merge_contract_serialization_is_digest_safe() -> None:
    class MergeEnvelope(BaseModel):
        contract: EffectiveRecipeMergeContract

    result = merge_admitted_recipe_snapshots(
        (
            _snapshot(
                "recipe.alpha",
                tool_grants=("tool.file.read",),
                evidence_requirements=("evidence.claim:blocking",),
                context_requirements=("context.workspace:refs_only",),
            ),
        )
    )

    dumped = MergeEnvelope(contract=result).model_dump(by_alias=True, mode="json")
    serialized = json.dumps(dumped, sort_keys=True)

    assert dumped["contract"]["toolGrantCount"] == 1
    assert "tool.file.read" not in serialized
    assert "evidence.claim" not in serialized
    assert "context.workspace" not in serialized


def test_merge_contract_projection_rejects_mutated_digest_mismatch() -> None:
    result = merge_admitted_recipe_snapshots(
        (_snapshot("recipe.alpha", tool_grants=("tool.file.read",)),)
    )
    result.__dict__["tool_grants"] = ("tool.shell.run",)

    with pytest.raises(ValueError, match="merge digest mismatch"):
        result.public_projection()


def test_manual_merge_contract_cannot_set_live_activation_flags() -> None:
    with pytest.raises(ValidationError):
        EffectiveRecipeMergeContract(
            recipeRefs=("recipe.alpha",),
            hardSafetyRefs=(),
            hardSafetyMode="none",
            toolGrants=(),
            toolDenials=(),
            approvalRequirements=(),
            evidenceRequirements=(),
            contextRequirements=(),
            retryPolicy={"maxAttempts": 0, "repairAttempts": 0, "globalCap": 3},
            conflicts=(),
            blocked=False,
            defaultOff=False,
            trafficAttached=True,
            executionAttached=True,
            liveActivation=True,
            mergeDigest="sha256:" + "0" * 64,
        )


def test_merge_contract_model_construct_canonicalizes_live_flags_and_checks_digest() -> None:
    result = merge_admitted_recipe_snapshots(
        (_snapshot("recipe.alpha", tool_denials=("tool.shell.run",)),)
    )

    constructed = EffectiveRecipeMergeContract.model_construct(
        **_merge_contract_payload(
            result,
            defaultOff=False,
            trafficAttached=True,
            executionAttached=True,
            liveActivation=True,
        )
    )

    assert constructed.default_off is True
    assert constructed.traffic_attached is False
    assert constructed.execution_attached is False
    assert constructed.live_activation is False
    assert constructed.public_projection()["trafficAttached"] is False

    with pytest.raises(ValidationError, match="merge digest mismatch"):
        EffectiveRecipeMergeContract.model_construct(
            **_merge_contract_payload(
                result,
                mergeDigest="sha256:" + "0" * 64,
            )
        )


def test_merge_contract_public_projection_rejects_mutated_digest() -> None:
    result = merge_admitted_recipe_snapshots(
        (_snapshot("recipe.alpha", tool_denials=("tool.shell.run",)),)
    )
    result.__dict__["merge_digest"] = "sha256:" + "0" * 64

    with pytest.raises(ValueError, match="merge digest mismatch"):
        result.public_projection()
