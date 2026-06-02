from __future__ import annotations

import pytest
from pydantic import ValidationError

from magi_agent.routing.deterministic import (
    BaselineShadowMeasurementMetadata,
    DeterministicClassificationMetadata,
    DeterministicRoutePlanMetadata,
    DeterministicRoutingScope,
    DeterministicRolloutMetadata,
    FinalAnswerPolicyMetadata,
    RequiredEvidenceMetadata,
    build_baseline_shadow_route,
)


def test_arithmetic_exactness_produces_calculation_shadow_metadata_without_enforcement() -> None:
    route = build_baseline_shadow_route(
        "What is 128 * 49?",
        scope=DeterministicRoutingScope(agentRole="general", runOn="main", spawnDepth=0),
    )

    assert route.mode == "baseline_shadow"
    assert route.classification.exactness_requirements == ("arithmetic",)
    assert route.required_tool_names == ("Calculation",)
    assert [(item.evidence_type, item.status) for item in route.required_evidence] == [
        ("Calculation", "required_missing"),
    ]
    assert route.final_answer_policy.mode == "require_evidence_citation"
    assert route.baseline_measurement.would_route is True
    assert route.baseline_measurement.changed_final_action is False
    assert route.enforcement_attached is False
    assert route.traffic_attached is False


def test_current_fact_source_claims_require_search_and_source_inspection_evidence() -> None:
    route = build_baseline_shadow_route(
        "What is the current CEO of OpenAI? Cite your source.",
        scope=DeterministicRoutingScope(agentRole="research", runOn="child", spawnDepth=1),
    )

    assert route.classification.exactness_requirements == ("current_public_fact", "source_claim")
    assert route.required_tool_names == ("Search", "SourceInspection")
    assert [item.evidence_type for item in route.required_evidence] == [
        "WebSearch",
        "SourceInspection",
    ]
    assert route.final_answer_policy.mode == "require_evidence_citation"


def test_coding_file_state_route_metadata_requires_workspace_diff_tests_checkpoint_only() -> None:
    route = build_baseline_shadow_route(
        "Edit magi_agent/tools/base.py and run the tests before committing.",
        scope=DeterministicRoutingScope(agentRole="coding", runOn="child", spawnDepth=2),
    )

    assert route.classification.exactness_requirements == ("code_change", "file_state")
    assert route.required_tool_names == (
        "IsolatedWorkspace",
        "FileRead",
        "Diff",
        "Diagnostics",
        "TestRunner",
        "Checkpoint",
    )
    assert [item.evidence_type for item in route.required_evidence] == [
        "WorkspaceIsolation",
        "FileInspection",
        "GitDiff",
        "Diagnostics",
        "TestRun",
        "CommitCheckpoint",
    ]
    assert route.execution_attached is False
    assert route.runner_attached is False


def test_high_uncertainty_requires_clarification_metadata_instead_of_model_only() -> None:
    route = build_baseline_shadow_route(
        "Fix it there with the usual thing.",
        scope=DeterministicRoutingScope(agentRole="general", runOn="main", spawnDepth=0),
    )

    assert route.classification.uncertainty == "high"
    assert route.classification.clarification_required is True
    assert route.classification.exactness_requirements == ("clarification",)
    assert route.required_tool_names == ()
    assert route.required_evidence == ()
    assert route.final_answer_policy.mode == "require_clarification"
    assert route.baseline_measurement.would_route is True
    assert route.baseline_measurement.routed_tool_names == ()
    assert route.baseline_measurement.routed_evidence_types == ()
    assert route.retry_instruction.reason == "clarify_missing_target"
    assert route.retry_instruction.missing_evidence_types == ()


def test_audit_rollout_requires_prior_baseline_shadow_measurement_metadata() -> None:
    with pytest.raises(ValidationError, match="baseline shadow measurement"):
        DeterministicRolloutMetadata(mode="audit")

    route = build_baseline_shadow_route(
        "What is 2 + 2?",
        scope=DeterministicRoutingScope(agentRole="general", runOn="main", spawnDepth=0),
    )
    rollout = DeterministicRolloutMetadata(
        mode="audit",
        baselineMeasurement=route.baseline_measurement,
    )

    assert rollout.audit_ready is True
    assert rollout.traffic_attached is False


