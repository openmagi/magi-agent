from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from magi_agent.harness.inference_scaling import (
    ATTACHMENT_FLAGS,
    BestOfNEligibilityMetadata,
    InferenceScalingScope,
    ScalingPolicyDecision,
    ScalingPolicyInput,
    TelemetryMetadata,
    build_scaling_policy_decision,
)


def _scope(**updates: object) -> InferenceScalingScope:
    data = {"runOn": "main", "agentRole": "general", "spawnDepth": 0}
    data.update(updates)
    return InferenceScalingScope.model_validate(data)


def _raw_private_telemetry(secret: str = "raw-token") -> TelemetryMetadata:
    return TelemetryMetadata(
        sessionId="sess-1",
        turnId="turn-1",
        scope=_scope(),
        publicSummary="ok",
        metadata={
            "token": secret,
            "nested": {"service_role_key": f"{secret}-service-role"},
        },
    )


def _decision_with_budget_telemetry(telemetry: TelemetryMetadata) -> ScalingPolicyDecision:
    base = build_scaling_policy_decision(
        ScalingPolicyInput(
            taskKind="simple_arithmetic",
            riskLevel="low",
            scope=_scope(),
            verifierConfidence=0.9,
        )
    )
    return ScalingPolicyDecision(
        scope=base.scope,
        computeBudget=base.computeBudget,
        deterministicGate=base.deterministicGate,
        plannerEscalation=base.plannerEscalation,
        criticEscalation=base.criticEscalation,
        modelEscalation=base.modelEscalation,
        reasoningBudgetGate=base.reasoningBudgetGate,
        bestOfN=base.bestOfN,
        verifierConfidence=base.verifierConfidence,
        verifierRetry=base.verifierRetry,
        budgetTelemetry=telemetry,
        nonHardScalingOptedOut=base.nonHardScalingOptedOut,
        hardEvidenceRequirementsBypassable=base.hardEvidenceRequirementsBypassable,
    )


def test_simple_arithmetic_requires_deterministic_tool_before_extra_model_compute() -> None:
    decision = build_scaling_policy_decision(
        ScalingPolicyInput(
            taskKind="simple_arithmetic",
            riskLevel="low",
            scope=_scope(),
            verifierConfidence=0.92,
            availableEvidenceTypes=(),
            optOutNonHardScaling=True,
        )
    )

    assert decision.computeBudget.riskLevel == "low"
    assert decision.deterministicGate.required is True
    assert decision.deterministicGate.requiredEvidenceTypes == ("Calculation",)
    assert decision.deterministicGate.escalationBlockedUntilEvidence is True
    assert decision.criticEscalation.eligible is False
    assert decision.modelEscalation.eligible is False
    assert decision.reasoningBudgetGate.allowLargerReasoningBudget is False
    assert decision.nonHardScalingOptedOut is True
    assert decision.hardEvidenceRequirementsBypassable is False


def test_source_sensitive_research_prefers_source_inspection_before_escalation() -> None:
    decision = build_scaling_policy_decision(
        ScalingPolicyInput(
            taskKind="source_sensitive_research",
            riskLevel="medium",
            scope=_scope(agentRole="research"),
            verifierConfidence=0.64,
            availableEvidenceTypes=("WebSearch",),
        )
    )

    assert decision.deterministicGate.requiredEvidenceTypes == (
        "WebSearch",
        "SourceInspection",
        "ClaimLink",
    )
    assert decision.deterministicGate.missingEvidenceTypes == ("SourceInspection", "ClaimLink")
    assert decision.deterministicGate.escalationBlockedUntilEvidence is True
    assert decision.criticEscalation.eligible is False
    assert decision.modelEscalation.eligible is False


def test_coding_changes_prefer_tests_diagnostics_and_diff_review_before_synthesis_escalation() -> None:
    decision = build_scaling_policy_decision(
        ScalingPolicyInput(
            taskKind="coding_change",
            riskLevel="high",
            scope=_scope(agentRole="coding"),
            verifierConfidence=0.73,
            availableEvidenceTypes=("FileInspection", "GitDiff"),
        )
    )

    assert decision.deterministicGate.requiredEvidenceTypes == (
        "FileInspection",
        "GitDiff",
        "Diagnostics",
        "TestRun",
        "DiffReview",
    )
    assert decision.deterministicGate.missingEvidenceTypes == (
        "Diagnostics",
        "TestRun",
        "DiffReview",
    )
    assert decision.reasoningBudgetGate.allowLargerReasoningBudget is False
    assert decision.criticEscalation.eligible is False


