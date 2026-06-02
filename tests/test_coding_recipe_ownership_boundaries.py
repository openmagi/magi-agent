from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from magi_agent.harness.coding.ownership_projection import (
    CodingRecipeOwnershipProjection,
    project_coding_recipe_ownership,
)
from magi_agent.recipes.first_party.coding.ownership import (
    CodingMechanicOwnership,
    REQUIRED_CODING_MECHANICS,
    build_coding_recipe_ownership_manifest,
)


PYTHON_ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = PYTHON_ROOT / "tests/fixtures/parity/coding_harness_consolidated_matrix.json"


def _projection() -> dict[str, object]:
    return project_coding_recipe_ownership().model_dump(by_alias=True, mode="json")


def _rendered_projection() -> str:
    return json.dumps(_projection(), sort_keys=True)


def _valid_projection_payload(**updates: object) -> dict[str, object]:
    payload = project_coding_recipe_ownership().model_dump(by_alias=True, mode="python")
    payload.update(updates)
    return payload


def test_coding_recipe_manifest_declares_all_required_mechanics_as_recipe_owned() -> None:
    manifest = build_coding_recipe_ownership_manifest()

    assert tuple(mechanic.mechanic_id for mechanic in manifest.mechanics) == REQUIRED_CODING_MECHANICS
    assert {mechanic.owner for mechanic in manifest.mechanics} == {"coding_recipe"}
    assert manifest.owning_layer == "Coding recipe/harness"
    assert manifest.activation_gate == "PR1-coding-ownership-fixture-only"
    assert manifest.core_touch_allowed is False
    assert manifest.default_off is True
    assert manifest.live_authority_allowed is False


def test_public_projection_is_default_off_local_only_and_has_no_live_attachments() -> None:
    projection = _projection()

    assert projection["defaultOff"] is True
    assert projection["liveAuthorityAllowed"] is False
    assert projection["coreTouchAllowed"] is False
    assert projection["activationGate"] == "PR1-coding-ownership-fixture-only"
    assert projection["owningLayer"] == "Coding recipe/harness"
    assert projection["adkPrimitiveNames"] == ["Agent.metadata", "FunctionTool.name"]
    assert projection["liveAttachmentRefs"] == []
    assert projection["toolHostDispatchAttached"] is False
    assert projection["adkRunnerAttached"] is False
    assert projection["modelProviderAttached"] is False
    assert projection["workspaceMutationAttached"] is False


def test_projection_exposes_digest_safe_policy_refs_without_raw_or_secret_fields() -> None:
    projection = _projection()
    rendered = _rendered_projection()

    assert projection["policyRefs"]
    assert all(ref.startswith("policy-ref:sha256:") for ref in projection["policyRefs"])
    assert all("policy:" not in ref.removeprefix("policy-ref:") for ref in projection["policyRefs"])
    assert all("read-before-edit" not in ref for ref in projection["policyRefs"])
    forbidden_terms = (
        "raw",
        "prompt",
        "output",
        "transcript",
        "/Users/",
        "/workspace/",
        "secret",
        "token",
        "password",
        "credential",
        "PRIVATE_PAYLOAD_DO_NOT_PROJECT",
    )
    assert not any(term.lower() in rendered.lower() for term in forbidden_terms)


def test_core_references_are_generic_substrate_names_only() -> None:
    projection = _projection()

    assert projection["coreSubstrateRefs"] == [
        "agent-metadata",
        "tool-name-metadata",
        "callback-metadata",
        "session-metadata",
        "artifact-ref-metadata",
        "evaluation-result-metadata",
    ]
    assert "ToolHost" not in _rendered_projection()
    assert "dispatcher" not in _rendered_projection()
    assert "registry" not in _rendered_projection()
    assert "magi_agent.runtime" not in _rendered_projection()


def test_manifest_rejects_live_attachment_and_raw_policy_refs() -> None:
    with pytest.raises(ValidationError):
        build_coding_recipe_ownership_manifest(
            liveAttachmentRefs=("toolhost:dispatch",),
        )

    with pytest.raises(ValidationError):
        build_coding_recipe_ownership_manifest(
            policyRefs=("policy:/Users/kevin/private/raw_prompt",),
        )


def test_exported_projection_rejects_direct_raw_or_non_digest_policy_refs() -> None:
    with pytest.raises(ValidationError):
        CodingRecipeOwnershipProjection.model_validate(
            _valid_projection_payload(policyRefs=("policy:/Users/kevin/private/raw_prompt",)),
        )

    with pytest.raises(ValidationError):
        CodingRecipeOwnershipProjection.model_validate(
            _valid_projection_payload(policyRefs=("policy-ref:not-a-sha",)),
        )


def test_exported_projection_rejects_direct_private_or_raw_manifest_id() -> None:
    for manifest_id in (
        "/Users/kevin/private/raw_prompt",
        "ToolHost:dispatcher",
        "magi_agent.runtime",
    ):
        with pytest.raises(ValidationError):
            CodingRecipeOwnershipProjection.model_validate(
                _valid_projection_payload(manifestId=manifest_id),
            )


def test_mechanic_construct_and_copy_cannot_bypass_metadata_validators() -> None:
    valid = build_coding_recipe_ownership_manifest().mechanics[0]

    with pytest.raises(ValidationError):
        CodingMechanicOwnership.model_construct(
            mechanicId="read-before-edit",
            policyRef="policy:/Users/kevin/private/raw_prompt",
            substrateRefs=("tool-name-metadata",),
        )

    with pytest.raises(ValidationError):
        valid.model_copy(
            update={
                "substrateRefs": ("/Users/kevin/private/raw_prompt",),
            },
        )


def test_pr1_matrix_row_is_marked_covered_by_ownership_files_and_test() -> None:
    data = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
    row = next(item for item in data["rows"] if item["id"] == "coding_recipe_ownership_boundaries")

    assert row["alreadyCovered"] is True
    assert row["defaultOff"] is True
    assert row["liveAuthorityAllowed"] is False
    assert row["coveredByFiles"] == [
        "magi_agent/recipes/coding_mutation.py",
        "magi_agent/recipes/coding_subagents.py",
        "magi_agent/coding/meta_adapter.py",
        "magi_agent/recipes/first_party/coding/ownership.py",
        "magi_agent/harness/coding/ownership_projection.py",
    ]
    assert row["coveredByTests"] == [
        "tests/recipes/test_coding_mutation_recipe.py",
        "tests/recipes/test_coding_subagent_recipe.py",
        "tests/test_coding_meta_adapter.py",
        "tests/test_coding_recipe_ownership_boundaries.py",
    ]
    assert row["missingImplementation"] == ["complete"]
