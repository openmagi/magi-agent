from __future__ import annotations

from openmagi_core_agent.recipes.composition import (
    AdmittedRecipeSnapshot,
    RecipeStackInput,
)
from openmagi_core_agent.recipes.effective_contract import (
    build_effective_recipe_contract,
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


def test_explicit_recipe_selected_and_admitted_is_included() -> None:
    stack = RecipeStackInput(
        explicitRecipeRefs=("recipe.explicit.included",),
        turnId="turn-1",
        sessionId="session-1",
    )

    contract = build_effective_recipe_contract(
        stack=stack,
        admitted_snapshots=(
            _snapshot(
                "recipe.explicit.included",
                approval_requirements=("approval.owner",),
            ),
        ),
    )

    assert contract.blocked is False
    assert contract.effective_recipe_refs == ("recipe.explicit.included",)
    assert contract.included_explicit_refs == ("recipe.explicit.included",)
    assert contract.included_auto_refs == ()
    assert contract.effective_approval_requirements == ("approval.owner",)


def test_explicit_recipe_selected_but_missing_admission_blocks() -> None:
    stack = RecipeStackInput(
        explicitRecipeRefs=("recipe.explicit.missing",),
        turnId="turn-1",
        sessionId="session-1",
    )

    contract = build_effective_recipe_contract(
        stack=stack,
        admitted_snapshots=(),
    )

    assert contract.blocked is True
    assert contract.effective_recipe_refs == ()
    assert contract.included_explicit_refs == ()
    assert contract.excluded_refs[0].recipe_ref == "recipe.explicit.missing"
    assert contract.excluded_refs[0].reason == "explicit_recipe_missing"
    assert contract.conflicts[0].code == "explicit_recipe_missing"


def test_explicit_recipe_conflicting_with_hard_safety_blocks() -> None:
    stack = RecipeStackInput(
        explicitRecipeRefs=("recipe.explicit.granting",),
        hardSafetyRefs=("recipe.safety.hard",),
        turnId="turn-1",
        sessionId="session-1",
    )

    contract = build_effective_recipe_contract(
        stack=stack,
        admitted_snapshots=(
            _snapshot("recipe.explicit.granting", tool_grants=("tool.shell.run",)),
            _snapshot(
                "recipe.safety.hard",
                hard_safety=True,
                tool_denials=("tool.shell.run",),
                projection_rules=("hardSafety.mode:enforce",),
            ),
        ),
    )

    assert contract.blocked is True
    assert contract.effective_recipe_refs == ()
    assert contract.included_explicit_refs == ()
    assert contract.conflicts[0].code == "tool_denied_grant"
    assert contract.conflicts[0].blocking is True


def test_explicit_recipe_plus_compatible_auto_recipe_succeeds() -> None:
    stack = RecipeStackInput(
        explicitRecipeRefs=("recipe.explicit.base",),
        autoRecipeRefs=("recipe.auto.compatible",),
        allowAdditionalAutoRecipes=True,
        turnId="turn-1",
        sessionId="session-1",
    )

    contract = build_effective_recipe_contract(
        stack=stack,
        admitted_snapshots=(
            _snapshot("recipe.explicit.base", tool_grants=("tool.file.read",)),
            _snapshot("recipe.auto.compatible", tool_grants=("tool.web.fetch",)),
        ),
    )

    assert contract.blocked is False
    assert contract.effective_recipe_refs == (
        "recipe.auto.compatible",
        "recipe.explicit.base",
    )
    assert contract.included_explicit_refs == ("recipe.explicit.base",)
    assert contract.included_auto_refs == ("recipe.auto.compatible",)
    assert contract.effective_tool_grants == ("tool.file.read", "tool.web.fetch")


def test_explicit_recipe_keeps_or_blocks_incompatible_auto_by_policy() -> None:
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

    excluded = build_effective_recipe_contract(
        stack=stack,
        admitted_snapshots=(explicit, incompatible_auto),
        auto_conflict_policy="exclude",
    )
    blocked = build_effective_recipe_contract(
        stack=stack,
        admitted_snapshots=(explicit, incompatible_auto),
        auto_conflict_policy="block",
    )

    assert excluded.blocked is False
    assert excluded.effective_recipe_refs == ("recipe.explicit.base",)
    assert excluded.included_explicit_refs == ("recipe.explicit.base",)
    assert excluded.included_auto_refs == ()
    assert excluded.excluded_refs[0].recipe_ref == "recipe.auto.incompatible"
    assert excluded.excluded_refs[0].reason == "auto_recipe_incompatible"
    assert excluded.conflicts[0].blocking is False

    assert blocked.blocked is True
    assert blocked.effective_recipe_refs == ()
    assert blocked.included_explicit_refs == ()
    assert blocked.conflicts[0].code == "auto_recipe_incompatible"
    assert blocked.conflicts[0].blocking is True


def test_governed_fixture_does_not_fall_back_to_general_chat_recipe() -> None:
    stack = RecipeStackInput(
        explicitRecipeRefs=("recipe.governed.required",),
        autoRecipeRefs=("recipe.fallback.general_chat",),
        allowAdditionalAutoRecipes=True,
        turnId="turn-1",
        sessionId="session-1",
    )

    contract = build_effective_recipe_contract(
        stack=stack,
        admitted_snapshots=(
            _snapshot("recipe.fallback.general_chat", governed=False),
        ),
        required_governed_recipe_refs=("recipe.governed.required",),
    )
    conflict_codes = {conflict.code for conflict in contract.conflicts}

    assert contract.blocked is True
    assert contract.effective_recipe_refs == ()
    assert contract.included_auto_refs == ()
    assert "explicit_recipe_missing" in conflict_codes
    assert "required_governed_recipe_missing" in conflict_codes


def test_governed_fixture_blocks_ungoverned_general_chat_auto_resolution() -> None:
    stack = RecipeStackInput(
        explicitRecipeRefs=("recipe.governed.required",),
        autoRecipeRefs=("recipe.fallback.general_chat",),
        allowAdditionalAutoRecipes=True,
        selectionSource="selector.fixture",
        turnId="turn-1",
        sessionId="session-1",
    )

    contract = build_effective_recipe_contract(
        stack=stack,
        admitted_snapshots=(
            _snapshot("recipe.governed.required", governed=True),
            _snapshot("recipe.fallback.general_chat", governed=False),
        ),
        required_governed_recipe_refs=(
            "recipe.governed.required",
            "recipe.fallback.general_chat",
        ),
    )
    conflict_codes = {conflict.code for conflict in contract.conflicts}

    assert contract.blocked is True
    assert contract.effective_recipe_refs == ()
    assert contract.included_explicit_refs == ()
    assert contract.included_auto_refs == ()
    assert "explicit_recipe_missing" not in conflict_codes
    assert "required_governed_recipe_resolved_ungoverned" in conflict_codes


def test_governed_fixture_blocks_ungoverned_auto_fallback_even_when_required_ref_is_present() -> None:
    stack = RecipeStackInput(
        explicitRecipeRefs=("recipe.governed.required",),
        autoRecipeRefs=("recipe.fallback.general_chat",),
        allowAdditionalAutoRecipes=True,
        selectionSource="selector.fixture",
        turnId="turn-1",
        sessionId="session-1",
    )

    contract = build_effective_recipe_contract(
        stack=stack,
        admitted_snapshots=(
            _snapshot("recipe.governed.required", governed=True),
            _snapshot("recipe.fallback.general_chat", governed=False),
        ),
        required_governed_recipe_refs=("recipe.governed.required",),
    )
    conflict_codes = {conflict.code for conflict in contract.conflicts}

    assert contract.blocked is True
    assert contract.effective_recipe_refs == ()
    assert contract.included_explicit_refs == ()
    assert contract.included_auto_refs == ()
    assert contract.excluded_refs[0].recipe_ref == "recipe.fallback.general_chat"
    assert contract.excluded_refs[0].reason == "auto_recipe_ungoverned_for_required_governance"
    assert "explicit_recipe_missing" not in conflict_codes
    assert "auto_recipe_ungoverned_for_required_governance" in conflict_codes