def test_ambiguous_architecture_can_be_planner_and_critic_metadata_only() -> None:
    decision = build_scaling_policy_decision(
        ScalingPolicyInput(
            taskKind="ambiguous_architecture",
            riskLevel="medium",
            scope=_scope(agentRole="coding"),
            verifierConfidence=0.48,
            availableEvidenceTypes=(),
        )
    )

    assert decision.deterministicGate.required is False
    assert decision.plannerEscalation.eligible is True
    assert decision.criticEscalation.eligible is True
    assert decision.criticEscalation.reason == "ambiguity"
    assert decision.modelEscalation.eligible is False
    assert decision.criticEscalation.metadataOnly is True
    assert decision.plannerEscalation.metadataOnly is True


def test_repeated_verifier_failure_requires_changed_action_or_evidence_target() -> None:
    decision = build_scaling_policy_decision(
        ScalingPolicyInput(
            taskKind="verifier_retry",
            riskLevel="medium",
            scope=_scope(),
            verifierConfidence=0.3,
            repeatedVerifierFailure=True,
            changedActionOrEvidenceTarget=False,
        )
    )

    assert decision.verifierRetry.changedActionOrEvidenceTargetRequired is True
    assert decision.verifierRetry.blindResamplingAllowed is False
    assert decision.criticEscalation.eligible is False
    assert decision.bestOfN.eligible is False

    changed = decision.model_copy(update={"verifierRetry": decision.verifierRetry.model_copy(update={"changedActionOrEvidenceTarget": True})})
    assert changed.verifierRetry.changedActionOrEvidenceTarget is True


def test_low_verifier_confidence_can_request_critic_metadata_without_model_routing() -> None:
    decision = build_scaling_policy_decision(
        ScalingPolicyInput(
            taskKind="complex_synthesis",
            riskLevel="high",
            scope=_scope(agentRole="research"),
            verifierConfidence=0.35,
            availableEvidenceTypes=("SourceInspection", "ClaimLink"),
        )
    )

    assert decision.verifierConfidence.score == 0.35
    assert decision.criticEscalation.eligible is True
    assert decision.criticEscalation.reason == "low_verifier_confidence"
    assert decision.modelEscalation.eligible is True
    assert decision.modelEscalation.metadataOnly is True
    assert decision.routeAttached is False
    assert decision.modelRoutingAttached is False


def test_best_of_n_requires_reliable_verifier_ranking_and_safe_side_effects() -> None:
    eligible = BestOfNEligibilityMetadata(
        verifierCanRankOutcomes=True,
        sideEffectsSafe=True,
        sideEffectClass="none",
        maxVariants=3,
    )
    assert eligible.eligible is True
    assert eligible.defaultEnabled is False

    with pytest.raises(ValidationError, match="requires reliable verifier ranking"):
        BestOfNEligibilityMetadata(
            verifierCanRankOutcomes=False,
            sideEffectsSafe=True,
            sideEffectClass="none",
            maxVariants=3,
        )

    with pytest.raises(ValidationError, match="requires safe or no side effects"):
        BestOfNEligibilityMetadata(
            verifierCanRankOutcomes=True,
            sideEffectsSafe=False,
            sideEffectClass="external",
            maxVariants=3,
        )


def test_non_hard_scaling_opt_out_does_not_bypass_hard_evidence_requirements() -> None:
    decision = build_scaling_policy_decision(
        ScalingPolicyInput(
            taskKind="coding_change",
            riskLevel="high",
            scope=_scope(agentRole="coding"),
            verifierConfidence=0.2,
            availableEvidenceTypes=(),
            optOutNonHardScaling=True,
        )
    )

    assert decision.nonHardScalingOptedOut is True
    assert decision.criticEscalation.eligible is False
    assert decision.modelEscalation.eligible is False
    assert decision.deterministicGate.required is True
    assert decision.deterministicGate.escalationBlockedUntilEvidence is True
    assert decision.hardEvidenceRequirementsBypassable is False


