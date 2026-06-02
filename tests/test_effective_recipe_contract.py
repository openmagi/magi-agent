from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from magi_agent.recipes.composition import (
    AdmittedRecipeSnapshot,
    RecipeStackInput,
)
from magi_agent.recipes.effective_contract import (
    EffectiveRecipeContract,
    EffectiveRecipeConflict,
    build_effective_recipe_contract,
)
from magi_agent.recipes.hook_composition import (
    HookContribution,
    compose_hook_contributions,
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
    hook_contributions: tuple[str, ...] = (),
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
        "hookContributions": hook_contributions,
        "retryPolicy": retry_policy,
        "projectionRules": projection_rules,
    }
    payload["snapshotDigest"] = AdmittedRecipeSnapshot.compute_snapshot_digest(payload)
    return AdmittedRecipeSnapshot._from_registry_snapshot(payload)


def _hook_contribution(
    recipe_ref: str,
    hook_id: str,
    *,
    priority: int = 100,
    blocking: bool = False,
    failure_mode: str = "fail_open",
    idempotency_key: str | None = None,
    side_effectful: bool = False,
) -> HookContribution:
    payload: dict[str, object] = {
        "recipeRef": recipe_ref,
        "hookId": hook_id,
        "stage": "beforeToolUse",
        "priority": priority,
        "scope": ("all",),
        "idempotencyKey": idempotency_key,
        "blocking": blocking,
        "failureMode": failure_mode,
        "sideEffectful": side_effectful,
        "securityCritical": blocking,
        "privateConfig": {"token": "sk-proj-private-value"},
    }
    payload["contributionDigest"] = HookContribution.compute_contribution_digest(payload)
    return HookContribution._from_registry_contribution(payload)


def test_explicit_recipe_must_appear_or_contract_blocks() -> None:
    stack = RecipeStackInput(
        explicitRecipeRefs=("recipe.explicit",),
        autoRecipeRefs=("recipe.auto",),
        allowAdditionalAutoRecipes=True,
        turnId="turn-1",
        sessionId="session-1",
    )

    contract = build_effective_recipe_contract(
        stack=stack,
        admitted_snapshots=(_snapshot("recipe.auto", tool_grants=("tool.web.fetch",)),),
    )

    assert contract.blocked is True
    assert contract.effective_recipe_refs == ()
    assert contract.included_explicit_refs == ()
    assert contract.included_auto_refs == ()
    assert contract.excluded_refs[0].recipe_ref == "recipe.explicit"
    assert contract.excluded_refs[0].reason == "explicit_recipe_missing"
    assert contract.conflicts[0].code == "explicit_recipe_missing"


def test_blocked_contract_does_not_carry_hooks() -> None:
    stack = RecipeStackInput(
        explicitRecipeRefs=("recipe.explicit",),
        turnId="turn-1",
        sessionId="session-1",
    )
    hook_contract = compose_hook_contributions(
        (
            _hook_contribution(
                "recipe.auto",
                "hook.side_effect",
                idempotency_key="hook.side_effect:key",
            ),
        )
    )

    contract = build_effective_recipe_contract(
        stack=stack,
        admitted_snapshots=(),
        hook_contract=hook_contract,
    )

    assert contract.blocked is True
    assert contract.effective_hooks is None
    assert contract.public_projection()["effectiveHooks"] is None


def test_auto_recipe_conflict_excludes_or_blocks_according_to_policy() -> None:
    explicit = _snapshot("recipe.explicit", tool_grants=("tool.web.fetch",))
    auto = _snapshot("recipe.auto", tool_denials=("tool.web.fetch",))
    stack = RecipeStackInput(
        explicitRecipeRefs=("recipe.explicit",),
        autoRecipeRefs=("recipe.auto",),
        allowAdditionalAutoRecipes=True,
        turnId="turn-1",
        sessionId="session-1",
    )

    excluded = build_effective_recipe_contract(
        stack=stack,
        admitted_snapshots=(explicit, auto),
        auto_conflict_policy="exclude",
    )
    blocked = build_effective_recipe_contract(
        stack=stack,
        admitted_snapshots=(explicit, auto),
        auto_conflict_policy="block",
    )

    assert excluded.blocked is False
    assert excluded.effective_recipe_refs == ("recipe.explicit",)
    assert excluded.included_auto_refs == ()
    assert excluded.excluded_refs[0].recipe_ref == "recipe.auto"
    assert excluded.excluded_refs[0].reason == "auto_recipe_incompatible"
    assert excluded.effective_tool_grants == ("tool.web.fetch",)
    assert excluded.effective_tool_denials == ()
    assert excluded.conflicts[0].code == "auto_recipe_incompatible"
    assert excluded.conflicts[0].blocking is False

    assert blocked.blocked is True
    assert blocked.effective_recipe_refs == ()
    assert blocked.conflicts[0].code == "auto_recipe_incompatible"
    assert blocked.conflicts[0].blocking is True