def test_rollout_audit_ready_requires_baseline_shadow_measurement_metadata() -> None:
    with pytest.raises(ValidationError, match="baseline shadow measurement"):
        DeterministicRolloutMetadata(
            mode="baseline_shadow",
            auditReady=True,
            baselineMeasurement=None,
        )


def test_audit_exactness_policy_requires_evidence_citation_without_live_blocking() -> None:
    route = build_baseline_shadow_route(
        "What is 2 + 2?",
        scope=DeterministicRoutingScope(agentRole="general", runOn="main", spawnDepth=0),
        mode="audit",
    )

    assert route.mode == "audit"
    assert route.final_answer_policy.mode == "require_evidence_citation"
    assert route.final_answer_policy.metadata_only is True
    assert route.enforcement_attached is False


def test_enforce_block_policy_is_metadata_only_and_cannot_attach_to_live_traffic() -> None:
    route = build_baseline_shadow_route(
        "What is 2 + 2?",
        scope=DeterministicRoutingScope(agentRole="general", runOn="main", spawnDepth=0),
        mode="block_final_answer",
    )

    assert route.mode == "block_final_answer"
    assert route.final_answer_policy.mode == "block_without_evidence"
    assert route.final_answer_policy.metadata_only is True
    assert route.enforcement_attached is False

    with pytest.raises(ValidationError):
        route.model_copy(update={"enforcementAttached": True})


@pytest.mark.parametrize(
    "flag",
    (
        "trafficAttached",
        "executionAttached",
        "runnerAttached",
        "enforcementAttached",
        "canaryAttached",
    ),
)
def test_model_copy_cannot_enable_any_attachment_flag(flag: str) -> None:
    route = build_baseline_shadow_route(
        "What is 2 + 2?",
        scope=DeterministicRoutingScope(agentRole="general", runOn="main", spawnDepth=0),
    )

    with pytest.raises(ValidationError):
        route.model_copy(update={flag: True})


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("requiredToolNames", ()),
        ("requiredToolNames", ("Calculation", "Calculation")),
        ("requiredToolNames", ("Calculation", " ")),
    ),
)
def test_route_required_tool_names_are_non_empty_unique_strings(
    field_name: str,
    value: tuple[str, ...],
) -> None:
    route = build_baseline_shadow_route(
        "What is 2 + 2?",
        scope=DeterministicRoutingScope(agentRole="general", runOn="main", spawnDepth=0),
    )

    with pytest.raises(ValidationError):
        route.model_copy(update={field_name: value})


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("routedToolNames", ("Calculation", "Calculation")),
        ("routedToolNames", ("Calculation", " ")),
        ("routedEvidenceTypes", ("Calculation", "Calculation")),
        ("routedEvidenceTypes", ("Calculation", " ")),
    ),
)
def test_baseline_measurement_routed_names_are_unique_non_empty_strings(
    field_name: str,
    value: tuple[str, ...],
) -> None:
    measurement = BaselineShadowMeasurementMetadata(
        wouldRoute=True,
        routedToolNames=("Calculation",),
        routedEvidenceTypes=("Calculation",),
    )

    with pytest.raises(ValidationError):
        measurement.model_copy(update={field_name: value})


def test_baseline_measurement_would_not_route_cannot_keep_routed_metadata() -> None:
    with pytest.raises(ValidationError, match="wouldRoute=False"):
        BaselineShadowMeasurementMetadata(
            wouldRoute=False,
            routedToolNames=("Calculation",),
            routedEvidenceTypes=("Calculation",),
        )