def test_non_hard_scaling_opt_out_disables_best_of_n_but_not_hard_evidence_gate() -> None:
    decision = build_scaling_policy_decision(
        ScalingPolicyInput(
            taskKind="coding_change",
            riskLevel="high",
            scope=_scope(agentRole="coding"),
            verifierConfidence=0.91,
            availableEvidenceTypes=(),
            optOutNonHardScaling=True,
            verifierCanRankOutcomes=True,
            sideEffectsSafe=True,
            sideEffectClass="none",
            maxVariants=3,
        )
    )

    assert decision.nonHardScalingOptedOut is True
    assert decision.bestOfN.verifierCanRankOutcomes is True
    assert decision.bestOfN.sideEffectsSafe is True
    assert decision.bestOfN.eligible is False
    assert decision.deterministicGate.required is True
    assert decision.deterministicGate.escalationBlockedUntilEvidence is True
    assert decision.hardEvidenceRequirementsBypassable is False


def test_repeated_verifier_failure_without_changed_target_disables_best_of_n() -> None:
    decision = build_scaling_policy_decision(
        ScalingPolicyInput(
            taskKind="verifier_retry",
            riskLevel="medium",
            scope=_scope(),
            verifierConfidence=0.3,
            repeatedVerifierFailure=True,
            changedActionOrEvidenceTarget=False,
            verifierCanRankOutcomes=True,
            sideEffectsSafe=True,
            sideEffectClass="read_only",
            maxVariants=3,
        )
    )

    assert decision.verifierRetry.changedActionOrEvidenceTargetRequired is True
    assert decision.verifierRetry.blindResamplingAllowed is False
    assert decision.bestOfN.eligible is False


def test_hidden_reasoning_fields_are_rejected_or_excluded_from_public_telemetry() -> None:
    with pytest.raises(ValidationError, match="hidden reasoning"):
        TelemetryMetadata(
            sessionId="sess-1",
            turnId="turn-1",
            scope=_scope(),
            publicSummary="ok",
            metadata={"hiddenReasoning": "do not expose"},
        )

    telemetry = TelemetryMetadata(
        sessionId="sess-1",
        turnId="turn-1",
        scope=_scope(),
        publicSummary="token=secret-token-1234567890 " + ("x" * 400),
        metadata={"safe": ["value"]},
    )

    dumped = telemetry.model_dump(by_alias=True)
    assert "hiddenReasoning" not in str(dumped)
    assert "secret-token-1234567890" not in dumped["publicSummary"]
    assert len(dumped["publicSummary"]) <= 200
    with pytest.raises(TypeError):
        telemetry.metadata["safe"].append("mutation")


def test_telemetry_metadata_redacts_nested_secret_keys_and_free_text_values() -> None:
    telemetry = TelemetryMetadata(
        sessionId="sess-1",
        turnId="turn-1",
        scope=_scope(),
        publicSummary="ok",
        metadata={
            "request": {
                "authorization": "Bearer raw-authorization-token",
                "proxy_authorization": "Basic raw-proxy-secret",
                "cookie": "sid=raw-cookie-secret",
                "accessToken": "raw-access-token",
                "nested": [
                    {"token": "raw-nested-token"},
                    {"api_key": "raw-api-key"},
                    {"github_oauth": "raw-github-oauth"},
                    "password=raw-free-text-password keep shape",
                ],
            }
        },
    )

    dumped = telemetry.model_dump(by_alias=True)
    public = dumped["metadata"]

    assert public["request"]["authorization"] == "[REDACTED]"
    assert public["request"]["proxy_authorization"] == "[REDACTED]"
    assert public["request"]["cookie"] == "[REDACTED]"
    assert public["request"]["accessToken"] == "[REDACTED]"
    assert public["request"]["nested"][0]["token"] == "[REDACTED]"
    assert public["request"]["nested"][1]["api_key"] == "[REDACTED]"
    assert public["request"]["nested"][2]["github_oauth"] == "[REDACTED]"
    assert "raw-free-text-password" not in public["request"]["nested"][3]
    for raw_secret in (
        "raw-authorization-token",
        "raw-proxy-secret",
        "raw-cookie-secret",
        "raw-access-token",
        "raw-nested-token",
        "raw-api-key",
        "raw-github-oauth",
        "raw-free-text-password",
    ):
        assert raw_secret not in str(dumped)


def test_public_summary_redacts_common_secret_patterns() -> None:
    telemetry = TelemetryMetadata(
        sessionId="sess-1",
        turnId="turn-1",
        scope=_scope(),
        publicSummary=(
            "Bearer raw-bearer-token Basic cmF3LWJhc2lj token=raw-token "
            "api_key=raw-api-key github_oauth=raw-github-oauth cookie=raw-cookie "
            "proxy_authorization=raw-proxy session_token=raw-session accessToken=raw-access "
            "refreshToken=raw-refresh"
        ),
        metadata={},
    )

    dumped_summary = telemetry.model_dump(by_alias=True)["publicSummary"]
    for raw_secret in (
        "raw-bearer-token",
        "cmF3LWJhc2lj",
        "raw-token",
        "raw-api-key",
        "raw-github-oauth",
        "raw-cookie",
        "raw-proxy",
        "raw-session",
        "raw-access",
        "raw-refresh",
    ):
        assert raw_secret not in dumped_summary