def test_auto_recipe_hook_conflict_excludes_auto_when_policy_allows() -> None:
    explicit = _snapshot(
        "recipe.explicit",
        hook_contributions=("hook.policy",),
    )
    auto = _snapshot(
        "recipe.auto",
        hook_contributions=("hook.policy",),
    )
    stack = RecipeStackInput(
        explicitRecipeRefs=("recipe.explicit",),
        autoRecipeRefs=("recipe.auto",),
        allowAdditionalAutoRecipes=True,
        turnId="turn-1",
        sessionId="session-1",
    )

    contract = build_effective_recipe_contract(
        stack=stack,
        admitted_snapshots=(explicit, auto),
        hook_contributions=(
            _hook_contribution(
                "recipe.explicit",
                "hook.policy",
                priority=10,
                blocking=True,
                failure_mode="fail_closed",
            ),
            _hook_contribution(
                "recipe.auto",
                "hook.policy",
                priority=20,
                blocking=True,
                failure_mode="fail_closed",
            ),
        ),
        auto_conflict_policy="exclude",
    )
    conflict_codes = {conflict.code for conflict in contract.conflicts}

    assert contract.blocked is False
    assert contract.effective_recipe_refs == ("recipe.explicit",)
    assert contract.included_auto_refs == ()
    assert contract.excluded_refs[0].recipe_ref == "recipe.auto"
    assert contract.excluded_refs[0].reason == "auto_recipe_incompatible"
    assert contract.effective_hooks is not None
    assert tuple(hook.recipe_refs for hook in contract.effective_hooks.hooks) == (
        ("recipe.explicit",),
    )
    assert "auto_recipe_incompatible" in conflict_codes
    assert "non_idempotent_hook_duplicate" in conflict_codes
    assert all(conflict.blocking is False for conflict in contract.conflicts)


def test_auto_recipe_hook_conflict_blocks_when_policy_requires() -> None:
    explicit = _snapshot(
        "recipe.explicit",
        hook_contributions=("hook.policy",),
    )
    auto = _snapshot(
        "recipe.auto",
        hook_contributions=("hook.policy",),
    )
    stack = RecipeStackInput(
        explicitRecipeRefs=("recipe.explicit",),
        autoRecipeRefs=("recipe.auto",),
        allowAdditionalAutoRecipes=True,
        turnId="turn-1",
        sessionId="session-1",
    )

    contract = build_effective_recipe_contract(
        stack=stack,
        admitted_snapshots=(explicit, auto),
        hook_contributions=(
            _hook_contribution(
                "recipe.explicit",
                "hook.policy",
                priority=10,
                blocking=True,
                failure_mode="fail_closed",
            ),
            _hook_contribution(
                "recipe.auto",
                "hook.policy",
                priority=20,
                blocking=True,
                failure_mode="fail_closed",
            ),
        ),
        auto_conflict_policy="block",
    )
    conflict_codes = {conflict.code for conflict in contract.conflicts}

    assert contract.blocked is True
    assert contract.effective_recipe_refs == ()
    assert contract.effective_hooks is None
    assert "auto_recipe_incompatible" in conflict_codes
    assert "non_idempotent_hook_duplicate" in conflict_codes
    assert all(conflict.blocking is True for conflict in contract.conflicts)