@pytest.mark.parametrize(
    ("routed_tool_names", "routed_evidence_types"),
    (
        (("Calculation",), ()),
        ((), ("Calculation",)),
    ),
)
def test_baseline_measurement_rejects_partial_routed_metadata(
    routed_tool_names: tuple[str, ...],
    routed_evidence_types: tuple[str, ...],
) -> None:
    with pytest.raises(ValidationError, match="partial routed metadata"):
        BaselineShadowMeasurementMetadata(
            wouldRoute=True,
            routedToolNames=routed_tool_names,
            routedEvidenceTypes=routed_evidence_types,
        )


def test_route_would_route_true_needs_routed_metadata_or_clarification() -> None:
    route = build_baseline_shadow_route(
        "What is 2 + 2?",
        scope=DeterministicRoutingScope(agentRole="general", runOn="main", spawnDepth=0),
    )
    invalid_measurement = BaselineShadowMeasurementMetadata.model_construct(
        would_route=True,
        routed_tool_names=(),
        routed_evidence_types=(),
    )

    with pytest.raises(ValidationError, match="wouldRoute=True"):
        route.model_copy(update={"baselineMeasurement": invalid_measurement})


def test_clarification_route_allows_would_route_true_without_tools_or_evidence() -> None:
    route = build_baseline_shadow_route(
        "Do it there with the usual thing.",
        scope=DeterministicRoutingScope(agentRole="general", runOn="main", spawnDepth=0),
    )

    assert route.baseline_measurement.would_route is True
    assert route.baseline_measurement.routed_tool_names == ()
    assert route.baseline_measurement.routed_evidence_types == ()
    assert route.final_answer_policy.mode == "require_clarification"


def test_clarification_route_requires_would_route_true_on_model_copy() -> None:
    route = build_baseline_shadow_route(
        "Do it there with the usual thing.",
        scope=DeterministicRoutingScope(agentRole="general", runOn="main", spawnDepth=0),
    )
    invalid_measurement = BaselineShadowMeasurementMetadata(
        wouldRoute=False,
        routedToolNames=(),
        routedEvidenceTypes=(),
    )

    with pytest.raises(ValidationError, match="clarification"):
        route.model_copy(update={"baselineMeasurement": invalid_measurement})


def test_clarification_route_requires_would_route_true_on_public_construction() -> None:
    route = build_baseline_shadow_route(
        "Do it there with the usual thing.",
        scope=DeterministicRoutingScope(agentRole="general", runOn="main", spawnDepth=0),
    )
    invalid_measurement = BaselineShadowMeasurementMetadata(
        wouldRoute=False,
        routedToolNames=(),
        routedEvidenceTypes=(),
    )

    with pytest.raises(ValidationError, match="clarification"):
        DeterministicRoutePlanMetadata(
            mode=route.mode,
            reason=route.reason,
            scope=route.scope,
            classification=route.classification,
            requiredToolNames=route.required_tool_names,
            requiredEvidence=route.required_evidence,
            finalAnswerPolicy=route.final_answer_policy,
            retryInstruction=route.retry_instruction,
            baselineMeasurement=invalid_measurement,
        )


def test_no_exactness_baseline_route_does_not_route_or_require_evidence() -> None:
    route = build_baseline_shadow_route(
        "Say hello.",
        scope=DeterministicRoutingScope(agentRole="general", runOn="main", spawnDepth=0),
    )

    assert route.classification.exactness_requirements == ()
    assert route.classification.clarification_required is False
    assert route.baseline_measurement.would_route is False
    assert route.baseline_measurement.routed_tool_names == ()
    assert route.baseline_measurement.routed_evidence_types == ()
    assert route.required_tool_names == ()
    assert route.required_evidence == ()
    assert route.final_answer_policy.mode == "allow_model_final_answer"
    assert route.retry_instruction.missing_evidence_types == ()