def test_public_summary_redacts_colon_json_yaml_and_provider_key_patterns() -> None:
    telemetry = TelemetryMetadata(
        sessionId="sess-1",
        turnId="turn-1",
        scope=_scope(),
        publicSummary=(
            'token: raw-token "token":"raw-token" sk-1234567890abcdef '
            "service_role_key=raw-service-role private_key=raw-private-key"
        ),
        metadata={
            "summary": (
                'token: raw-token "token":"raw-token" sk-1234567890abcdef '
                "service_role_key=raw-service-role private_key=raw-private-key"
            )
        },
    )

    dumped = telemetry.model_dump(by_alias=True)
    serialized = json.dumps(dumped, sort_keys=True)

    for raw_secret in (
        "raw-token",
        "sk-1234567890abcdef",
        "raw-service-role",
        "raw-private-key",
    ):
        assert raw_secret not in serialized


def test_telemetry_model_copy_preserves_canonical_metadata_while_serializing_redacted() -> None:
    telemetry = TelemetryMetadata(
        sessionId="sess-1",
        turnId="turn-1",
        scope=_scope(),
        publicSummary="ok",
        metadata={
            "token": "raw-token",
            "nested": {"service_role_key": "raw-service-role"},
        },
    )

    copied = telemetry.model_copy()

    assert copied.metadata["token"] == "raw-token"
    assert copied.metadata["nested"]["service_role_key"] == "raw-service-role"
    dumped = copied.model_dump(by_alias=True)
    serialized = json.dumps(dumped, sort_keys=True)
    assert dumped["metadata"]["token"] == "[REDACTED]"
    assert dumped["metadata"]["nested"]["service_role_key"] == "[REDACTED]"
    assert "raw-token" not in serialized
    assert "raw-service-role" not in serialized


def test_telemetry_model_copy_revalidates_metadata_updates_and_attachment_flags() -> None:
    telemetry = TelemetryMetadata(
        sessionId="sess-1",
        turnId="turn-1",
        scope=_scope(),
        publicSummary="ok",
        metadata={"token": "raw-token"},
    )

    updated = telemetry.model_copy(update={"metadata": {"private_key": "raw-private-key"}})

    assert updated.metadata["private_key"] == "raw-private-key"
    assert updated.model_dump(by_alias=True)["metadata"]["private_key"] == "[REDACTED]"
    with pytest.raises(TypeError):
        updated.metadata["items"] = []
    # C-4 PR-G2 (raise-to-coerce): a forged Literal[False] attachment flag is
    # now coerced to False uniformly instead of raising a ValidationError. The
    # end-result invariant is preserved: the value still reads False.
    coerced = telemetry.model_copy(update={"trafficAttached": True})
    assert coerced.traffic_attached is False
    with pytest.raises(ValidationError, match="hidden reasoning"):
        telemetry.model_copy(update={"metadata": {"hiddenReasoning": "private"}})


def test_scaling_policy_decision_preserves_canonical_budget_telemetry_while_serializing_redacted() -> None:
    telemetry = _raw_private_telemetry()

    decision = _decision_with_budget_telemetry(telemetry)

    assert decision.budgetTelemetry.metadata["token"] == "raw-token"
    assert decision.budgetTelemetry.metadata["nested"]["service_role_key"] == "raw-token-service-role"
    dumped = decision.model_dump(by_alias=True)
    serialized = json.dumps(dumped, sort_keys=True)
    assert dumped["budgetTelemetry"]["metadata"]["token"] == "[REDACTED]"
    assert dumped["budgetTelemetry"]["metadata"]["nested"]["service_role_key"] == "[REDACTED]"
    assert "raw-token" not in serialized
    assert "raw-token-service-role" not in serialized