def test_precomposed_auto_hook_contract_requires_recipe_scoped_contributions() -> None:
    explicit = _snapshot(
        "recipe.explicit",
        hook_contributions=("hook.policy",),
    )
    auto = _snapshot(
        "recipe.auto",
        hook_contributions=("hook.policy",),
    )
    hook_contract = compose_hook_contributions(
        (
            _hook_contribution(
                "recipe.explicit",
                "hook.policy",
                priority=10,
                blocking=True,
                failure_mode="fail_closed",
            ),
            _hook_contribution(
                "recipe.auto",
                "hook.policy",
                priority=20,
                blocking=True,
                failure_mode="fail_closed",
            ),
        )
    )
    stack = RecipeStackInput(
        explicitRecipeRefs=("recipe.explicit",),
        autoRecipeRefs=("recipe.auto",),
        allowAdditionalAutoRecipes=True,
        turnId="turn-1",
        sessionId="session-1",
    )

    contract = build_effective_recipe_contract(
        stack=stack,
        admitted_snapshots=(explicit, auto),
        hook_contract=hook_contract,
        auto_conflict_policy="exclude",
    )
    conflict_codes = {conflict.code for conflict in contract.conflicts}

    assert contract.blocked is True
    assert contract.effective_recipe_refs == ()
    assert contract.effective_hooks is None
    assert "recipe_scoped_hook_contributions_required" in conflict_codes
    assert "non_idempotent_hook_duplicate" in conflict_codes


def test_precomposed_hook_contract_fails_closed_when_auto_declares_uncompiled_hook() -> None:
    explicit = _snapshot(
        "recipe.explicit",
        hook_contributions=("hook.explicit",),
    )
    auto = _snapshot(
        "recipe.auto",
        hook_contributions=("hook.auto",),
    )
    hook_contract = compose_hook_contributions(
        (
            _hook_contribution(
                "recipe.explicit",
                "hook.explicit",
                idempotency_key="hook.explicit:key",
            ),
        )
    )
    stack = RecipeStackInput(
        explicitRecipeRefs=("recipe.explicit",),
        autoRecipeRefs=("recipe.auto",),
        allowAdditionalAutoRecipes=True,
        turnId="turn-1",
        sessionId="session-1",
    )

    contract = build_effective_recipe_contract(
        stack=stack,
        admitted_snapshots=(explicit, auto),
        hook_contract=hook_contract,
        auto_conflict_policy="exclude",
    )
    conflict_codes = {conflict.code for conflict in contract.conflicts}

    assert contract.blocked is True
    assert contract.effective_recipe_refs == ()
    assert contract.effective_hooks is None
    assert contract.included_auto_refs == ()
    assert "recipe_scoped_hook_contributions_required" in conflict_codes


def test_missing_hook_contract_fails_closed_when_mandatory_recipe_declares_hook() -> None:
    explicit = _snapshot(
        "recipe.explicit",
        hook_contributions=("hook.explicit",),
    )
    stack = RecipeStackInput(
        explicitRecipeRefs=("recipe.explicit",),
        turnId="turn-1",
        sessionId="session-1",
    )

    contract = build_effective_recipe_contract(
        stack=stack,
        admitted_snapshots=(explicit,),
    )
    conflict_codes = {conflict.code for conflict in contract.conflicts}

    assert contract.blocked is True
    assert contract.effective_recipe_refs == ()
    assert contract.effective_hooks is None
    assert "declared_hook_contribution_missing" in conflict_codes


def test_missing_hook_contract_fails_closed_when_auto_recipe_declares_hook() -> None:
    explicit = _snapshot("recipe.explicit")
    auto = _snapshot(
        "recipe.auto",
        hook_contributions=("hook.auto",),
    )
    stack = RecipeStackInput(
        explicitRecipeRefs=("recipe.explicit",),
        autoRecipeRefs=("recipe.auto",),
        allowAdditionalAutoRecipes=True,
        turnId="turn-1",
        sessionId="session-1",
    )

    contract = build_effective_recipe_contract(
        stack=stack,
        admitted_snapshots=(explicit, auto),
        auto_conflict_policy="exclude",
    )
    conflict_codes = {conflict.code for conflict in contract.conflicts}

    assert contract.blocked is True
    assert contract.effective_recipe_refs == ()
    assert contract.effective_hooks is None
    assert "recipe_scoped_hook_contributions_required" in conflict_codes


