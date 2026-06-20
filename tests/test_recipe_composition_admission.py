from __future__ import annotations

import json

import pytest
from pydantic import BaseModel, ValidationError

from magi_agent.recipes.composition import (
    AdmittedRecipeSnapshot,
    RecipeAdmissionRequest,
    RecipeAdmissionResult,
    RecipeStackInput,
    admit_recipe_stack,
)


# Module-level subclass for forge-rejection tests. Defining this inside a test
# function body trips pydantic 2.13 + `from __future__ import annotations`:
# function-local namespaces can't resolve inherited deferred annotations
# (e.g. `Any`) from `FalseOnlyAuthorityModel`, raising
# `PydanticUserError: ForgedStack is not fully defined`. Module namespace
# resolves them, sidestepping the trip while preserving test intent
# (the admission boundary must reject any non-RecipeStackInput-exact instance).
class ForgedStack(RecipeStackInput):
    pass


def _secret_fixture(*parts: str) -> str:
    return "".join(parts)


DUMMY_SECRET_SUFFIX = _secret_fixture("12345678", "90abcdef")
DUMMY_SK_PROJ = _secret_fixture("sk-", "proj-", DUMMY_SECRET_SUFFIX)


def _snapshot(
    recipe_ref: str,
    *,
    governed: bool = True,
    hard_safety: bool = False,
    tool_grants: tuple[str, ...] = (),
    raw_prompt: str | None = None,
    private_config: str | None = None,
    registry_admitted: bool = True,
) -> AdmittedRecipeSnapshot:
    payload: dict[str, object] = {
        "recipeRef": recipe_ref,
        "version": "v1",
        "source": "fixture",
        "governed": governed,
        "hardSafety": hard_safety,
        "toolGrants": tool_grants,
        "toolDenials": (),
        "evidenceRequirements": ("evidence.public",),
        "approvalRequirements": (),
        "contextRequirements": ("context.public",),
        "hookContributions": (),
        "retryPolicy": "none",
        "projectionRules": ("projection.digest_only",),
    }
    if raw_prompt is not None:
        payload["rawPrompt"] = raw_prompt
    if private_config is not None:
        payload["privateConfig"] = private_config
    payload["snapshotDigest"] = AdmittedRecipeSnapshot.compute_snapshot_digest(payload)
    if registry_admitted:
        return AdmittedRecipeSnapshot._from_registry_snapshot(payload)
    return AdmittedRecipeSnapshot(**payload)


def test_explicit_ref_missing_from_admission_fails_closed() -> None:
    stack = RecipeStackInput(
        explicitRecipeRefs=["openmagi.research"],
        autoRecipeRefs=["openmagi.compatible"],
        allowAdditionalAutoRecipes=True,
        turnId="turn-1",
        sessionId="session-1",
    )
    request = RecipeAdmissionRequest(
        stack=stack,
        admittedSnapshots=[_snapshot("openmagi.compatible")],
    )

    result = admit_recipe_stack(request)

    assert result.blocked is True
    assert result.admitted_recipe_refs == ()
    assert result.admitted_snapshots == ()
    assert result.missing_explicit_refs == ("openmagi.research",)
    assert result.conflicts[0].code == "explicit_recipe_missing"
    assert result.conflicts[0].recipe_ref == "openmagi.research"


def test_ungoverned_auto_recipe_cannot_satisfy_governed_selector_fixture() -> None:
    stack = RecipeStackInput(
        autoRecipeRefs=["openmagi.selector-required"],
        allowAdditionalAutoRecipes=True,
        selectionSource="selector.fixture",
        turnId="turn-1",
        sessionId="session-1",
    )
    request = RecipeAdmissionRequest(
        stack=stack,
        admittedSnapshots=[_snapshot("openmagi.selector-required", governed=False)],
        requiredGovernedRecipeRefs=["openmagi.selector-required"],
    )

    result = admit_recipe_stack(request)

    assert result.blocked is True
    assert result.admitted_recipe_refs == ()
    assert result.admitted_snapshots == ()
    assert result.conflicts[0].code == "required_governed_recipe_resolved_ungoverned"
    assert result.public_projection()["conflicts"][0]["code"] == (
        "required_governed_recipe_resolved_ungoverned"
    )