@pytest.mark.parametrize("mode", ("baseline_shadow", "audit"))
@pytest.mark.parametrize(
    "policy_mode",
    ("require_clarification", "require_evidence_citation", "block_without_evidence"),
)
def test_public_no_exactness_baseline_and_audit_routes_require_model_final_answer_policy(
    mode: str,
    policy_mode: str,
) -> None:
    route = build_baseline_shadow_route(
        "Say hello.",
        scope=DeterministicRoutingScope(agentRole="general", runOn="main", spawnDepth=0),
        mode=mode,  # type: ignore[arg-type]
    )

    assert route.classification.exactness_requirements == ()
    assert route.classification.clarification_required is False

    with pytest.raises(ValidationError, match="allow_model_final_answer"):
        DeterministicRoutePlanMetadata(
            mode=route.mode,
            reason=route.reason,
            scope=route.scope,
            classification=route.classification,
            requiredToolNames=route.required_tool_names,
            requiredEvidence=route.required_evidence,
            finalAnswerPolicy=FinalAnswerPolicyMetadata(mode=policy_mode),  # type: ignore[arg-type]
            retryInstruction=route.retry_instruction,
            baselineMeasurement=route.baseline_measurement,
        )


@pytest.mark.parametrize("mode", ("baseline_shadow", "audit"))
@pytest.mark.parametrize(
    "policy_mode",
    ("require_clarification", "require_evidence_citation", "block_without_evidence"),
)
def test_model_copy_no_exactness_baseline_and_audit_routes_require_model_final_answer_policy(
    mode: str,
    policy_mode: str,
) -> None:
    route = build_baseline_shadow_route(
        "Say hello.",
        scope=DeterministicRoutingScope(agentRole="general", runOn="main", spawnDepth=0),
        mode=mode,  # type: ignore[arg-type]
    )

    assert route.classification.exactness_requirements == ()
    assert route.classification.clarification_required is False

    with pytest.raises(ValidationError, match="allow_model_final_answer"):
        route.model_copy(
            update={
                "finalAnswerPolicy": FinalAnswerPolicyMetadata(mode=policy_mode),  # type: ignore[arg-type]
            },
        )


@pytest.mark.parametrize(
    "update",
    (
        {"requiredToolNames": ("Search",)},
        {
            "baselineMeasurement": BaselineShadowMeasurementMetadata.model_construct(
                would_route=True,
                routed_tool_names=("Search",),
                routed_evidence_types=("Calculation",),
            ),
        },
    ),
)
def test_route_required_tools_match_baseline_measurement_tools(update: dict[str, object]) -> None:
    route = build_baseline_shadow_route(
        "What is 2 + 2?",
        scope=DeterministicRoutingScope(agentRole="general", runOn="main", spawnDepth=0),
    )

    with pytest.raises(ValidationError, match="requiredToolNames"):
        route.model_copy(update=update)


@pytest.mark.parametrize(
    "update",
    (
        {"requiredEvidence": (RequiredEvidenceMetadata(evidenceType="WebSearch"),)},
        {
            "baselineMeasurement": BaselineShadowMeasurementMetadata.model_construct(
                would_route=True,
                routed_tool_names=("Calculation",),
                routed_evidence_types=("WebSearch",),
            ),
        },
    ),
)
def test_route_required_evidence_matches_baseline_measurement_evidence(
    update: dict[str, object],
) -> None:
    route = build_baseline_shadow_route(
        "What is 2 + 2?",
        scope=DeterministicRoutingScope(agentRole="general", runOn="main", spawnDepth=0),
    )

    with pytest.raises(ValidationError, match="requiredEvidence"):
        route.model_copy(update=update)


def test_clarification_classification_requires_clarification_policy() -> None:
    route = build_baseline_shadow_route(
        "Fix it there with the usual thing.",
        scope=DeterministicRoutingScope(agentRole="general", runOn="main", spawnDepth=0),
    )

    with pytest.raises(ValidationError, match="require_clarification"):
        route.model_copy(
            update={
                "finalAnswerPolicy": FinalAnswerPolicyMetadata(mode="require_evidence_citation"),
            },
        )