def test_raw_hook_contributions_exclude_auto_when_declared_auto_hook_is_missing() -> None:
    explicit = _snapshot(
        "recipe.explicit",
        hook_contributions=("hook.explicit",),
    )
    auto = _snapshot(
        "recipe.auto",
        hook_contributions=("hook.auto",),
    )
    stack = RecipeStackInput(
        explicitRecipeRefs=("recipe.explicit",),
        autoRecipeRefs=("recipe.auto",),
        allowAdditionalAutoRecipes=True,
        turnId="turn-1",
        sessionId="session-1",
    )

    contract = build_effective_recipe_contract(
        stack=stack,
        admitted_snapshots=(explicit, auto),
        hook_contributions=(
            _hook_contribution(
                "recipe.explicit",
                "hook.explicit",
                idempotency_key="hook.explicit:key",
            ),
        ),
        auto_conflict_policy="exclude",
    )
    conflict_codes = {conflict.code for conflict in contract.conflicts}

    assert contract.blocked is False
    assert contract.effective_recipe_refs == ("recipe.explicit",)
    assert contract.included_auto_refs == ()
    assert contract.excluded_refs[0].recipe_ref == "recipe.auto"
    assert contract.excluded_refs[0].reason == "auto_recipe_incompatible"
    assert contract.effective_hooks is not None
    assert "declared_hook_contribution_missing" in conflict_codes
    assert any(
        conflict.code == "declared_hook_contribution_missing"
        and conflict.blocking is False
        for conflict in contract.conflicts
    )


def test_raw_hook_contributions_block_when_declared_auto_hook_missing_and_policy_blocks() -> None:
    explicit = _snapshot(
        "recipe.explicit",
        hook_contributions=("hook.explicit",),
    )
    auto = _snapshot(
        "recipe.auto",
        hook_contributions=("hook.auto",),
    )
    stack = RecipeStackInput(
        explicitRecipeRefs=("recipe.explicit",),
        autoRecipeRefs=("recipe.auto",),
        allowAdditionalAutoRecipes=True,
        turnId="turn-1",
        sessionId="session-1",
    )

    contract = build_effective_recipe_contract(
        stack=stack,
        admitted_snapshots=(explicit, auto),
        hook_contributions=(
            _hook_contribution(
                "recipe.explicit",
                "hook.explicit",
                idempotency_key="hook.explicit:key",
            ),
        ),
        auto_conflict_policy="block",
    )
    conflict_codes = {conflict.code for conflict in contract.conflicts}

    assert contract.blocked is True
    assert contract.effective_recipe_refs == ()
    assert contract.effective_hooks is None
    assert "auto_recipe_incompatible" in conflict_codes
    assert "declared_hook_contribution_missing" in conflict_codes


def test_hook_contract_must_be_scoped_to_effective_recipes() -> None:
    stack = RecipeStackInput(
        explicitRecipeRefs=("recipe.explicit",),
        turnId="turn-1",
        sessionId="session-1",
    )
    hook_contract = compose_hook_contributions(
        (
            _hook_contribution(
                "recipe.unrelated",
                "hook.unrelated",
                idempotency_key="hook.unrelated:key",
            ),
        )
    )

    contract = build_effective_recipe_contract(
        stack=stack,
        admitted_snapshots=(_snapshot("recipe.explicit"),),
        hook_contract=hook_contract,
    )

    assert contract.blocked is True
    assert contract.effective_hooks is None
    assert contract.conflicts[0].code == "hook_recipe_scope_violation"
    assert contract.conflicts[0].blocking is True


def test_hook_contract_must_be_declared_by_effective_snapshot() -> None:
    stack = RecipeStackInput(
        explicitRecipeRefs=("recipe.alpha",),
        turnId="turn-1",
        sessionId="session-1",
    )
    hook_contract = compose_hook_contributions(
        (
            _hook_contribution(
                "recipe.alpha",
                "hook.undeclared",
                idempotency_key="hook.undeclared:key",
            ),
        )
    )

    contract = build_effective_recipe_contract(
        stack=stack,
        admitted_snapshots=(_snapshot("recipe.alpha", hook_contributions=()),),
        hook_contract=hook_contract,
    )

    assert contract.blocked is True
    assert contract.effective_hooks is None
    assert contract.conflicts[0].code == "hook_contribution_not_declared"
    assert contract.conflicts[0].blocking is True