def test_hard_safety_ref_missing_from_admission_fails_closed() -> None:
    stack = RecipeStackInput(
        hardSafetyRefs=["openmagi.safety"],
        turnId="turn-1",
        sessionId="session-1",
    )
    request = RecipeAdmissionRequest(stack=stack, admittedSnapshots=())

    result = admit_recipe_stack(request)

    assert result.blocked is True
    assert result.missing_required_refs == ("openmagi.safety",)
    assert result.conflicts[0].code == "hard_safety_recipe_missing"
    assert result.conflicts[0].recipe_ref == "openmagi.safety"


def test_hard_safety_ref_requires_hard_safety_snapshot() -> None:
    stack = RecipeStackInput(
        hardSafetyRefs=["openmagi.safety"],
        turnId="turn-1",
        sessionId="session-1",
    )
    request = RecipeAdmissionRequest(
        stack=stack,
        admittedSnapshots=[_snapshot("openmagi.safety", hard_safety=False)],
    )

    result = admit_recipe_stack(request)

    assert result.blocked is True
    assert result.admitted_recipe_refs == ()
    assert result.missing_required_refs == ("openmagi.safety",)
    assert result.conflicts[0].code == "hard_safety_recipe_not_hard"


def test_cross_section_duplicate_refs_are_admitted_once() -> None:
    stack = RecipeStackInput(
        explicitRecipeRefs=["openmagi.safety"],
        hardSafetyRefs=["openmagi.safety"],
        turnId="turn-1",
        sessionId="session-1",
    )
    snapshot = _snapshot("openmagi.safety", hard_safety=True)
    result = admit_recipe_stack(
        RecipeAdmissionRequest(stack=stack, admittedSnapshots=[snapshot])
    )

    assert result.blocked is False
    assert result.admitted_recipe_refs == ("openmagi.safety",)
    assert result.admitted_snapshots == (snapshot,)


def test_duplicate_admitted_snapshots_conflict_without_duplicate_admission() -> None:
    stack = RecipeStackInput(
        explicitRecipeRefs=["openmagi.research"],
        turnId="turn-1",
        sessionId="session-1",
    )
    snapshot = _snapshot("openmagi.research")
    result = admit_recipe_stack(
        RecipeAdmissionRequest(stack=stack, admittedSnapshots=[snapshot, snapshot])
    )

    assert result.blocked is True
    assert result.admitted_recipe_refs == ()
    assert result.admitted_snapshots == ()
    assert result.conflicts[0].code == "duplicate_admitted_recipe_snapshot"


def test_raw_snapshot_payload_cannot_be_used_as_admission_authority() -> None:
    payload: dict[str, object] = {
        "recipeRef": "evil.recipe",
        "version": "v1",
        "source": "client",
        "governed": True,
        "hardSafety": False,
        "toolGrants": ("tool.file.write",),
        "toolDenials": (),
        "evidenceRequirements": (),
        "approvalRequirements": (),
        "contextRequirements": (),
        "hookContributions": (),
        "retryPolicy": "none",
        "projectionRules": ("projection.digest_only",),
    }
    payload["snapshotDigest"] = AdmittedRecipeSnapshot.compute_snapshot_digest(payload)

    stack = RecipeStackInput(
        explicitRecipeRefs=["evil.recipe"],
        turnId="turn-1",
        sessionId="session-1",
    )

    with pytest.raises(ValidationError):
        RecipeAdmissionRequest(stack=stack, admittedSnapshots=[payload])


def test_constructor_created_snapshot_instance_cannot_be_used_as_admission_authority() -> None:
    snapshot = _snapshot("evil.recipe", registry_admitted=False)
    stack = RecipeStackInput(
        explicitRecipeRefs=["evil.recipe"],
        turnId="turn-1",
        sessionId="session-1",
    )

    with pytest.raises(ValidationError):
        RecipeAdmissionRequest(stack=stack, admittedSnapshots=[snapshot])