@pytest.mark.parametrize("mode", ("baseline_shadow", "audit"))
def test_exactness_policy_requires_evidence_citation_in_baseline_and_audit(mode: str) -> None:
    route = build_baseline_shadow_route(
        "What is 2 + 2?",
        scope=DeterministicRoutingScope(agentRole="general", runOn="main", spawnDepth=0),
        mode=mode,  # type: ignore[arg-type]
    )

    with pytest.raises(ValidationError, match="require_evidence_citation"):
        route.model_copy(
            update={"finalAnswerPolicy": FinalAnswerPolicyMetadata(mode="allow_model_final_answer")},
        )


@pytest.mark.parametrize("mode", ("enforce", "block_final_answer"))
def test_exactness_policy_requires_block_metadata_in_enforce_and_block(mode: str) -> None:
    route = build_baseline_shadow_route(
        "What is 2 + 2?",
        scope=DeterministicRoutingScope(agentRole="general", runOn="main", spawnDepth=0),
        mode=mode,  # type: ignore[arg-type]
    )

    with pytest.raises(ValidationError, match="block_without_evidence"):
        route.model_copy(
            update={
                "finalAnswerPolicy": FinalAnswerPolicyMetadata(mode="require_evidence_citation"),
            },
        )


@pytest.mark.parametrize("mode", ("enforce", "block_final_answer"))
def test_public_enforce_route_without_exactness_requires_block_policy(mode: str) -> None:
    route = build_baseline_shadow_route(
        "Say hello.",
        scope=DeterministicRoutingScope(agentRole="general", runOn="main", spawnDepth=0),
        mode=mode,  # type: ignore[arg-type]
    )

    assert route.classification.exactness_requirements == ()

    with pytest.raises(ValidationError, match="block_without_evidence"):
        DeterministicRoutePlanMetadata(
            mode=route.mode,
            reason=route.reason,
            scope=route.scope,
            classification=route.classification,
            requiredToolNames=route.required_tool_names,
            requiredEvidence=route.required_evidence,
            finalAnswerPolicy=FinalAnswerPolicyMetadata(mode="allow_model_final_answer"),
            retryInstruction=route.retry_instruction,
            baselineMeasurement=route.baseline_measurement,
        )


@pytest.mark.parametrize("mode", ("enforce", "block_final_answer"))
def test_model_copy_enforce_route_without_exactness_requires_block_policy(mode: str) -> None:
    route = build_baseline_shadow_route(
        "Say hello.",
        scope=DeterministicRoutingScope(agentRole="general", runOn="main", spawnDepth=0),
        mode=mode,  # type: ignore[arg-type]
    )

    assert route.classification.exactness_requirements == ()

    with pytest.raises(ValidationError, match="block_without_evidence"):
        route.model_copy(
            update={"finalAnswerPolicy": FinalAnswerPolicyMetadata(mode="allow_model_final_answer")},
        )


@pytest.mark.parametrize("mode", ("enforce", "block_final_answer"))
def test_builder_enforce_clarification_route_uses_block_policy(mode: str) -> None:
    route = build_baseline_shadow_route(
        "Fix it there with the usual thing.",
        scope=DeterministicRoutingScope(agentRole="general", runOn="main", spawnDepth=0),
        mode=mode,  # type: ignore[arg-type]
    )

    assert route.classification.clarification_required is True
    assert route.final_answer_policy.mode == "block_without_evidence"


def test_public_classification_rejects_clarification_exactness_without_required_flag() -> None:
    with pytest.raises(ValidationError, match="clarificationRequired=True"):
        DeterministicClassificationMetadata(
            exactnessRequirements=("clarification",),
            clarificationRequired=False,
        )


def test_public_classification_rejects_required_flag_without_clarification_exactness() -> None:
    with pytest.raises(ValidationError, match="clarification exactness requirement"):
        DeterministicClassificationMetadata(
            exactnessRequirements=(),
            clarificationRequired=True,
        )


def test_model_copy_revalidates_classification_clarification_exactness_consistency() -> None:
    classification = DeterministicClassificationMetadata(
        exactnessRequirements=("clarification",),
        clarificationRequired=True,
    )

    with pytest.raises(ValidationError, match="clarificationRequired=True"):
        classification.model_copy(update={"clarificationRequired": False})