def test_required_governed_recipe_refs_block_ungoverned_fallback() -> None:
    stack = RecipeStackInput(
        explicitRecipeRefs=("recipe.governed",),
        turnId="turn-1",
        sessionId="session-1",
    )
    ungoverned = _snapshot("recipe.governed", governed=False)

    contract = build_effective_recipe_contract(
        stack=stack,
        admitted_snapshots=(ungoverned,),
        required_governed_recipe_refs=("recipe.governed",),
    )

    assert contract.blocked is True
    assert contract.effective_recipe_refs == ()
    assert contract.conflicts[0].code == "required_governed_recipe_resolved_ungoverned"


def test_effective_digest_is_stable() -> None:
    alpha = _snapshot(
        "recipe.alpha",
        tool_grants=("tool.file.read", "tool.web.fetch"),
        approval_requirements=("approval.owner",),
        evidence_requirements=("evidence.claim:required",),
        context_requirements=("context.workspace:refs_only",),
        retry_policy="retry:max:2:repair:1",
    )
    beta = _snapshot(
        "recipe.beta",
        tool_denials=("tool.shell.run",),
        approval_requirements=("approval.operator",),
        evidence_requirements=("evidence.claim:blocking",),
        context_requirements=("context.workspace:summary",),
        retry_policy="retry:max:3",
    )
    stack = RecipeStackInput(
        explicitRecipeRefs=("recipe.alpha", "recipe.beta"),
        turnId="turn-1",
        sessionId="session-1",
    )

    first = build_effective_recipe_contract(stack=stack, admitted_snapshots=(alpha, beta))
    second = build_effective_recipe_contract(stack=stack, admitted_snapshots=(beta, alpha))

    assert first.effective_digest == second.effective_digest
    assert first.public_projection() == second.public_projection()
    assert first.effective_tool_grants == ("tool.file.read", "tool.web.fetch")
    assert first.effective_tool_denials == ("tool.shell.run",)
    assert first.effective_approval_requirements == (
        "approval.operator",
        "approval.owner",
    )
    assert first.effective_evidence_requirements == ("evidence.claim:blocking",)
    assert first.effective_context_policy == ("context.workspace:refs_only",)
    assert first.effective_retry_policy.max_attempts == 2


def test_public_projection_is_safe_and_includes_hook_contract_digest() -> None:
    snapshot = _snapshot(
        "recipe.alpha",
        tool_grants=("tool.file.read",),
        tool_denials=("tool.shell.run",),
        evidence_requirements=("evidence.claim:blocking",),
        approval_requirements=("approval.operator",),
        context_requirements=("context.workspace:refs_only",),
        hook_contributions=("hook.private",),
    )
    hook_contract = compose_hook_contributions(
        (
            _hook_contribution(
                "recipe.alpha",
                "hook.private",
                idempotency_key="hook.redacted.id",
            ),
        )
    )
    stack = RecipeStackInput(
        explicitRecipeRefs=("recipe.alpha",),
        turnId="turn-1",
        sessionId="session-1",
    )

    contract = build_effective_recipe_contract(
        stack=stack,
        admitted_snapshots=(snapshot,),
        hook_contract=hook_contract,
    )
    projection = contract.public_projection()
    dumped = contract.model_dump(by_alias=True, mode="json")
    dumped_json = contract.model_dump_json(by_alias=True)
    serialized = json.dumps(projection, sort_keys=True) + json.dumps(dumped) + dumped_json

    assert projection["effectiveDigest"] == contract.effective_digest
    assert projection["effectiveHooks"]["compositionDigest"] == hook_contract.composition_digest
    assert projection["toolGrantCount"] == 1
    assert projection["toolDenialCount"] == 1
    assert "tool.file.read" not in serialized
    assert "tool.shell.run" not in serialized
    assert "evidence.claim" not in serialized
    assert "context.workspace" not in serialized
    assert "approval.operator" not in serialized
    assert "sk-proj-private-value" not in serialized


def test_effective_conflict_direct_projection_uses_subject_digest() -> None:
    conflict = EffectiveRecipeConflict(
        code="tool_denied_grant",
        subjectRef="tool.web.fetch",
        recipeRefs=("recipe.alpha", "recipe.beta"),
        blocking=True,
    )

    serialized = (
        json.dumps(conflict.public_projection(), sort_keys=True)
        + json.dumps(conflict.model_dump(by_alias=True, mode="json"))
        + conflict.model_dump_json(by_alias=True)
    )

    assert "subjectDigest" in serialized
    assert "tool.web.fetch" not in serialized
    assert "recipe.alpha" not in serialized
    assert "recipe.beta" not in serialized