def test_registry_admitted_snapshot_cannot_be_retargeted_by_coherent_mutation() -> None:
    snapshot = _snapshot("openmagi.research", tool_grants=("tool.file.read",))
    retargeted_payload: dict[str, object] = {
        "recipeRef": "evil.recipe",
        "version": "v1",
        "source": "fixture",
        "governed": True,
        "hardSafety": False,
        "toolGrants": ("tool.file.write",),
        "toolDenials": (),
        "evidenceRequirements": ("evidence.public",),
        "approvalRequirements": (),
        "contextRequirements": ("context.public",),
        "hookContributions": (),
        "retryPolicy": "none",
        "projectionRules": ("projection.digest_only",),
    }
    retargeted_payload["snapshotDigest"] = AdmittedRecipeSnapshot.compute_snapshot_digest(
        retargeted_payload
    )
    snapshot.__dict__.update(
        {
            "recipe_ref": "evil.recipe",
            "snapshot_digest": retargeted_payload["snapshotDigest"],
            "version": "v1",
            "source": "fixture",
            "governed": True,
            "hard_safety": False,
            "tool_grants": ("tool.file.write",),
            "tool_denials": (),
            "evidence_requirements": ("evidence.public",),
            "approval_requirements": (),
            "context_requirements": ("context.public",),
            "hook_contributions": (),
            "retry_policy": "none",
            "projection_rules": ("projection.digest_only",),
        }
    )
    stack = RecipeStackInput(
        explicitRecipeRefs=["evil.recipe"],
        turnId="turn-1",
        sessionId="session-1",
    )

    with pytest.raises(ValidationError):
        RecipeAdmissionRequest(stack=stack, admittedSnapshots=[snapshot])


def test_registry_admitted_snapshot_cannot_be_retargeted_by_method_shadowing() -> None:
    snapshot = _snapshot("openmagi.research", tool_grants=("tool.file.read",))
    original_payload = snapshot._digest_payload()
    snapshot.__dict__.update(
        {
            "recipe_ref": "evil.recipe",
            "tool_grants": ("tool.file.write",),
            "_digest_payload": lambda: original_payload,
        }
    )
    stack = RecipeStackInput(
        explicitRecipeRefs=["evil.recipe"],
        turnId="turn-1",
        sessionId="session-1",
    )

    with pytest.raises(ValidationError):
        RecipeAdmissionRequest(stack=stack, admittedSnapshots=[snapshot])


def test_snapshot_copy_ignores_shadowed_model_dump_for_registry_authority() -> None:
    snapshot = _snapshot("openmagi.research")
    forged_payload: dict[str, object] = {
        "recipe_ref": "evil.recipe",
        "version": "v1",
        "source": "fixture",
        "governed": True,
        "hard_safety": False,
        "tool_grants": ("tool.file.write",),
        "tool_denials": (),
        "evidence_requirements": ("evidence.public",),
        "approval_requirements": (),
        "context_requirements": ("context.public",),
        "hook_contributions": (),
        "retry_policy": "none",
        "projection_rules": ("projection.digest_only",),
    }
    forged_payload["snapshot_digest"] = AdmittedRecipeSnapshot.compute_snapshot_digest(
        forged_payload
    )
    snapshot.__dict__["model_dump"] = lambda **_: forged_payload

    copied = snapshot.model_copy()

    assert copied.recipe_ref == "openmagi.research"


def test_client_source_snapshot_is_not_admitted() -> None:
    payload: dict[str, object] = {
        "recipeRef": "evil.recipe",
        "version": "v1",
        "source": "client",
        "governed": True,
        "hardSafety": False,
        "toolGrants": (),
        "toolDenials": (),
        "evidenceRequirements": (),
        "approvalRequirements": (),
        "contextRequirements": (),
        "hookContributions": (),
        "retryPolicy": "none",
        "projectionRules": ("projection.digest_only",),
    }
    payload["snapshotDigest"] = AdmittedRecipeSnapshot.compute_snapshot_digest(payload)

    with pytest.raises(ValidationError):
        AdmittedRecipeSnapshot(**payload)