def test_public_classification_rejects_mixed_clarification_exactness() -> None:
    with pytest.raises(ValidationError, match="clarification-only"):
        DeterministicClassificationMetadata(
            exactnessRequirements=("code_change", "clarification"),
            clarificationRequired=True,
        )


def test_model_copy_rejects_mixed_clarification_exactness() -> None:
    classification = DeterministicClassificationMetadata(
        exactnessRequirements=("clarification",),
        clarificationRequired=True,
    )

    with pytest.raises(ValidationError, match="clarification-only"):
        classification.model_copy(update={"exactnessRequirements": ("source_claim", "clarification")})


def test_route_model_copy_revalidates_constructed_invalid_classification() -> None:
    route = build_baseline_shadow_route(
        "Fix it there with the usual thing.",
        scope=DeterministicRoutingScope(agentRole="general", runOn="main", spawnDepth=0),
    )
    invalid = DeterministicClassificationMetadata.model_construct(
        exactness_requirements=("clarification",),
        risk_level="low",
        uncertainty="high",
        clarification_required=False,
    )

    with pytest.raises(ValidationError, match="clarificationRequired=True"):
        route.model_copy(update={"classification": invalid})


def test_retry_instruction_missing_evidence_matches_required_evidence() -> None:
    route = build_baseline_shadow_route(
        "What is 2 + 2?",
        scope=DeterministicRoutingScope(agentRole="general", runOn="main", spawnDepth=0),
    )

    with pytest.raises(ValidationError, match="missingEvidenceTypes"):
        route.model_copy(
            update={
                "retryInstruction": route.retry_instruction.model_copy(
                    update={"missingEvidenceTypes": ("WebSearch",)},
                ),
            },
        )


def test_retry_instruction_may_omit_missing_evidence_when_clarification_required() -> None:
    route = build_baseline_shadow_route(
        "Fix it there with the usual thing.",
        scope=DeterministicRoutingScope(agentRole="general", runOn="main", spawnDepth=0),
    )

    assert route.retry_instruction.missing_evidence_types == ()


def test_model_copy_rejects_retry_missing_evidence_on_clarification_route() -> None:
    route = build_baseline_shadow_route(
        "Fix it there with the usual thing.",
        scope=DeterministicRoutingScope(agentRole="general", runOn="main", spawnDepth=0),
    )

    with pytest.raises(ValidationError, match="missingEvidenceTypes"):
        route.model_copy(
            update={
                "retryInstruction": route.retry_instruction.model_copy(
                    update={"missingEvidenceTypes": ("Calculation",)},
                ),
            },
        )


def test_public_construction_rejects_retry_missing_evidence_on_clarification_route() -> None:
    route = build_baseline_shadow_route(
        "Fix it there with the usual thing.",
        scope=DeterministicRoutingScope(agentRole="general", runOn="main", spawnDepth=0),
    )

    with pytest.raises(ValidationError, match="missingEvidenceTypes"):
        DeterministicRoutePlanMetadata(
            mode=route.mode,
            reason=route.reason,
            scope=route.scope,
            classification=route.classification,
            requiredToolNames=route.required_tool_names,
            requiredEvidence=route.required_evidence,
            finalAnswerPolicy=route.final_answer_policy,
            retryInstruction=route.retry_instruction.model_copy(
                update={"missingEvidenceTypes": ("Calculation",)},
            ),
            baselineMeasurement=route.baseline_measurement,
        )


def test_model_copy_rejects_retry_missing_evidence_on_no_route_baseline() -> None:
    route = build_baseline_shadow_route(
        "Say hello.",
        scope=DeterministicRoutingScope(agentRole="general", runOn="main", spawnDepth=0),
    )

    with pytest.raises(ValidationError, match="missingEvidenceTypes"):
        route.model_copy(
            update={
                "retryInstruction": route.retry_instruction.model_copy(
                    update={"missingEvidenceTypes": ("Calculation",)},
                ),
            },
        )


