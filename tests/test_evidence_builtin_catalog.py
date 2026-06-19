from __future__ import annotations

import pytest
from pydantic import ValidationError

from magi_agent.evidence.builtin import (
    BuiltInEvidenceType,
    builtin_evidence_by_type,
    builtin_evidence_catalog,
    builtin_evidence_types,
)
from magi_agent.evidence.types import EvidenceContract


EXPECTED_BUILTIN_TYPES = (
    "GitDiff",
    "TestRun",
    "CodeDiagnostics",
    "CommitCheckpoint",
    "FileDeliver",
    "ArtifactVerify",
    "DeterministicEvidenceVerifier",
    "WebSearch",
    "KnowledgeSearch",
    "SourceInspection",
    "PlanVerifier",
    "Calculation",
    "DateRange",
    "Clock",
    "TelegramDeliveryAck",
    "PromptTransform",
    "EditMatch",
    "DocumentCoverage",
)

ALLOWED_PRODUCER_SURFACES = {
    "tool_host",
    "artifact_service",
    "channel_adapter",
    "verifier",
    "plugin",
    "transcript",
    "adk_event",
}


def _by_type() -> dict[str, BuiltInEvidenceType]:
    return {item.type: item for item in builtin_evidence_catalog()}


def test_builtin_catalog_contains_exact_core_owned_types_in_stable_order() -> None:
    catalog = builtin_evidence_catalog()

    assert tuple(item.type for item in catalog) == EXPECTED_BUILTIN_TYPES
    assert builtin_evidence_types() == EXPECTED_BUILTIN_TYPES
    assert all(item.core_owned is True for item in catalog)
    assert all(item.customizable is False for item in catalog)
    assert all(item.traffic_attached is False for item in catalog)
    assert all(item.execution_attached is False for item in catalog)
    assert all(not item.type.startswith("custom:") for item in catalog)


def test_builtin_catalog_marks_producer_surfaces_as_metadata_only() -> None:
    by_type = _by_type()

    assert by_type["GitDiff"].producer_surfaces == ("tool_host", "transcript")
    assert by_type["TestRun"].producer_surfaces == ("tool_host", "transcript")
    assert by_type["CodeDiagnostics"].producer_surfaces == ("tool_host", "transcript")
    assert by_type["CodeDiagnostics"].source_kinds == ("tool_trace", "transcript")
    assert by_type["ArtifactVerify"].producer_surfaces == (
        "artifact_service",
        "verifier",
        "transcript",
    )
    assert by_type["DeterministicEvidenceVerifier"].producer_surfaces == (
        "verifier",
        "transcript",
    )
    assert by_type["WebSearch"].producer_surfaces == (
        "tool_host",
        "plugin",
        "transcript",
        "adk_event",
    )
    assert by_type["TelegramDeliveryAck"].producer_surfaces == (
        "channel_adapter",
        "transcript",
        "adk_event",
    )

    for item in by_type.values():
        assert set(item.producer_surfaces) <= ALLOWED_PRODUCER_SURFACES
        assert item.metadata_only is True


def test_builtin_catalog_returns_defensive_copies() -> None:
    first = builtin_evidence_catalog()
    second = builtin_evidence_catalog()

    assert first == second
    assert first is not second
    assert all(left is not right for left, right in zip(first, second, strict=True))

    changed = first[0].model_copy(update={"description": "mutated locally"})
    assert changed.description == "mutated locally"
    assert builtin_evidence_catalog()[0].description != "mutated locally"

    with pytest.raises(ValidationError):
        first[0].core_owned = False  # type: ignore[misc]


def test_builtin_lookup_returns_defensive_copies_or_none() -> None:
    git_diff = builtin_evidence_by_type("GitDiff")
    assert git_diff is not None
    assert git_diff.type == "GitDiff"
    assert git_diff is not builtin_evidence_by_type("GitDiff")

    assert builtin_evidence_by_type("custom:StripeWebhookAck") is None
    assert builtin_evidence_by_type("NotBuiltIn") is None
    assert builtin_evidence_by_type("Diagnostics") is None


def test_direct_builtin_construction_rejects_custom_or_non_core_catalog_items() -> None:
    with pytest.raises(ValidationError):
        BuiltInEvidenceType(
            type="custom:StripeWebhookAck",
            description="Custom evidence must not be cataloged as built-in.",
            producer_surfaces=("plugin",),
            source_kinds=("external_ack",),
        )

    with pytest.raises(ValidationError):
        BuiltInEvidenceType(
            type="GitDiff",
            description="Built-ins must remain core-owned.",
            producer_surfaces=("tool_host",),
            source_kinds=("tool_trace",),
            core_owned=False,
        )

    # ``customizable`` is Literal[False] -- the C-4 ``FalseOnlyAuthorityModel``
    # kernel coerces a True assertion to False (strictly stronger than the
    # legacy raise; the security invariant "non-customizable" is preserved on
    # every construction surface). Verify the coerce semantic explicitly.
    coerced = BuiltInEvidenceType(
        type="GitDiff",
        description="Built-ins must remain non-customizable.",
        producer_surfaces=("tool_host",),
        source_kinds=("tool_trace",),
        customizable=True,
    )
    assert coerced.customizable is False