def test_forged_snapshot_digest_is_rejected() -> None:
    payload = {
        "recipeRef": "openmagi.research",
        "snapshotDigest": "sha256:" + "0" * 64,
        "version": "v1",
        "source": "fixture",
        "governed": True,
        "hardSafety": False,
        "toolGrants": ("tool.file.read",),
        "toolDenials": (),
        "evidenceRequirements": (),
        "approvalRequirements": (),
        "contextRequirements": (),
        "hookContributions": (),
        "retryPolicy": "none",
        "projectionRules": ("projection.digest_only",),
    }

    with pytest.raises(ValidationError, match="snapshot digest mismatch"):
        AdmittedRecipeSnapshot(**payload)


def test_snapshot_projection_rejects_raw_prompt_and_private_config() -> None:
    with pytest.raises(ValidationError):
        _snapshot(
            "openmagi.research",
            raw_prompt="raw model prompt text",
            private_config="/Users/alice/private/config.json",
        )

    snapshot = _snapshot("openmagi.research", tool_grants=("tool.file.read",))
    snapshot.__dict__["raw_prompt"] = "raw model prompt text"
    snapshot.__dict__["private_config"] = "/Users/alice/private/config.json"

    projection = snapshot.public_projection()
    dumped = json.dumps(projection, sort_keys=True)

    assert "raw model prompt" not in dumped
    assert "/Users/alice" not in dumped
    assert DUMMY_SK_PROJ not in dumped
    assert projection["recipeRef"] == "openmagi.research"
    assert projection["toolGrantCount"] == 1


def test_snapshot_model_dump_is_digest_safe_projection() -> None:
    snapshot = _snapshot(
        "openmagi.research",
        tool_grants=("tool.file.read",),
    )
    snapshot.__dict__["raw_prompt"] = "raw model prompt text"
    snapshot.__dict__["private_config"] = "/Users/alice/private/config.json"

    dumped = snapshot.model_dump(by_alias=True, mode="json")
    dumped_json = snapshot.model_dump_json(by_alias=True)
    serialized = json.dumps(dumped, sort_keys=True) + dumped_json

    assert dumped["toolGrantCount"] == 1
    assert "toolGrants" not in dumped
    assert "toolDenials" not in dumped
    assert "tool.file.read" not in serialized
    assert "evidence.public" not in serialized
    assert "context.public" not in serialized
    assert "raw model prompt" not in serialized
    assert "/Users/alice" not in serialized


def test_nested_snapshot_model_dump_is_digest_safe_projection() -> None:
    class SnapshotEnvelope(BaseModel):
        snapshot: AdmittedRecipeSnapshot

    snapshot = _snapshot(
        "openmagi.research",
        tool_grants=("tool.file.read",),
    )
    envelope = SnapshotEnvelope(snapshot=snapshot)
    envelope.snapshot.__dict__["raw_prompt"] = "raw model prompt text"
    envelope.snapshot.__dict__["private_config"] = "/Users/alice/private/config.json"

    dumped = envelope.model_dump(by_alias=True, mode="json")
    serialized = json.dumps(dumped, sort_keys=True)

    assert dumped["snapshot"]["toolGrantCount"] == 1
    assert "toolGrants" not in serialized
    assert "toolDenials" not in serialized
    assert "evidenceRequirements" not in serialized
    assert "contextRequirements" not in serialized
    assert "tool.file.read" not in serialized
    assert "evidence.public" not in serialized
    assert "context.public" not in serialized
    assert "raw model prompt" not in serialized
    assert "/Users/alice" not in serialized