def test_public_construction_rejects_retry_missing_evidence_on_no_route_baseline() -> None:
    route = build_baseline_shadow_route(
        "Say hello.",
        scope=DeterministicRoutingScope(agentRole="general", runOn="main", spawnDepth=0),
    )

    with pytest.raises(ValidationError, match="missingEvidenceTypes"):
        DeterministicRoutePlanMetadata(
            mode=route.mode,
            reason=route.reason,
            scope=route.scope,
            classification=route.classification,
            requiredToolNames=route.required_tool_names,
            requiredEvidence=route.required_evidence,
            finalAnswerPolicy=route.final_answer_policy,
            retryInstruction=route.retry_instruction.model_copy(
                update={"missingEvidenceTypes": ("Calculation",)},
            ),
            baselineMeasurement=route.baseline_measurement,
        )


def test_phase_0_gate_surface_is_explicit_metadata_only_and_not_live_attached() -> None:
    route = build_baseline_shadow_route(
        "What is 2 + 2?",
        scope=DeterministicRoutingScope(agentRole="general", runOn="main", spawnDepth=0),
    )

    assert route.gate_surface.classifier_gates_represented is False
    assert route.gate_surface.active_requirement_gates_represented is False
    assert route.gate_surface.phase0_shadow_only is True
    assert route.gate_surface.metadata_only is True
    assert route.gate_surface.traffic_attached is False
    assert route.gate_surface.execution_attached is False


@pytest.mark.parametrize(
    "update",
    (
        {"classifierGatesRepresented": True},
        {"activeRequirementGatesRepresented": True},
        {"phase0ShadowOnly": False},
        {"metadataOnly": False},
        {"trafficAttached": True},
        {"executionAttached": True},
    ),
)
def test_gate_surface_cannot_imply_live_gate_enforcement(update: dict[str, object]) -> None:
    route = build_baseline_shadow_route(
        "What is 2 + 2?",
        scope=DeterministicRoutingScope(agentRole="general", runOn="main", spawnDepth=0),
    )
    gate_surface = route.gate_surface.__class__()

    with pytest.raises(ValidationError):
        gate_surface.model_copy(update=update)


def test_route_model_copy_revalidates_constructed_invalid_gate_surface() -> None:
    route = build_baseline_shadow_route(
        "What is 2 + 2?",
        scope=DeterministicRoutingScope(agentRole="general", runOn="main", spawnDepth=0),
    )
    invalid = route.gate_surface.__class__.model_construct(
        classifier_gates_represented=True,
        active_requirement_gates_represented=False,
        phase0_shadow_only=True,
        metadata_only=True,
        traffic_attached=False,
        execution_attached=False,
    )

    with pytest.raises(ValidationError, match="classifier gate"):
        route.model_copy(update={"gateSurface": invalid})


def test_public_construction_rejects_route_mismatches() -> None:
    route = build_baseline_shadow_route(
        "What is 2 + 2?",
        scope=DeterministicRoutingScope(agentRole="general", runOn="main", spawnDepth=0),
    )

    with pytest.raises(ValidationError, match="requiredToolNames"):
        DeterministicRoutePlanMetadata(
            mode=route.mode,
            reason=route.reason,
            scope=route.scope,
            classification=route.classification,
            requiredToolNames=("Search",),
            requiredEvidence=route.required_evidence,
            finalAnswerPolicy=route.final_answer_policy,
            retryInstruction=route.retry_instruction,
            baselineMeasurement=route.baseline_measurement,
        )