def test_blocked_contract_validation_rejects_any_activation_material() -> None:
    contract = build_effective_recipe_contract(
        stack=RecipeStackInput(
            explicitRecipeRefs=("recipe.alpha",),
            turnId="turn-1",
            sessionId="session-1",
        ),
        admitted_snapshots=(_snapshot("recipe.alpha"),),
    )

    with pytest.raises(
        ValidationError,
        match="blocked effective contract cannot carry activation material",
    ):
        EffectiveRecipeContract.model_validate(
            {
                "schemaVersion": contract.schema_version,
                "effectiveRecipeRefs": (),
                "includedExplicitRefs": (),
                "includedAutoRefs": (),
                "excludedRefs": contract.excluded_refs,
                "effectiveToolGrants": (),
                "effectiveToolDenials": ("tool.shell.run",),
                "effectiveEvidenceRequirements": (),
                "effectiveApprovalRequirements": (),
                "effectiveContextPolicy": (),
                "effectiveHooks": None,
                "effectiveRetryPolicy": contract.effective_retry_policy,
                "conflicts": contract.conflicts,
                "blocked": True,
                "defaultOff": True,
                "trafficAttached": False,
                "executionAttached": False,
                "liveActivation": False,
                "effectiveDigest": contract.effective_digest,
            }
        )

    with pytest.raises(
        ValidationError,
        match="blocked effective contract cannot carry activation material",
    ):
        EffectiveRecipeContract.model_validate(
            {
                "schemaVersion": contract.schema_version,
                "effectiveRecipeRefs": (),
                "includedExplicitRefs": (),
                "includedAutoRefs": (),
                "excludedRefs": contract.excluded_refs,
                "effectiveToolGrants": (),
                "effectiveToolDenials": (),
                "effectiveEvidenceRequirements": (),
                "effectiveApprovalRequirements": (),
                "effectiveContextPolicy": (),
                "effectiveHooks": None,
                "effectiveRetryPolicy": {
                    "maxAttempts": 1,
                    "repairAttempts": 0,
                    "globalCap": 3,
                },
                "conflicts": contract.conflicts,
                "blocked": True,
                "defaultOff": True,
                "trafficAttached": False,
                "executionAttached": False,
                "liveActivation": False,
                "effectiveDigest": contract.effective_digest,
            }
        )


def test_no_live_attachment_flags_can_be_set_true() -> None:
    contract = build_effective_recipe_contract(
        stack=RecipeStackInput(
            explicitRecipeRefs=("recipe.alpha",),
            turnId="turn-1",
            sessionId="session-1",
        ),
        admitted_snapshots=(_snapshot("recipe.alpha"),),
    )

    assert contract.default_off is True
    assert contract.traffic_attached is False
    assert contract.execution_attached is False
    assert contract.live_activation is False

    with pytest.raises(ValidationError):
        EffectiveRecipeContract.model_validate(
            {
                "schemaVersion": contract.schema_version,
                "effectiveRecipeRefs": contract.effective_recipe_refs,
                "includedExplicitRefs": contract.included_explicit_refs,
                "includedAutoRefs": contract.included_auto_refs,
                "excludedRefs": contract.excluded_refs,
                "effectiveToolGrants": contract.effective_tool_grants,
                "effectiveToolDenials": contract.effective_tool_denials,
                "effectiveEvidenceRequirements": contract.effective_evidence_requirements,
                "effectiveApprovalRequirements": contract.effective_approval_requirements,
                "effectiveContextPolicy": contract.effective_context_policy,
                "effectiveHooks": contract.effective_hooks,
                "effectiveRetryPolicy": contract.effective_retry_policy,
                "conflicts": contract.conflicts,
                "blocked": contract.blocked,
                "defaultOff": True,
                "trafficAttached": True,
                "executionAttached": False,
                "liveActivation": False,
                "effectiveDigest": contract.effective_digest,
            }
        )

    with pytest.raises(
        ValueError,
        match="effective recipe contract authority fields are immutable",
    ):
        contract.model_copy(update={"liveActivation": True})