def test_admission_request_model_dump_is_digest_safe_projection() -> None:
    snapshot = _snapshot(
        "openmagi.research",
        tool_grants=("tool.file.read",),
    )
    request = RecipeAdmissionRequest(
        stack=RecipeStackInput(
            explicitRecipeRefs=["openmagi.research"],
            turnId="turn-1",
            sessionId="session-1",
        ),
        admittedSnapshots=(snapshot,),
        requiredGovernedRecipeRefs=("openmagi.research",),
    )

    dumped = request.model_dump(by_alias=True, mode="json")
    dumped_json = request.model_dump_json(by_alias=True)
    serialized = json.dumps(dumped, sort_keys=True) + dumped_json

    assert dumped["admittedSnapshots"][0]["toolGrantCount"] == 1
    assert "toolGrants" not in dumped["admittedSnapshots"][0]
    assert "tool.file.read" not in serialized
    assert "evidence.public" not in serialized
    assert "context.public" not in serialized


def test_nested_admission_request_model_dump_is_digest_safe_projection() -> None:
    class RequestEnvelope(BaseModel):
        request: RecipeAdmissionRequest

    snapshot = _snapshot(
        "openmagi.research",
        tool_grants=("tool.file.read",),
    )
    request = RecipeAdmissionRequest(
        stack=RecipeStackInput(
            explicitRecipeRefs=["openmagi.research"],
            turnId="turn-1",
            sessionId="session-1",
        ),
        admittedSnapshots=(snapshot,),
        requiredGovernedRecipeRefs=("openmagi.research",),
    )

    dumped = RequestEnvelope(request=request).model_dump(by_alias=True, mode="json")
    serialized = json.dumps(dumped, sort_keys=True)

    assert dumped["request"]["admittedSnapshots"][0]["toolGrantCount"] == 1
    assert "toolGrants" not in serialized
    assert "toolDenials" not in serialized
    assert "tool.file.read" not in serialized
    assert "evidence.public" not in serialized
    assert "context.public" not in serialized


def test_snapshot_projection_rejects_mutated_digest_mismatch() -> None:
    snapshot = _snapshot("openmagi.research", tool_grants=("tool.file.read",))
    snapshot.__dict__["tool_grants"] = ("tool.file.write",)

    with pytest.raises(ValueError, match="snapshot digest mismatch"):
        snapshot.public_projection()


def test_snapshot_model_dump_rejects_mutated_digest_mismatch() -> None:
    snapshot = _snapshot("openmagi.research", tool_grants=("tool.file.read",))
    snapshot.__dict__["snapshot_digest"] = "sha256:" + "0" * 64

    with pytest.raises(Exception) as exc_info:
        snapshot.model_dump(by_alias=True, mode="json")

    assert "sha256:" + "0" * 64 not in str(exc_info.value)


def test_nested_admission_result_model_dump_rejects_mutated_snapshot_digest() -> None:
    snapshot = _snapshot("openmagi.research")
    result = RecipeAdmissionResult(
        stackDigest="sha256:" + "a" * 64,
        admittedSnapshots=(snapshot,),
        admittedRecipeRefs=("openmagi.research",),
        missingRequiredRefs=(),
        conflicts=(),
        blocked=False,
    )
    result.admitted_snapshots[0].__dict__["snapshot_digest"] = "sha256:" + "0" * 64

    with pytest.raises(Exception) as exc_info:
        result.model_dump(by_alias=True, mode="json")

    assert "sha256:" + "0" * 64 not in str(exc_info.value)


def test_admission_result_projection_rejects_mutated_stack_digest() -> None:
    result = RecipeAdmissionResult(
        stackDigest="sha256:" + "a" * 64,
        admittedSnapshots=(),
        admittedRecipeRefs=(),
        missingRequiredRefs=(),
        conflicts=(),
        blocked=False,
    )
    result.__dict__["stack_digest"] = "sha256:" + "b" * 64

    with pytest.raises(ValueError, match="stack digest mismatch"):
        result.public_projection()


def test_unblocked_result_refs_must_match_admitted_snapshots() -> None:
    with pytest.raises(ValidationError):
        RecipeAdmissionResult(
            stackDigest="sha256:" + "a" * 64,
            admittedSnapshots=(),
            admittedRecipeRefs=("openmagi.research",),
            missingRequiredRefs=(),
            conflicts=(),
            blocked=False,
        )