def test_public_route_rejects_classifier_mismatch_with_consistent_wrong_routed_metadata() -> None:
    route = build_baseline_shadow_route(
        "What is 2 + 2?",
        scope=DeterministicRoutingScope(agentRole="general", runOn="main", spawnDepth=0),
    )
    wrong_measurement = BaselineShadowMeasurementMetadata(
        wouldRoute=True,
        routedToolNames=("Search",),
        routedEvidenceTypes=("WebSearch",),
    )

    with pytest.raises(ValidationError, match="classification exactness"):
        DeterministicRoutePlanMetadata(
            mode=route.mode,
            reason=route.reason,
            scope=route.scope,
            classification=route.classification,
            requiredToolNames=("Search",),
            requiredEvidence=(RequiredEvidenceMetadata(evidenceType="WebSearch"),),
            finalAnswerPolicy=route.final_answer_policy,
            retryInstruction=route.retry_instruction.model_copy(
                update={"missingEvidenceTypes": ("WebSearch",)},
            ),
            baselineMeasurement=wrong_measurement,
        )


def test_model_copy_rejects_classifier_mismatch_with_consistent_wrong_routed_metadata() -> None:
    route = build_baseline_shadow_route(
        "What is 2 + 2?",
        scope=DeterministicRoutingScope(agentRole="general", runOn="main", spawnDepth=0),
    )
    wrong_measurement = BaselineShadowMeasurementMetadata(
        wouldRoute=True,
        routedToolNames=("Search",),
        routedEvidenceTypes=("WebSearch",),
    )

    with pytest.raises(ValidationError, match="classification exactness"):
        route.model_copy(
            update={
                "requiredToolNames": ("Search",),
                "requiredEvidence": (RequiredEvidenceMetadata(evidenceType="WebSearch"),),
                "retryInstruction": route.retry_instruction.model_copy(
                    update={"missingEvidenceTypes": ("WebSearch",)},
                ),
                "baselineMeasurement": wrong_measurement,
            },
        )


def test_public_no_exactness_route_rejects_non_empty_routed_metadata() -> None:
    route = build_baseline_shadow_route(
        "Say hello.",
        scope=DeterministicRoutingScope(agentRole="general", runOn="main", spawnDepth=0),
    )
    wrong_measurement = BaselineShadowMeasurementMetadata(
        wouldRoute=True,
        routedToolNames=("Search",),
        routedEvidenceTypes=("WebSearch",),
    )

    with pytest.raises(ValidationError, match="classification exactness"):
        DeterministicRoutePlanMetadata(
            mode=route.mode,
            reason=route.reason,
            scope=route.scope,
            classification=route.classification,
            requiredToolNames=("Search",),
            requiredEvidence=(RequiredEvidenceMetadata(evidenceType="WebSearch"),),
            finalAnswerPolicy=route.final_answer_policy,
            retryInstruction=route.retry_instruction,
            baselineMeasurement=wrong_measurement,
        )


def test_route_rejects_represented_required_evidence_status_in_phase_0() -> None:
    route = build_baseline_shadow_route(
        "What is 2 + 2?",
        scope=DeterministicRoutingScope(agentRole="general", runOn="main", spawnDepth=0),
    )

    with pytest.raises(ValidationError, match="required_missing"):
        route.model_copy(
            update={
                "requiredEvidence": (
                    RequiredEvidenceMetadata(evidenceType="Calculation", status="represented"),
                ),
            },
        )


def test_constructed_invalid_nested_measurement_is_revalidated_on_model_copy() -> None:
    route = build_baseline_shadow_route(
        "What is 2 + 2?",
        scope=DeterministicRoutingScope(agentRole="general", runOn="main", spawnDepth=0),
    )
    invalid = BaselineShadowMeasurementMetadata.model_construct(
        would_route=False,
        routed_tool_names=("Calculation",),
        routed_evidence_types=("Calculation",),
    )

    with pytest.raises(ValidationError, match="wouldRoute=False"):
        route.model_copy(update={"baselineMeasurement": invalid})


def test_main_child_role_and_spawn_depth_scope_is_preserved() -> None:
    scope = DeterministicRoutingScope(agentRole="coding", runOn="child", spawnDepth=3)

    route = build_baseline_shadow_route("Change a file.", scope=scope)

    assert route.scope == scope
    assert route.model_dump(by_alias=True)["scope"] == {
        "agentRole": "coding",
        "runOn": "child",
        "spawnDepth": 3,
    }