def test_effective_contract_model_construct_canonicalizes_live_authority_flags() -> None:
    contract = build_effective_recipe_contract(
        stack=RecipeStackInput(
            explicitRecipeRefs=("recipe.alpha",),
            turnId="turn-1",
            sessionId="session-1",
        ),
        admitted_snapshots=(_snapshot("recipe.alpha"),),
    )

    constructed = EffectiveRecipeContract.model_construct(
        schema_version=contract.schema_version,
        effective_recipe_refs=contract.effective_recipe_refs,
        effective_hard_safety_refs=contract.effective_hard_safety_refs,
        included_explicit_refs=contract.included_explicit_refs,
        included_auto_refs=contract.included_auto_refs,
        excluded_refs=contract.excluded_refs,
        effective_tool_grants=contract.effective_tool_grants,
        effective_tool_denials=contract.effective_tool_denials,
        effective_evidence_requirements=contract.effective_evidence_requirements,
        effective_approval_requirements=contract.effective_approval_requirements,
        effective_context_policy=contract.effective_context_policy,
        effective_hooks=contract.effective_hooks,
        effective_retry_policy=contract.effective_retry_policy,
        conflicts=contract.conflicts,
        blocked=contract.blocked,
        default_off=False,
        traffic_attached=True,
        execution_attached=True,
        live_activation=True,
        effective_digest=contract.effective_digest,
    )

    assert constructed.default_off is True
    assert constructed.traffic_attached is False
    assert constructed.execution_attached is False
    assert constructed.live_activation is False
    assert constructed.public_projection()["trafficAttached"] is False


def test_effective_contract_model_construct_rejects_digest_mismatch() -> None:
    contract = build_effective_recipe_contract(
        stack=RecipeStackInput(
            explicitRecipeRefs=("recipe.alpha",),
            turnId="turn-1",
            sessionId="session-1",
        ),
        admitted_snapshots=(_snapshot("recipe.alpha"),),
    )

    with pytest.raises(ValidationError, match="effective digest mismatch"):
        EffectiveRecipeContract.model_construct(
            schema_version=contract.schema_version,
            effective_recipe_refs=contract.effective_recipe_refs,
            effective_hard_safety_refs=contract.effective_hard_safety_refs,
            included_explicit_refs=contract.included_explicit_refs,
            included_auto_refs=contract.included_auto_refs,
            excluded_refs=contract.excluded_refs,
            effective_tool_grants=contract.effective_tool_grants,
            effective_tool_denials=contract.effective_tool_denials,
            effective_evidence_requirements=contract.effective_evidence_requirements,
            effective_approval_requirements=contract.effective_approval_requirements,
            effective_context_policy=contract.effective_context_policy,
            effective_hooks=contract.effective_hooks,
            effective_retry_policy=contract.effective_retry_policy,
            conflicts=contract.conflicts,
            blocked=contract.blocked,
            default_off=True,
            traffic_attached=False,
            execution_attached=False,
            live_activation=False,
            effective_digest="sha256:" + "1" * 64,
        )


def test_effective_contract_model_construct_rejects_blocked_activation_material() -> None:
    contract = build_effective_recipe_contract(
        stack=RecipeStackInput(
            explicitRecipeRefs=("recipe.alpha",),
            turnId="turn-1",
            sessionId="session-1",
        ),
        admitted_snapshots=(_snapshot("recipe.alpha"),),
    )

    with pytest.raises(
        ValidationError,
        match="blocked effective contract cannot carry activation material",
    ):
        EffectiveRecipeContract.model_construct(
            schema_version=contract.schema_version,
            effective_recipe_refs=contract.effective_recipe_refs,
            effective_hard_safety_refs=contract.effective_hard_safety_refs,
            included_explicit_refs=contract.included_explicit_refs,
            included_auto_refs=contract.included_auto_refs,
            excluded_refs=contract.excluded_refs,
            effective_tool_grants=contract.effective_tool_grants,
            effective_tool_denials=contract.effective_tool_denials,
            effective_evidence_requirements=contract.effective_evidence_requirements,
            effective_approval_requirements=contract.effective_approval_requirements,
            effective_context_policy=contract.effective_context_policy,
            effective_hooks=contract.effective_hooks,
            effective_retry_policy=contract.effective_retry_policy,
            conflicts=contract.conflicts,
            blocked=True,
            default_off=True,
            traffic_attached=False,
            execution_attached=False,
            live_activation=False,
            effective_digest=contract.effective_digest,
        )