def test_public_projection_rejects_non_snapshot_replacement_with_matching_digest() -> None:
    snapshot = _snapshot("openmagi.research")
    result = RecipeAdmissionResult(
        stackDigest="sha256:" + "a" * 64,
        admittedSnapshots=(snapshot,),
        admittedRecipeRefs=("openmagi.research",),
        missingRequiredRefs=(),
        conflicts=(),
        blocked=False,
    )

    class FakeSnapshot:
        snapshot_digest = snapshot.snapshot_digest

        def public_projection(self) -> dict[str, object]:
            return {"recipeRef": "evil.recipe", "toolGrantCount": 999}

    result.__dict__["admitted_snapshots"] = (FakeSnapshot(),)

    with pytest.raises(ValueError):
        result.public_projection()


def test_public_projection_rejects_stateful_conflict_replacement() -> None:
    result = RecipeAdmissionResult(
        stackDigest="sha256:" + "a" * 64,
        admittedSnapshots=(),
        admittedRecipeRefs=(),
        missingRequiredRefs=("openmagi.safety",),
        conflicts=(),
        blocked=True,
    )

    class FakeConflict:
        def public_projection(self) -> dict[str, str]:
            return {
                "code": "hard_safety_recipe_missing",
                "recipeRef": "openmagi.safety",
            }

    result.__dict__["conflicts"] = (FakeConflict(),)

    with pytest.raises(ValueError):
        result.public_projection()


def test_blocked_result_rejects_direct_admitted_activation_material() -> None:
    fallback = _snapshot("openmagi.general")

    with pytest.raises(ValidationError):
        RecipeAdmissionResult(
            stackDigest="sha256:" + "a" * 64,
            admittedSnapshots=(fallback,),
            admittedRecipeRefs=("openmagi.general",),
            missingRequiredRefs=("openmagi.selector-required",),
            conflicts=(),
            blocked=True,
        )


def test_blocked_result_copy_and_serialization_preserve_fail_closed_state() -> None:
    result = RecipeAdmissionResult(
        stackDigest="sha256:" + "a" * 64,
        admittedSnapshots=(),
        admittedRecipeRefs=(),
        missingRequiredRefs=("openmagi.safety",),
        conflicts=(),
        blocked=True,
    )

    with pytest.raises((ValidationError, ValueError)):
        result.model_copy(
            update={
                "missingRequiredRefs": (),
                "conflicts": (),
                "blocked": False,
            }
        )

    result.__dict__["missing_required_refs"] = ()
    result.__dict__["conflicts"] = ()
    result.__dict__["blocked"] = False

    with pytest.raises(ValueError, match="admission result digest mismatch"):
        result.public_projection()


def test_result_private_digest_reset_cannot_reopen_blocked_state() -> None:
    result = RecipeAdmissionResult(
        stackDigest="sha256:" + "a" * 64,
        admittedSnapshots=(),
        admittedRecipeRefs=(),
        missingRequiredRefs=("openmagi.safety",),
        conflicts=(),
        blocked=True,
    )
    result.__dict__["missing_required_refs"] = ()
    result.__dict__["blocked"] = False
    result._validated_result_digest = result._result_digest()

    with pytest.raises(ValueError, match="admission result digest mismatch"):
        result.public_projection()


def test_result_digest_method_shadowing_cannot_reopen_blocked_state() -> None:
    result = RecipeAdmissionResult(
        stackDigest="sha256:" + "a" * 64,
        admittedSnapshots=(),
        admittedRecipeRefs=(),
        missingRequiredRefs=("openmagi.safety",),
        conflicts=(),
        blocked=True,
    )
    original_result_digest = result._result_digest()
    result.__dict__["missing_required_refs"] = ()
    result.__dict__["blocked"] = False
    result.__dict__["_result_digest"] = lambda: original_result_digest

    with pytest.raises(ValueError, match="admission result digest mismatch"):
        result.public_projection()