def test_scaling_policy_decision_model_copy_preserves_canonical_budget_telemetry() -> None:
    decision = _decision_with_budget_telemetry(_raw_private_telemetry())

    copied = decision.model_copy()

    assert copied.budgetTelemetry.metadata["token"] == "raw-token"
    assert copied.budgetTelemetry.metadata["nested"]["service_role_key"] == "raw-token-service-role"
    dumped = copied.model_dump(by_alias=True)
    serialized = json.dumps(dumped, sort_keys=True)
    assert dumped["budgetTelemetry"]["metadata"]["token"] == "[REDACTED]"
    assert dumped["budgetTelemetry"]["metadata"]["nested"]["service_role_key"] == "[REDACTED]"
    assert "raw-token" not in serialized
    assert "raw-token-service-role" not in serialized


def test_scaling_policy_decision_model_copy_update_preserves_canonical_budget_telemetry() -> None:
    decision = _decision_with_budget_telemetry(_raw_private_telemetry("original-token"))

    updated = decision.model_copy(update={"budgetTelemetry": _raw_private_telemetry("updated-token")})

    assert updated.budgetTelemetry.metadata["token"] == "updated-token"
    assert updated.budgetTelemetry.metadata["nested"]["service_role_key"] == "updated-token-service-role"
    dumped = updated.model_dump(by_alias=True)
    serialized = json.dumps(dumped, sort_keys=True)
    assert dumped["budgetTelemetry"]["metadata"]["token"] == "[REDACTED]"
    assert dumped["budgetTelemetry"]["metadata"]["nested"]["service_role_key"] == "[REDACTED]"
    assert "updated-token" not in serialized
    assert "updated-token-service-role" not in serialized


def test_scaling_policy_decision_model_copy_revalidates_parent_and_nested_attachment_updates() -> None:
    """C-4 PR-G2 (raise-to-coerce): a forged ``Literal[False]`` attachment
    flag is now coerced to False uniformly across construct/copy/validate
    instead of raising a ValidationError. The end-result invariant is
    preserved: the value still reads False on the resulting decision (and
    on the nested telemetry once it round-trips through validate).
    """
    decision = _decision_with_budget_telemetry(_raw_private_telemetry())

    coerced = decision.model_copy(update={"trafficAttached": True})
    assert coerced.traffic_attached is False

    nested = TelemetryMetadata(
        sessionId="sess-1",
        turnId="turn-1",
        scope=_scope(),
        publicSummary="ok",
        metadata={"token": "raw-token"},
        trafficAttached=True,
    )
    # The nested telemetry was already coerced on its own construction.
    assert nested.traffic_attached is False
    coerced_nested = decision.model_copy(update={"budgetTelemetry": nested})
    assert coerced_nested.budgetTelemetry.traffic_attached is False


def test_telemetry_and_decisions_are_frozen_json_like_and_attachment_flags_are_forced_false() -> None:
    decision = build_scaling_policy_decision(
        ScalingPolicyInput(
            taskKind="simple_arithmetic",
            riskLevel="low",
            scope=_scope(),
            verifierConfidence=0.9,
        )
    )

    assert set(ATTACHMENT_FLAGS) == {
        "trafficAttached",
        "executionAttached",
        "runnerAttached",
        "routeAttached",
        "modelRoutingAttached",
        "billingAttached",
        "authAttached",
        "apiProxyAttached",
        "canaryAttached",
    }
    assert all(decision.model_dump(by_alias=True)[flag] is False for flag in ATTACHMENT_FLAGS)

    # C-4 PR-G2 (raise-to-coerce): forged Literal[False] attachment flags
    # are now coerced uniformly instead of raising on model_copy(update=...).
    # The end-result invariant is preserved: the value still reads False.
    coerced_traffic = decision.model_copy(update={"trafficAttached": True})
    assert coerced_traffic.traffic_attached is False
    coerced_routing = decision.model_copy(update={"modelRoutingAttached": True})
    assert coerced_routing.model_routing_attached is False
    with pytest.raises(ValidationError):
        ScalingPolicyInput(
            taskKind="simple_arithmetic",
            riskLevel="low",
            scope=_scope(),
            verifierConfidence=0.9,
            hiddenReasoning="private",
        )


def test_scope_metadata_validates_main_child_role_and_spawn_depth() -> None:
    assert _scope().run_on == "main"
    assert _scope(runOn="child", agentRole="research", spawnDepth=2).spawn_depth == 2

    with pytest.raises(ValidationError, match="main runs must use spawnDepth=0"):
        _scope(runOn="main", spawnDepth=1)
    with pytest.raises(ValidationError, match="child runs must use spawnDepth greater than 0"):
        _scope(runOn="child", spawnDepth=0)
