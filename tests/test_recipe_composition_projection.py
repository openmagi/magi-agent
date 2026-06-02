from __future__ import annotations

import pytest
from pydantic import ValidationError

from magi_agent.recipes.composition import (
    AdmittedRecipeSnapshot,
    RecipeStackInput,
)
from magi_agent.recipes.effective_contract import (
    build_effective_recipe_contract,
)
from magi_agent.recipes.projection import (
    RecipeCompositionMergeDecision,
    RecipeCompositionProjection,
    project_effective_recipe_contract,
)


def _snapshot(
    recipe_ref: str,
    *,
    governed: bool = True,
    hard_safety: bool = False,
    tool_grants: tuple[str, ...] = (),
    tool_denials: tuple[str, ...] = (),
    evidence_requirements: tuple[str, ...] = (),
    approval_requirements: tuple[str, ...] = (),
    context_requirements: tuple[str, ...] = (),
    retry_policy: str = "none",
    projection_rules: tuple[str, ...] = (),
) -> AdmittedRecipeSnapshot:
    payload: dict[str, object] = {
        "recipeRef": recipe_ref,
        "snapshotDigest": "sha256:" + "0" * 64,
        "version": "v1",
        "source": "fixture",
        "governed": governed,
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
    return AdmittedRecipeSnapshot._from_registry_snapshot(payload)


def test_effective_contract_projection_is_digest_safe_and_count_based() -> None:
    stack = RecipeStackInput(
        explicitRecipeRefs=("recipe.explicit.alpha",),
        autoRecipeRefs=("recipe.auto.beta",),
        hardSafetyRefs=("recipe.safety.hard",),
        allowAdditionalAutoRecipes=True,
        turnId="turn-1",
        sessionId="session-1",
    )
    contract = build_effective_recipe_contract(
        stack=stack,
        admitted_snapshots=(
            _snapshot(
                "recipe.explicit.alpha",
                tool_grants=("tool.file.read",),
                evidence_requirements=("evidence.claim:required",),
                approval_requirements=("approval.owner",),
                context_requirements=("context.workspace:refs_only",),
                retry_policy="retry:max:2:repair:1",
            ),
            _snapshot(
                "recipe.auto.beta",
                tool_grants=("tool.web.fetch",),
                evidence_requirements=("evidence.audit:required",),
                approval_requirements=("approval.operator",),
                context_requirements=("context.workspace:refs_only",),
                retry_policy="retry:max:2:repair:1",
            ),
            _snapshot(
                "recipe.safety.hard",
                hard_safety=True,
                tool_denials=("tool.shell.run",),
                projection_rules=("hardSafety.mode:enforce",),
            ),
        ),
        global_retry_cap=1,
    )

    projection = project_effective_recipe_contract(contract)
    rendered = projection.model_dump_json()

    assert projection.schema_version == "recipeCompositionProjection.v1"
    assert projection.effective_digest == contract.effective_digest
    assert projection.audit_digest.startswith("sha256:")
    assert projection.hard_safety_status == "enforced"
    assert projection.conflict_status == "clear"
    assert projection.public_safe_counts.tool_grant_count == 2
    assert projection.public_safe_counts.tool_denial_count == 1
    assert projection.public_safe_counts.evidence_requirement_count == 2
    assert projection.public_safe_counts.approval_requirement_count == 2
    assert projection.public_safe_counts.context_policy_count == 1
    assert projection.public_safe_counts.hook_count == 0
    assert {
        decision.code for decision in projection.merge_decisions
    } >= {"explicit.included", "auto.included", "hard_safety.enforced"}
    assert project_effective_recipe_contract(contract).audit_digest == projection.audit_digest
    assert project_effective_recipe_contract(contract).hard_safety_status == "enforced"

    for forbidden in (
        "tool.file.read",
        "tool.web.fetch",
        "tool.shell.run",
        "evidence.claim:required",
        "approval.owner",
        "context.workspace:refs_only",
        "raw prompt text",
        "/Users/alice/private/config.json",
        "sk-proj-fake-example-token",
    ):
        assert forbidden not in rendered


def test_projection_exposes_excluded_refs_and_nonblocking_conflict_status() -> None:
    explicit = _snapshot("recipe.explicit.base", tool_grants=("tool.web.fetch",))
    incompatible_auto = _snapshot(
        "recipe.auto.incompatible",
        tool_denials=("tool.web.fetch",),
    )
    stack = RecipeStackInput(
        explicitRecipeRefs=("recipe.explicit.base",),
        autoRecipeRefs=("recipe.auto.incompatible",),
        allowAdditionalAutoRecipes=True,
        turnId="turn-1",
        sessionId="session-1",
    )
    contract = build_effective_recipe_contract(
        stack=stack,
        admitted_snapshots=(explicit, incompatible_auto),
        auto_conflict_policy="exclude",
    )

    projection = project_effective_recipe_contract(contract)
    dumped = projection.model_dump(mode="json", by_alias=True)

    assert projection.blocked is False
    assert projection.conflict_status == "conflicted"
    assert dumped["excludedRefs"] == (
        {
            "recipeRef": "recipe.auto.incompatible",
            "reason": "auto_recipe_incompatible",
            "blocking": False,
        },
    )
    assert projection.excluded_reason_codes == ("auto_recipe_incompatible",)
    assert projection.conflicts[0].code == "auto_recipe_incompatible"
    assert projection.conflicts[0].blocking is False


def test_projection_represents_blocked_hard_safety_missing_contract() -> None:
    contract = build_effective_recipe_contract(
        stack=RecipeStackInput(
            hardSafetyRefs=("recipe.safety.missing",),
            turnId="turn-1",
            sessionId="session-1",
        ),
        admitted_snapshots=(),
    )

    projection = project_effective_recipe_contract(contract)

    assert projection.blocked is True
    assert projection.conflict_status == "blocked"
    assert projection.hard_safety_status == "blocked"
    assert projection.hard_safety_ref_count == 0
    assert projection.hard_safety_included_count == 0
    assert projection.conflicts[0].code == "hard_safety_recipe_missing"


@pytest.mark.parametrize(
    "unsafe_subject",
    (
        "raw prompt text",
        "/Users/alice/private/config.json",
        "sk-proj-fake-example-token",
        "toolArgs",
        "toolResult",
        "hiddenConfig",
        "rawPluginConfig",
    ),
)
def test_projection_rejects_unsafe_merge_decision_subjects(unsafe_subject: str) -> None:
    with pytest.raises(ValueError):
        RecipeCompositionMergeDecision.from_subject(
            code="projection.safe",
            subject_ref=unsafe_subject,
            recipe_ref_count=1,
            blocking=True,
        )


def test_projection_rejects_direct_authority_flag_forgery() -> None:
    contract = build_effective_recipe_contract(
        stack=RecipeStackInput(
            explicitRecipeRefs=("recipe.explicit.base",),
            turnId="turn-1",
            sessionId="session-1",
        ),
        admitted_snapshots=(_snapshot("recipe.explicit.base"),),
    )
    projection = project_effective_recipe_contract(contract)
    dumped = projection.model_dump(mode="json", by_alias=True)

    with pytest.raises((ValidationError, ValueError)):
        RecipeCompositionProjection.model_validate(
            {**dumped, "trafficAttached": True},
        )

    with pytest.raises(ValueError):
        projection.model_copy(update={"liveActivation": True})


def test_projection_rejects_recomputed_digest_with_inconsistent_metadata() -> None:
    contract = build_effective_recipe_contract(
        stack=RecipeStackInput(
            explicitRecipeRefs=("recipe.explicit.base",),
            turnId="turn-1",
            sessionId="session-1",
        ),
        admitted_snapshots=(_snapshot("recipe.explicit.base"),),
    )
    projection = project_effective_recipe_contract(contract)
    payload = {
        **projection.model_dump(mode="json", by_alias=True),
        "conflictCount": 1,
    }
    payload["auditDigest"] = RecipeCompositionProjection.compute_audit_digest(payload)

    with pytest.raises(ValueError, match="conflict count"):
        RecipeCompositionProjection.model_validate(payload)

    payload = {
        **projection.model_dump(mode="json", by_alias=True),
        "conflictStatus": "blocked",
    }
    payload["auditDigest"] = RecipeCompositionProjection.compute_audit_digest(payload)

    with pytest.raises(ValueError, match="conflict status"):
        RecipeCompositionProjection.model_validate(payload)


def test_projection_rejects_recomputed_digest_with_hard_safety_count_mismatch() -> None:
    stack = RecipeStackInput(
        explicitRecipeRefs=("recipe.explicit.base",),
        hardSafetyRefs=("recipe.safety.hard",),
        turnId="turn-1",
        sessionId="session-1",
    )
    contract = build_effective_recipe_contract(
        stack=stack,
        admitted_snapshots=(
            _snapshot("recipe.explicit.base"),
            _snapshot("recipe.safety.hard", hard_safety=True),
        ),
    )
    projection = project_effective_recipe_contract(contract)
    payload = {
        **projection.model_dump(mode="json", by_alias=True),
        "hardSafetyStatus": "not_required",
        "hardSafetyRefCount": 0,
        "hardSafetyIncludedCount": 1,
    }
    payload["auditDigest"] = RecipeCompositionProjection.compute_audit_digest(payload)

    with pytest.raises(ValueError, match="hard safety"):
        RecipeCompositionProjection.model_validate(payload)


def test_projection_rejects_recomputed_digest_that_removes_hard_safety_metadata() -> None:
    stack = RecipeStackInput(
        explicitRecipeRefs=("recipe.explicit.base",),
        hardSafetyRefs=("recipe.safety.hard",),
        turnId="turn-1",
        sessionId="session-1",
    )
    contract = build_effective_recipe_contract(
        stack=stack,
        admitted_snapshots=(
            _snapshot("recipe.explicit.base"),
            _snapshot("recipe.safety.hard", hard_safety=True),
        ),
    )
    projection = project_effective_recipe_contract(contract)
    payload = {
        **projection.model_dump(mode="json", by_alias=True),
        "hardSafetyStatus": "not_required",
        "hardSafetyRefCount": 0,
        "hardSafetyIncludedCount": 0,
        "mergeDecisions": tuple(
            decision
            for decision in projection.model_dump(mode="json", by_alias=True)["mergeDecisions"]
            if decision["code"] != "hard_safety.enforced"
        ),
    }
    payload["auditDigest"] = RecipeCompositionProjection.compute_audit_digest(payload)

    with pytest.raises(ValueError, match="builder-minted audit digest"):
        RecipeCompositionProjection.model_validate(payload)


def test_projection_public_projection_rejects_mutated_unminted_audit_digest() -> None:
    stack = RecipeStackInput(
        explicitRecipeRefs=("recipe.explicit.base",),
        hardSafetyRefs=("recipe.safety.hard",),
        turnId="turn-1",
        sessionId="session-1",
    )
    contract = build_effective_recipe_contract(
        stack=stack,
        admitted_snapshots=(
            _snapshot("recipe.explicit.base"),
            _snapshot("recipe.safety.hard", hard_safety=True),
        ),
    )
    projection = project_effective_recipe_contract(contract)
    payload = {
        **projection.model_dump(mode="json", by_alias=True),
        "hardSafetyStatus": "not_required",
        "hardSafetyRefCount": 0,
        "hardSafetyIncludedCount": 0,
        "mergeDecisions": tuple(
            decision
            for decision in projection.model_dump(mode="json", by_alias=True)["mergeDecisions"]
            if decision["code"] != "hard_safety.enforced"
        ),
    }
    projection.__dict__["hard_safety_status"] = "not_required"
    projection.__dict__["hard_safety_ref_count"] = 0
    projection.__dict__["hard_safety_included_count"] = 0
    projection.__dict__["merge_decisions"] = tuple(
        decision
        for decision in projection.merge_decisions
        if decision.code != "hard_safety.enforced"
    )
    projection.__dict__["audit_digest"] = RecipeCompositionProjection.compute_audit_digest(
        payload
    )

    with pytest.raises(ValueError, match="builder-minted audit digest"):
        projection.public_projection()


def test_projection_rejects_forged_effective_contract_authority() -> None:
    contract = build_effective_recipe_contract(
        stack=RecipeStackInput(
            explicitRecipeRefs=("recipe.explicit.base",),
            turnId="turn-1",
            sessionId="session-1",
        ),
        admitted_snapshots=(_snapshot("recipe.explicit.base"),),
    )
    forged = contract.model_copy()
    object.__setattr__(forged, "traffic_attached", True)

    with pytest.raises(ValueError):
        project_effective_recipe_contract(forged)