def test_mutated_admission_request_stack_fails_closed_at_boundary() -> None:
    snapshot = _snapshot("evil.recipe")
    request = RecipeAdmissionRequest(
        stack=RecipeStackInput(
            explicitRecipeRefs=["openmagi.research"],
            turnId="turn-1",
            sessionId="session-1",
        ),
        admittedSnapshots=(snapshot,),
    )

    class FakeStack:
        explicit_recipe_refs = ("evil.recipe",)
        auto_recipe_refs: tuple[str, ...] = ()
        hard_safety_refs: tuple[str, ...] = ()

        def all_recipe_refs(self) -> tuple[str, ...]:
            return ("evil.recipe",)

        def stack_digest(self) -> str:
            return "sha256:" + "a" * 64

    request.__dict__["stack"] = FakeStack()

    with pytest.raises((ValidationError, ValueError)):
        admit_recipe_stack(request)


def test_in_place_mutated_admission_request_stack_fails_closed_at_boundary() -> None:
    snapshot = _snapshot("evil.recipe")
    stack = RecipeStackInput(
        explicitRecipeRefs=["openmagi.research"],
        turnId="turn-1",
        sessionId="session-1",
    )
    request = RecipeAdmissionRequest(
        stack=stack,
        admittedSnapshots=(snapshot,),
    )
    request.stack.__dict__["explicit_recipe_refs"] = ("evil.recipe",)

    with pytest.raises(ValueError, match="recipe stack input digest mismatch"):
        admit_recipe_stack(request)


def test_stack_mutated_before_admission_request_construction_is_rejected() -> None:
    stack = RecipeStackInput(
        explicitRecipeRefs=["openmagi.research"],
        turnId="turn-1",
        sessionId="session-1",
    )
    stack.__dict__["explicit_recipe_refs"] = ("evil.recipe",)

    with pytest.raises(ValidationError):
        RecipeAdmissionRequest(
            stack=stack,
            admittedSnapshots=(_snapshot("evil.recipe"),),
        )


def test_subclassed_stack_is_rejected_at_admission_request_boundary() -> None:
    with pytest.raises(ValidationError):
        RecipeAdmissionRequest(
            stack=ForgedStack(
                explicitRecipeRefs=["openmagi.research"],
                turnId="turn-1",
                sessionId="session-1",
            ),
            admittedSnapshots=(_snapshot("openmagi.research"),),
        )


def test_subclassed_stack_replacement_is_rejected_at_admission_boundary() -> None:
    request = RecipeAdmissionRequest(
        stack=RecipeStackInput(
            explicitRecipeRefs=["openmagi.research"],
            turnId="turn-1",
            sessionId="session-1",
        ),
        admittedSnapshots=(_snapshot("openmagi.research"),),
    )

    request.__dict__["stack"] = ForgedStack(
        explicitRecipeRefs=["evil.recipe"],
        turnId="turn-1",
        sessionId="session-1",
    )

    with pytest.raises(ValueError, match="admission request stack requires RecipeStackInput"):
        admit_recipe_stack(request)


def test_stack_digest_method_shadowing_cannot_bypass_admission_boundary() -> None:
    snapshot = _snapshot("evil.recipe")
    request = RecipeAdmissionRequest(
        stack=RecipeStackInput(
            explicitRecipeRefs=["openmagi.research"],
            turnId="turn-1",
            sessionId="session-1",
        ),
        admittedSnapshots=(snapshot,),
    )
    original_stack_digest = request.stack.stack_digest()
    request.stack.__dict__["explicit_recipe_refs"] = ("evil.recipe",)
    request.stack.__dict__["stack_digest"] = lambda: original_stack_digest

    with pytest.raises(ValueError, match="recipe stack input digest mismatch"):
        admit_recipe_stack(request)