def test_builtin_catalog_rejects_non_catalog_pascal_case_items() -> None:
    with pytest.raises(ValidationError):
        BuiltInEvidenceType(
            type="StripeWebhookAck",
            description="Non-catalog evidence must use the custom namespace.",
            producer_surfaces=("plugin",),
            source_kinds=("external_ack",),
        )

    with pytest.raises(ValidationError):
        BuiltInEvidenceType(
            type="Diagnostics",
            description="Generic diagnostics must not alias CodeDiagnostics evidence.",
            producer_surfaces=("tool_host",),
            source_kinds=("tool_trace",),
        )


def test_model_copy_revalidates_protected_builtin_catalog_invariants() -> None:
    git_diff = builtin_evidence_by_type("GitDiff")
    assert git_diff is not None

    # Literal[True] catalog invariants still raise on a False assertion.
    with pytest.raises(ValidationError):
        git_diff.model_copy(update={"coreOwned": False})
    with pytest.raises(ValidationError):
        git_diff.model_copy(update={"metadataOnly": False})

    # Literal[False] catalog invariants are force-falsed by the C-4
    # ``FalseOnlyAuthorityModel`` kernel on every construction surface; a True
    # assertion via ``model_copy`` is coerced to False (strictly stronger than
    # the legacy raise -- the security contract "non-customizable / no live
    # traffic / no live execution" is preserved without an escape hatch).
    coerced_customizable = git_diff.model_copy(update={"customizable": True})
    assert coerced_customizable.customizable is False
    coerced_traffic = git_diff.model_copy(update={"traffic_attached": True})
    assert coerced_traffic.traffic_attached is False
    coerced_execution = git_diff.model_copy(update={"executionAttached": True})
    assert coerced_execution.execution_attached is False


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("coreOwned", 1),
        ("metadataOnly", 1),
    ),
)
def test_builtin_literal_true_metadata_rejects_coerced_values(
    field_name: str,
    value: object,
) -> None:
    with pytest.raises(ValidationError):
        BuiltInEvidenceType.model_validate(
            {
                "type": "GitDiff",
                "description": "Workspace diff evidence observed after mutation.",
                "producerSurfaces": ["tool_host"],
                "sourceKinds": ["tool_trace"],
                field_name: value,
            }
        )


@pytest.mark.parametrize(
    ("field_name",),
    (
        ("customizable",),
        ("trafficAttached",),
        ("executionAttached",),
    ),
)
def test_builtin_literal_false_metadata_force_false_coerces_any_value(
    field_name: str,
) -> None:
    # ``Literal[False]`` fields are owned by the C-4
    # ``FalseOnlyAuthorityModel`` kernel: any non-None caller assertion --
    # including bool-coerced int values (0/1) -- is force-falsed BEFORE
    # the Literal type check, replacing the legacy raise with the strictly-
    # stronger coerce semantic on every construction surface.
    for value in (0, 1, True, False):
        coerced = BuiltInEvidenceType.model_validate(
            {
                "type": "GitDiff",
                "description": "Workspace diff evidence observed after mutation.",
                "producerSurfaces": ["tool_host"],
                "sourceKinds": ["tool_trace"],
                field_name: value,
            }
        )
        dumped = coerced.model_dump(by_alias=True)
        assert dumped[field_name] is False


def test_custom_evidence_contracts_remain_declarative_metadata_only() -> None:
    contract = EvidenceContract.model_validate(
        {
            "id": "billing-change-safe",
            "triggers": ["beforeCommit"],
            "requirements": [
                {
                    "type": "custom:StripeWebhookAck",
                    "fields": {
                        "status": {"equals": "received"},
                        "eventId": {"exists": True},
                    },
                }
            ],
            "onMissing": "audit",
        }
    )

    assert contract.requirements[0].type == "custom:StripeWebhookAck"
    assert contract.requirements[0].fields["status"].equals == "received"
    assert builtin_evidence_by_type(contract.requirements[0].type) is None
    assert "custom:StripeWebhookAck" not in builtin_evidence_types()

    dumped = contract.model_dump(by_alias=True)
    assert dumped["requirements"][0]["fields"]["eventId"]["exists"] is True
    assert dumped["trafficAttached"] is False
    assert dumped["executionAttached"] is False