def test_stack_helper_method_shadowing_cannot_bypass_admission_boundary() -> None:
    snapshot = _snapshot("evil.recipe")
    request = RecipeAdmissionRequest(
        stack=RecipeStackInput(
            explicitRecipeRefs=["openmagi.research"],
            turnId="turn-1",
            sessionId="session-1",
        ),
        admittedSnapshots=(snapshot,),
    )
    original_ref_sections = request.stack._safe_ref_sections()
    request.stack.__dict__["explicit_recipe_refs"] = ("evil.recipe",)
    request.stack.__dict__["_safe_ref_sections"] = lambda: original_ref_sections

    with pytest.raises(ValueError, match="recipe stack input digest mismatch"):
        admit_recipe_stack(request)


def test_shadowed_stack_model_copy_cannot_bypass_admission_boundary() -> None:
    snapshot = _snapshot("evil.recipe")
    request = RecipeAdmissionRequest(
        stack=RecipeStackInput(
            explicitRecipeRefs=["openmagi.research"],
            turnId="turn-1",
            sessionId="session-1",
        ),
        admittedSnapshots=(snapshot,),
    )
    request.stack.__dict__["model_copy"] = lambda: RecipeStackInput(
        explicitRecipeRefs=["evil.recipe"],
        turnId="turn-1",
        sessionId="session-1",
    )

    result = admit_recipe_stack(request)

    assert result.blocked is True
    assert result.missing_explicit_refs == ("openmagi.research",)


def test_admission_result_serialization_revalidates_mutated_public_fields() -> None:
    result = RecipeAdmissionResult(
        stackDigest="sha256:" + "a" * 64,
        admittedSnapshots=(),
        admittedRecipeRefs=(),
        missingRequiredRefs=(),
        conflicts=(),
        blocked=False,
    )
    result.__dict__["admitted_recipe_refs"] = (DUMMY_SK_PROJ,)
    result.__dict__["stack_digest"] = "not-a-digest"

    with pytest.raises(Exception) as exc_info:
        result.model_dump(by_alias=True, mode="json")

    error_text = str(exc_info.value)
    assert "not-a-digest" not in error_text
    assert "sk-proj" not in error_text
    assert DUMMY_SECRET_SUFFIX not in error_text


def test_admission_result_copy_and_construct_revalidate() -> None:
    result = RecipeAdmissionResult(
        stackDigest="sha256:" + "a" * 64,
        admittedSnapshots=(),
        admittedRecipeRefs=(),
        missingRequiredRefs=(),
        conflicts=(),
        blocked=False,
    )

    with pytest.raises((ValidationError, ValueError)):
        result.model_copy(update={"admittedRecipeRefs": [DUMMY_SK_PROJ]})

    with pytest.raises(ValidationError):
        RecipeAdmissionResult.model_construct(
            stackDigest="not-a-digest",
            admittedSnapshots=(),
            admittedRecipeRefs=("openmagi.research",),
            missingRequiredRefs=(),
            conflicts=(),
            blocked=False,
        )


def test_admission_result_copy_preserves_registry_snapshot_instances() -> None:
    snapshot = _snapshot("openmagi.research")
    result = RecipeAdmissionResult(
        stackDigest="sha256:" + "a" * 64,
        admittedSnapshots=(snapshot,),
        admittedRecipeRefs=("openmagi.research",),
        missingRequiredRefs=(),
        conflicts=(),
        blocked=False,
    )

    copied = result.model_copy()

    assert copied.blocked is False
    assert copied.admitted_snapshots == (snapshot,)


def test_admitted_snapshots_are_immutable_and_copy_revalidates() -> None:
    snapshot = _snapshot("openmagi.research")

    with pytest.raises(ValidationError):
        snapshot.version = "v2"  # type: ignore[misc]

    with pytest.raises(ValidationError):
        snapshot.model_copy(update={"snapshotDigest": "sha256:" + "1" * 64})

    with pytest.raises(ValidationError):
        snapshot.model_copy(update={"toolGrants": [DUMMY_SK_PROJ]})

    copied = snapshot.model_copy()
    RecipeAdmissionRequest(
        stack=RecipeStackInput(
            explicitRecipeRefs=["openmagi.research"],
            turnId="turn-1",
            sessionId="session-1",
        ),
        admittedSnapshots=(copied,),
    )
