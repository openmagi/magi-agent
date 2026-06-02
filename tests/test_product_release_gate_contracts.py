from __future__ import annotations

import importlib
import json
import subprocess
import sys
import warnings
from pathlib import Path

import pytest
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import ValidationError
from pydantic_core import PydanticSerializationError

from magi_agent.evals.release_gates import (
    ADK_EVALUATION_BOUNDARY,
    CanaryProofRef,
    DigestOnlyProjection,
    EvalObservationSet,
    EvalThresholds,
    HardInvariantEvaluation,
    OwnerApprovalRef,
    PromotionGateRecord,
    PluginSandboxObservation,
    PromotionRequest,
    PromotionResult,
    ReleaseGateAuthorityFlags,
    RollbackRef,
    SelectorGateDecision,
    evaluate_promotion_request,
)


PYTHON_ROOT = Path(__file__).parents[1]
MODULE_PATH = PYTHON_ROOT / "magi_agent" / "evals" / "release_gates.py"


def _thresholds() -> EvalThresholds:
    return EvalThresholds(
        maxCostMicros=100_000,
        maxToolInvocations=12,
        minEvalScore=0.98,
        maxEvalFailureRate=0.01,
        thresholdPolicyDigest=f"sha256:{_digest('7')}",
        verified=True,
    )


def _observations() -> EvalObservationSet:
    return EvalObservationSet(
        costMicros=91_000,
        toolInvocations=8,
        evalScore=0.995,
        evalFailureRate=0.0,
    )


def _digest(suffix: str = "a") -> str:
    return suffix * 64


def _projection() -> DigestOnlyProjection:
    return DigestOnlyProjection(
        projectionDigest=f"sha256:{_digest('a')}",
        policyDigest=f"sha256:{_digest('b')}",
        decisionDigest=f"sha256:{_digest('c')}",
        sourceSnapshotDigest=f"sha256:{_digest('d')}",
        publicMetadata={"suiteRef": "release-gate-suite-0001"},
    )


def _selector_decision() -> SelectorGateDecision:
    return SelectorGateDecision(
        selectorRef="selector:governed-release-route",
        selectedRef="recipe:governed-release-candidate",
        expectedGoverned=True,
        actualGoverned=True,
        usedFallback=False,
        governedPolicyDigest=f"sha256:{_digest('e')}",
    )


def _rollback_ref() -> RollbackRef:
    return RollbackRef(
        rollbackRef="rollback:previous-stable",
        rollbackPlanDigest=f"sha256:{_digest('f')}",
        previousSnapshotDigest=f"sha256:{_digest('1')}",
        verified=True,
    )


def _owner_approval_ref() -> OwnerApprovalRef:
    return OwnerApprovalRef(
        approvalRef="approval:owner-release-0001",
        ownerRef="owner:release-admin",
        approvalDigest=f"sha256:{_digest('2')}",
        approved=True,
        verified=True,
        bypassDetected=False,
    )


def _canary_proof_ref() -> CanaryProofRef:
    return CanaryProofRef(
        proofRef="canary:shadow-suite-0001",
        proofDigest=f"sha256:{_digest('3')}",
        verified=True,
    )


def _plugin_observation(overreach: bool = False) -> PluginSandboxObservation:
    return PluginSandboxObservation(
        pluginRef="plugin:trusted-metadata-only",
        sandboxPolicyDigest=f"sha256:{_digest('4')}",
        overreachDetected=overreach,
    )


def _hard_invariant(mode: str = "block", downgraded: bool = False) -> HardInvariantEvaluation:
    return HardInvariantEvaluation(
        invariantRef="invariant:governed-selector-required",
        configuredMode=mode,
        downgraded=downgraded,
    )


def _request(**overrides: object) -> PromotionRequest:
    payload: dict[str, object] = {
        "promotionId": "promotion-release-gate-0001",
        "candidateSnapshotDigest": f"sha256:{_digest('5')}",
        "targetStage": "canary",
        "thresholds": _thresholds(),
        "observations": _observations(),
        "projection": _projection(),
        "selectorDecision": _selector_decision(),
        "canaryProofRefs": (_canary_proof_ref(),),
        "rollbackRef": _rollback_ref(),
        "ownerApprovalRefs": (_owner_approval_ref(),),
        "pluginSandboxObservations": (_plugin_observation(),),
        "hardInvariantEvaluations": (_hard_invariant(),),
        "rawProjectionLeakDetected": False,
    }
    payload.update(overrides)
    return PromotionRequest(**payload)


def _forged_contract_instance(
    model_type: type[BaseModel],
    base: BaseModel,
    **overrides: object,
) -> BaseModel:
    class ForgedContract(model_type):  # type: ignore[misc, valid-type]
        model_config = ConfigDict(extra="allow")

        raw_output: str | None = None

    payload = base.model_dump(by_alias=False, mode="python")
    payload.update(overrides)
    payload["raw_output"] = "raw prompt bearer token /Users/kevin/.env"
    return BaseModel.model_construct.__func__(ForgedContract, **payload)


def _assert_no_review_secret_leak(value: object) -> None:
    encoded = json.dumps(value, default=str, sort_keys=True).lower()
    assert "raw prompt bearer token" not in encoded
    assert "raw output bearer token" not in encoded
    assert "/users/kevin/.env" not in encoded


class _RawObject:
    def __repr__(self) -> str:
        return "raw prompt bearer token /Users/kevin/.env"

    def __str__(self) -> str:
        return "raw output bearer token /Users/kevin/.env"


def test_promotion_pass_path_emits_digest_only_release_gate_result() -> None:
    result = evaluate_promotion_request(_request())
    payload = result.model_dump(by_alias=True, mode="json")
    record = PromotionGateRecord(
        request=_request(),
        result=result,
        recordDigest=f"sha256:{_digest('6')}",
    )

    assert result.allowed is True
    assert result.reason_codes == ()
    assert record.schema_version == "promotionGateRecord.v1"
    assert payload["projection"]["projectionDigest"] == f"sha256:{_digest('a')}"
    assert payload["projection"]["publicMetadata"] == {"suiteRef": "release-gate-suite-0001"}
    assert set(payload["authorityFlags"].values()) == {False}
    assert payload["adkEvaluationBoundary"] == ADK_EVALUATION_BOUNDARY
    encoded = json.dumps(payload, sort_keys=True).lower()
    for forbidden in (
        "raw prompt",
        "raw output",
        "hidden reasoning",
        "authorization",
        "cookie",
        "session key",
        "credential",
        "/users/",
        "/private/",
        ".env",
    ):
        assert forbidden not in encoded


@pytest.mark.parametrize(
    ("override", "reason_code"),
    (
        (
            {"rawProjectionLeakDetected": lambda: True},
            "raw_projection_leak",
        ),
        (
            {"selectorDecision": lambda: _selector_decision().model_copy(update={"usedFallback": True})},
            "selector_fallback",
        ),
        (
            {"selectorDecision": lambda: _selector_decision().model_copy(update={"actualGoverned": False})},
            "selector_governed_mismatch",
        ),
        (
            {"ownerApprovalRefs": lambda: (_owner_approval_ref().model_copy(update={"bypassDetected": True}),)},
            "approval_bypass",
        ),
        (
            {"pluginSandboxObservations": lambda: (_plugin_observation(overreach=True),)},
            "plugin_sandbox_overreach",
        ),
        (
            {"hardInvariantEvaluations": lambda: (_hard_invariant(mode="log_only"),)},
            "hard_invariant_downgrade",
        ),
        (
            {"hardInvariantEvaluations": lambda: (_hard_invariant(mode="disabled"),)},
            "hard_invariant_downgrade",
        ),
        (
            {"hardInvariantEvaluations": lambda: (_hard_invariant(downgraded=True),)},
            "hard_invariant_downgrade",
        ),
        (
            {"rollbackRef": lambda: None},
            "missing_rollback_ref",
        ),
        (
            {"ownerApprovalRefs": lambda: ()},
            "missing_owner_approval_ref",
        ),
        (
            {"ownerApprovalRefs": lambda: (_owner_approval_ref().model_copy(update={"verified": False}),)},
            "missing_owner_approval_ref",
        ),
        (
            {"canaryProofRefs": lambda: ()},
            "missing_canary_proof_ref",
        ),
        (
            {"observations": lambda: _observations().model_copy(update={"costMicros": 100_001})},
            "cost_threshold_exceeded",
        ),
        (
            {"observations": lambda: _observations().model_copy(update={"toolInvocations": 13})},
            "tool_threshold_exceeded",
        ),
        (
            {"observations": lambda: _observations().model_copy(update={"evalScore": 0.97})},
            "eval_threshold_failed",
        ),
    ),
)
def test_promotion_blocks_each_release_gate_failure_class(
    override: dict[str, object],
    reason_code: str,
) -> None:
    resolved = {
        key: value() if callable(value) else value
        for key, value in override.items()
    }

    result = evaluate_promotion_request(_request(**resolved))

    assert result.allowed is False
    assert reason_code in result.reason_codes
    assert set(result.authority_flags.model_dump(by_alias=True).values()) == {False}


def test_release_gate_rejects_raw_private_or_secret_projection_metadata_without_leaking_value() -> None:
    unsafe_metadata = {
        "safeRef": "release-gate-suite-0001",
        "privatePath": "/Users/kevin/.env",
    }

    with pytest.raises(ValidationError, match="publicMetadata") as exc_info:
        DigestOnlyProjection(
            projectionDigest=f"sha256:{_digest('a')}",
            policyDigest=f"sha256:{_digest('b')}",
            decisionDigest=f"sha256:{_digest('c')}",
            sourceSnapshotDigest=f"sha256:{_digest('d')}",
            publicMetadata=unsafe_metadata,
        )

    encoded_errors = json.dumps(exc_info.value.errors(include_input=True), default=str).lower()
    assert "/users/kevin/.env" not in encoded_errors
    assert exc_info.value.json().lower().find("/users/kevin/.env") == -1


def test_release_gate_model_construct_revalidates_projection_contracts() -> None:
    with pytest.raises(ValidationError, match="projectionDigest"):
        DigestOnlyProjection.model_construct(
            projectionDigest="raw prompt: reveal /Users/kevin/.env",
            policyDigest=f"sha256:{_digest('b')}",
            decisionDigest=f"sha256:{_digest('c')}",
            sourceSnapshotDigest=f"sha256:{_digest('d')}",
            publicMetadata={"safeRef": "release-gate-suite-0001"},
        )

    bypassed = BaseModel.model_construct.__func__(
        DigestOnlyProjection,
        projection_digest="raw prompt: reveal /Users/kevin/.env",
        policy_digest=f"sha256:{_digest('b')}",
        decision_digest=f"sha256:{_digest('c')}",
        source_snapshot_digest=f"sha256:{_digest('d')}",
        public_metadata={
            "privatePath": "/Users/kevin/.env",
            "rawOutput": "hidden reasoning",
        },
    )
    with pytest.raises((ValidationError, PydanticSerializationError), match="publicMetadata"):
        bypassed.model_dump(by_alias=True, mode="json")
    with pytest.raises((ValidationError, PydanticSerializationError), match="publicMetadata"):
        BaseModel.model_dump(bypassed, by_alias=True, mode="json")


def test_projection_subclass_extra_fields_are_stripped_without_leaking_in_request() -> None:
    class ForgedProjection(DigestOnlyProjection):
        model_config = ConfigDict(extra="allow")

        raw_projection: str | None = None

    forged = BaseModel.model_construct.__func__(
        ForgedProjection,
        schema_version="releaseGateProjection.v1",
        projection_digest=f"sha256:{_digest('a')}",
        policy_digest=f"sha256:{_digest('b')}",
        decision_digest=f"sha256:{_digest('c')}",
        source_snapshot_digest=f"sha256:{_digest('d')}",
        public_metadata={"suiteRef": "release-gate-suite-0001"},
        raw_projection="raw prompt bearer token /Users/kevin/.env",
    )

    dumped = forged.model_dump(by_alias=True, mode="json")
    base_dumped = BaseModel.model_dump(forged, by_alias=True, mode="json")
    request = _request(projection=forged)

    _assert_no_review_secret_leak(dumped)
    _assert_no_review_secret_leak(base_dumped)
    _assert_no_review_secret_leak(request.model_dump(by_alias=True, mode="json"))
    assert "raw_projection" not in dumped
    assert "raw_projection" not in base_dumped


def test_promotion_result_rejects_nonconstant_adk_boundary_without_leaking_value() -> None:
    unsafe_boundary = {
        "adkEvaluationImported": True,
        "modelCalled": True,
        "secretPath": "/Users/kevin/.env",
    }

    with pytest.raises(ValidationError, match="adkEvaluationBoundary") as exc_info:
        PromotionResult(
            promotionId="promotion-release-gate-0001",
            allowed=True,
            reasonCodes=(),
            projection=_projection(),
            authorityFlags=ReleaseGateAuthorityFlags(),
            adkEvaluationBoundary=unsafe_boundary,
        )

    encoded_errors = json.dumps(exc_info.value.errors(include_input=True), default=str).lower()
    assert "/users/kevin/.env" not in encoded_errors
    assert "secretpath" not in encoded_errors
    assert "modelcalled" not in encoded_errors

    bypassed = BaseModel.model_construct.__func__(
        PromotionResult,
        promotion_id="promotion-release-gate-0001",
        allowed=True,
        reason_codes=(),
        projection=_projection(),
        authority_flags=ReleaseGateAuthorityFlags(),
        adk_evaluation_boundary=unsafe_boundary,
    )

    with pytest.raises((ValidationError, PydanticSerializationError)) as dumped_exc:
        bypassed.model_dump(by_alias=True, mode="json")
    with pytest.raises((ValidationError, PydanticSerializationError)) as base_dumped_exc:
        BaseModel.model_dump(bypassed, by_alias=True, mode="json")

    _assert_no_review_secret_leak(dumped_exc.value.errors(include_input=True))
    _assert_no_review_secret_leak(str(base_dumped_exc.value))


def test_promotion_result_and_record_redact_forged_projection_subclasses() -> None:
    class ForgedProjection(DigestOnlyProjection):
        model_config = ConfigDict(extra="allow")

        raw_projection: str | None = None

    forged = BaseModel.model_construct.__func__(
        ForgedProjection,
        schema_version="releaseGateProjection.v1",
        projection_digest=f"sha256:{_digest('a')}",
        policy_digest=f"sha256:{_digest('b')}",
        decision_digest=f"sha256:{_digest('c')}",
        source_snapshot_digest=f"sha256:{_digest('d')}",
        public_metadata={"suiteRef": "release-gate-suite-0001"},
        raw_projection="raw output bearer token /Users/kevin/.env",
    )

    result_payload = {
        "promotionId": "promotion-release-gate-0001",
        "allowed": True,
        "reasonCodes": (),
        "projection": forged,
        "authorityFlags": ReleaseGateAuthorityFlags(),
        "adkEvaluationBoundary": dict(ADK_EVALUATION_BOUNDARY),
    }

    result = PromotionResult(**result_payload)
    result_errors = result.model_dump(by_alias=True, mode="json")
    assert "raw_projection" not in result_errors["projection"]
    _assert_no_review_secret_leak(result_errors)

    record = PromotionGateRecord(
        request=_request(),
        result=evaluate_promotion_request(_request()).model_dump(by_alias=True, mode="python") | {"projection": forged},
        recordDigest=f"sha256:{_digest('6')}",
    )
    record_errors = record.model_dump(by_alias=True, mode="json")
    _assert_no_review_secret_leak(record_errors)


def test_promotion_request_normalizes_nested_contract_subclasses_without_leaking_extras() -> None:
    nested_cases: tuple[tuple[str, object], ...] = (
        ("thresholds", _forged_contract_instance(EvalThresholds, _thresholds())),
        ("observations", _forged_contract_instance(EvalObservationSet, _observations())),
        ("selectorDecision", _forged_contract_instance(SelectorGateDecision, _selector_decision())),
        ("canaryProofRefs", (_forged_contract_instance(CanaryProofRef, _canary_proof_ref()),)),
        ("rollbackRef", _forged_contract_instance(RollbackRef, _rollback_ref())),
        ("ownerApprovalRefs", (_forged_contract_instance(OwnerApprovalRef, _owner_approval_ref()),)),
        ("pluginSandboxObservations", (_forged_contract_instance(PluginSandboxObservation, _plugin_observation()),)),
        ("hardInvariantEvaluations", (_forged_contract_instance(HardInvariantEvaluation, _hard_invariant()),)),
        ("authorityFlags", _forged_contract_instance(ReleaseGateAuthorityFlags, ReleaseGateAuthorityFlags())),
    )

    for field_name, forged_value in nested_cases:
        request = _request(**{field_name: forged_value})
        result = evaluate_promotion_request(request)

        _assert_no_review_secret_leak(result.model_dump(by_alias=True, mode="json"))
        assert set(result.authority_flags.model_dump(by_alias=True).values()) == {False}


def test_top_level_release_gate_subclasses_normalize_without_raw_extras() -> None:
    forged_request = _forged_contract_instance(PromotionRequest, _request())
    forged_result = _forged_contract_instance(PromotionResult, evaluate_promotion_request(_request()))
    record = PromotionGateRecord(
        request=forged_request,
        result=forged_result,
        recordDigest=f"sha256:{_digest('6')}",
    )

    result = evaluate_promotion_request(forged_request)

    _assert_no_review_secret_leak(result.model_dump(by_alias=True, mode="json"))
    _assert_no_review_secret_leak(record.model_dump(by_alias=True, mode="json"))


def test_release_gate_subclass_validation_errors_sanitize_declared_field_values() -> None:
    forged_observations = _forged_contract_instance(
        EvalObservationSet,
        _observations(),
        cost_micros="raw output bearer token /Users/kevin/.env",
    )
    forged_request = _forged_contract_instance(
        PromotionRequest,
        _request(),
        candidate_snapshot_digest="raw prompt bearer token /Users/kevin/.env",
    )

    with pytest.raises(ValidationError) as observations_exc:
        EvalObservationSet.model_validate(forged_observations)
    with pytest.raises(ValidationError) as request_exc:
        evaluate_promotion_request(forged_request)

    _assert_no_review_secret_leak(observations_exc.value.errors(include_input=True))
    _assert_no_review_secret_leak(request_exc.value.errors(include_input=True))


def test_direct_subclass_constructor_and_validate_sanitize_declared_scalars_and_extras() -> None:
    class ForgedObservationSet(EvalObservationSet):
        model_config = ConfigDict(extra="allow")

        raw_output: str | None = None

    with pytest.raises(ValidationError) as constructor_exc:
        ForgedObservationSet(
            costMicros="raw output bearer token /Users/kevin/.env",
            toolInvocations=8,
            evalScore=0.995,
            evalFailureRate=0.0,
            rawOutput="raw prompt bearer token /Users/kevin/.env",
        )
    with pytest.raises(ValidationError) as validate_exc:
        ForgedObservationSet.model_validate(
            {
                "costMicros": "raw output bearer token /Users/kevin/.env",
                "toolInvocations": 8,
                "evalScore": 0.995,
                "evalFailureRate": 0.0,
                "rawOutput": "raw prompt bearer token /Users/kevin/.env",
            },
        )

    _assert_no_review_secret_leak(constructor_exc.value.errors(include_input=True))
    _assert_no_review_secret_leak(validate_exc.value.errors(include_input=True))


def test_release_gate_mapping_scalar_errors_sanitize_sensitive_input() -> None:
    scalar_cases: tuple[tuple[type[BaseModel], dict[str, object]], ...] = (
        (
            EvalThresholds,
            _thresholds().model_dump(by_alias=True, mode="python")
            | {"maxCostMicros": "raw output bearer token /Users/kevin/.env"},
        ),
        (
            EvalObservationSet,
            _observations().model_dump(by_alias=True, mode="python")
            | {"evalScore": "hiddenReasoning /Users/kevin/.env"},
        ),
        (
            SelectorGateDecision,
            _selector_decision().model_dump(by_alias=True, mode="python")
            | {"expectedGoverned": "authHeader /Users/kevin/.env"},
        ),
        (
            OwnerApprovalRef,
            _owner_approval_ref().model_dump(by_alias=True, mode="python")
            | {"approved": "apiKey /Users/kevin/.env"},
        ),
        (
            HardInvariantEvaluation,
            _hard_invariant().model_dump(by_alias=True, mode="python")
            | {"configuredMode": "toolOutput /Users/kevin/.env"},
        ),
        (
            ReleaseGateAuthorityFlags,
            {"modelCalled": "raw prompt bearer token /Users/kevin/.env"},
        ),
    )

    for model_type, payload in scalar_cases:
        with pytest.raises(ValidationError) as exc_info:
            model_type(**payload)
        _assert_no_review_secret_leak(exc_info.value.errors(include_input=True))


def test_release_gate_unknown_scalar_objects_are_replaced_before_errors() -> None:
    object_cases: tuple[tuple[type[BaseModel], dict[str, object]], ...] = (
        (
            EvalThresholds,
            _thresholds().model_dump(by_alias=True, mode="python")
            | {"maxCostMicros": _RawObject()},
        ),
        (
            EvalObservationSet,
            _observations().model_dump(by_alias=True, mode="python")
            | {"costMicros": _RawObject()},
        ),
        (
            SelectorGateDecision,
            _selector_decision().model_dump(by_alias=True, mode="python")
            | {"expectedGoverned": _RawObject()},
        ),
        (
            OwnerApprovalRef,
            _owner_approval_ref().model_dump(by_alias=True, mode="python")
            | {"approved": _RawObject()},
        ),
        (
            ReleaseGateAuthorityFlags,
            {"modelCalled": _RawObject()},
        ),
    )

    for model_type, payload in object_cases:
        with pytest.raises(ValidationError) as exc_info:
            model_type(**payload)
        _assert_no_review_secret_leak(exc_info.value.errors(include_input=True))


def test_release_gate_top_level_model_validate_sanitizes_hostile_objects() -> None:
    model_types: tuple[type[BaseModel], ...] = (
        DigestOnlyProjection,
        PromotionRequest,
        PromotionResult,
        PromotionGateRecord,
    )

    for model_type in model_types:
        with pytest.raises(ValidationError) as exc_info:
            model_type.model_validate(_RawObject())
        _assert_no_review_secret_leak(exc_info.value.errors(include_input=True))


def test_release_gate_top_level_model_validate_sanitizes_hostile_strings() -> None:
    for model_type in (DigestOnlyProjection, PromotionRequest, PromotionResult, PromotionGateRecord):
        with pytest.raises(ValidationError) as exc_info:
            model_type.model_validate("raw prompt bearer token /Users/kevin/.env")
        _assert_no_review_secret_leak(exc_info.value.errors(include_input=True))


def test_release_gate_secret_shaped_strings_are_sanitized_before_scalar_errors() -> None:
    with pytest.raises(ValidationError) as exc_info:
        EvalObservationSet(
            costMicros="sk-live-abc123456789",
            toolInvocations=8,
            evalScore=0.995,
            evalFailureRate=0.0,
        )

    encoded_errors = json.dumps(exc_info.value.errors(include_input=True), default=str).lower()
    assert "sk-live-abc123456789" not in encoded_errors


def test_release_gate_extra_nested_mapping_keys_are_sanitized_before_errors() -> None:
    observation = EvalObservationSet(
        costMicros=91_000,
        toolInvocations=8,
        evalScore=0.995,
        evalFailureRate=0.0,
        extraPayload={"/Users/kevin/.env": "raw prompt bearer token"},
    )

    _assert_no_review_secret_leak(observation.model_dump(by_alias=True, mode="json"))


def test_release_gate_model_copy_errors_sanitize_sensitive_update_values() -> None:
    with pytest.raises(ValidationError) as exc_info:
        _observations().model_copy(
            update={"costMicros": "raw output bearer token /Users/kevin/.env"},
        )

    _assert_no_review_secret_leak(exc_info.value.errors(include_input=True))


def test_promotion_result_model_copy_sanitizes_snake_case_boundary_update() -> None:
    unsafe_boundary = {
        "adkEvaluationImported": True,
        "modelCalled": True,
        "secretPath": "/Users/kevin/.env",
    }

    with pytest.raises(ValidationError) as exc_info:
        evaluate_promotion_request(_request()).model_copy(
            update={"adk_evaluation_boundary": unsafe_boundary},
        )

    encoded_errors = json.dumps(exc_info.value.errors(include_input=True), default=str).lower()
    assert '"adkevaluationimported": true' not in encoded_errors
    assert "modelcalled" not in encoded_errors
    assert "secretpath" not in encoded_errors
    assert "/users/kevin/.env" not in encoded_errors


def test_promotion_result_duplicate_alias_name_inputs_are_sanitized_before_errors() -> None:
    payload = evaluate_promotion_request(_request()).model_dump(by_alias=True, mode="python")
    payload["adk_evaluation_boundary"] = {
        "adkEvaluationImported": True,
        "modelCalled": True,
        "secretPath": "/Users/kevin/.env",
    }
    payload["authority_flags"] = {
        "modelCalled": True,
        "productionAuthority": True,
        "privatePath": "/Users/kevin/.env",
    }

    with pytest.raises(ValidationError) as exc_info:
        PromotionResult(**payload)

    encoded_errors = json.dumps(exc_info.value.errors(include_input=True), default=str).lower()
    assert '"adkevaluationimported": true' not in encoded_errors
    assert "modelcalled" not in encoded_errors
    assert "productionauthority" not in encoded_errors
    assert "privatepath" not in encoded_errors
    assert "/users/kevin/.env" not in encoded_errors


def test_release_gate_base_model_copy_repr_sanitizes_bypassed_internal_state() -> None:
    unsafe_boundary = {
        "adkEvaluationImported": True,
        "modelCalled": True,
        "secretPath": "/Users/kevin/.env",
    }

    bypassed = BaseModel.model_copy(
        evaluate_promotion_request(_request()),
        update={"adk_evaluation_boundary": unsafe_boundary},
    )

    encoded_repr = repr(bypassed).lower()
    assert "'adkevaluationimported': true" not in encoded_repr
    assert "'modelcalled': true" not in encoded_repr
    assert "secretpath" not in encoded_repr
    assert "/users/kevin/.env" not in encoded_repr


def test_release_gate_model_validate_construct_copy_and_dump_sanitize_unknown_objects() -> None:
    forged = BaseModel.model_construct.__func__(
        EvalObservationSet,
        cost_micros=_RawObject(),
        tool_invocations=8,
        eval_score=0.995,
        eval_failure_rate=0.0,
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with pytest.raises(ValidationError) as validate_exc:
            EvalObservationSet.model_validate(forged)
        with pytest.raises(ValidationError) as copy_exc:
            _observations().model_copy(update={"costMicros": _RawObject()})
        with pytest.raises((ValidationError, PydanticSerializationError)):
            forged.model_dump(by_alias=True, mode="json")
        with pytest.raises((ValidationError, PydanticSerializationError)):
            BaseModel.model_dump(forged, by_alias=True, mode="json")

    _assert_no_review_secret_leak(validate_exc.value.errors(include_input=True))
    _assert_no_review_secret_leak(copy_exc.value.errors(include_input=True))
    _assert_no_review_secret_leak([str(item.message) for item in caught])


def test_release_gate_public_metadata_and_refs_reject_sensitive_marker_classes() -> None:
    sensitive_values = (
        "authHeader",
        "apiKey",
        "hiddenReasoning",
        "toolOutput",
        "rawOutput",
    )

    for sensitive_value in sensitive_values:
        MarkerValue = type(sensitive_value, (str,), {})
        marker_instance = MarkerValue("publicRef")
        with pytest.raises(ValidationError) as metadata_exc:
            DigestOnlyProjection(
                projectionDigest=f"sha256:{_digest('a')}",
                policyDigest=f"sha256:{_digest('b')}",
                decisionDigest=f"sha256:{_digest('c')}",
                sourceSnapshotDigest=f"sha256:{_digest('d')}",
                publicMetadata={"safeRef": sensitive_value},
            )
        with pytest.raises(ValidationError) as ref_exc:
            SelectorGateDecision(
                selectorRef=f"selector:{sensitive_value}",
                selectedRef="recipe:governed-release-candidate",
                expectedGoverned=True,
                actualGoverned=True,
                usedFallback=False,
                governedPolicyDigest=f"sha256:{_digest('e')}",
            )
        with pytest.raises(ValidationError) as marker_metadata_exc:
            DigestOnlyProjection(
                projectionDigest=f"sha256:{_digest('a')}",
                policyDigest=f"sha256:{_digest('b')}",
                decisionDigest=f"sha256:{_digest('c')}",
                sourceSnapshotDigest=f"sha256:{_digest('d')}",
                publicMetadata={"safeRef": marker_instance},
            )
        with pytest.raises(ValidationError) as marker_ref_exc:
            SelectorGateDecision(
                selectorRef=marker_instance,
                selectedRef="recipe:governed-release-candidate",
                expectedGoverned=True,
                actualGoverned=True,
                usedFallback=False,
                governedPolicyDigest=f"sha256:{_digest('e')}",
            )

        encoded_metadata = json.dumps(metadata_exc.value.errors(include_input=True), default=str).lower()
        encoded_ref = json.dumps(ref_exc.value.errors(include_input=True), default=str).lower()
        encoded_marker_metadata = json.dumps(
            marker_metadata_exc.value.errors(include_input=True),
            default=str,
        ).lower()
        encoded_marker_ref = json.dumps(marker_ref_exc.value.errors(include_input=True), default=str).lower()
        assert sensitive_value.lower() not in encoded_metadata
        assert sensitive_value.lower() not in encoded_ref
        assert sensitive_value.lower() not in encoded_marker_metadata
        assert sensitive_value.lower() not in encoded_marker_ref


def test_promotion_gate_record_rejects_boundary_spoof_without_leaking_value() -> None:
    boundary_cases = (
        {
            "adkEvaluationImported": True,
            "boundary": "custom_public_boundary",
            "rationale": "custom_public_rationale",
            "futureAdkPrimitive": "Evaluation",
        },
        {
            "adkEvaluationImported": True,
            "modelCalled": True,
            "secretPath": "/Users/kevin/.env",
        },
        {
            "adkEvaluationImported": False,
            "secretPath": "/Users/kevin/.env",
        },
    )

    for boundary in boundary_cases:
        result_payload = evaluate_promotion_request(_request()).model_dump(by_alias=True, mode="python")
        result_payload["adkEvaluationBoundary"] = boundary

        with pytest.raises(ValidationError) as exc_info:
            PromotionGateRecord(
                request=_request(),
                result=result_payload,
                recordDigest=f"sha256:{_digest('6')}",
            )

        encoded_errors = json.dumps(exc_info.value.errors(include_input=True), default=str).lower()
        assert "/users/kevin/.env" not in encoded_errors
        assert "secretpath" not in encoded_errors
        assert "modelcalled" not in encoded_errors
        assert '"adkevaluationimported": true' not in encoded_errors


def test_release_gate_blocks_ungoverned_selector_even_when_fixture_expected_ungoverned() -> None:
    selector_decision = _selector_decision().model_copy(
        update={
            "expectedGoverned": False,
            "actualGoverned": False,
        },
    )

    result = evaluate_promotion_request(_request(selectorDecision=selector_decision))

    assert result.allowed is False
    assert "selector_governed_mismatch" in result.reason_codes


def test_projection_contract_accepts_only_digests_and_public_metadata() -> None:
    projection = _projection()
    dumped = projection.model_dump(by_alias=True, mode="json")

    assert tuple(dumped) == (
        "schemaVersion",
        "projectionDigest",
        "policyDigest",
        "decisionDigest",
        "sourceSnapshotDigest",
        "publicMetadata",
    )
    for key in ("projectionDigest", "policyDigest", "decisionDigest", "sourceSnapshotDigest"):
        assert dumped[key].startswith("sha256:")
        assert len(dumped[key]) == 71


def test_authority_and_live_flags_remain_false_even_when_constructed_unsafely() -> None:
    flags = ReleaseGateAuthorityFlags.model_construct(
        evaluation_attached=True,
        adk_evaluation_imported=True,
        model_called=True,
        live_tool_dispatched=True,
        traffic_attached=True,
        production_authority=True,
        runtime_activation_allowed=True,
    )
    forged_flags = BaseModel.model_construct.__func__(
        ReleaseGateAuthorityFlags,
        modelCalled=True,
        productionAuthority=True,
        runtimeActivationAllowed=True,
    )
    result = evaluate_promotion_request(_request(authorityFlags=flags))

    assert flags.model_called is False
    assert flags.production_authority is False
    assert set(flags.model_dump(by_alias=True).values()) == {False}
    assert forged_flags.model_called is False
    assert forged_flags.production_authority is False
    assert forged_flags.runtime_activation_allowed is False
    assert set(forged_flags.model_dump(by_alias=True).values()) == {False}
    assert set(result.authority_flags.model_dump(by_alias=True).values()) == {False}


def test_promotion_gate_record_cannot_certify_stale_or_forged_result() -> None:
    failing_request = _request(
        rawProjectionLeakDetected=True,
        rollbackRef=None,
        ownerApprovalRefs=(),
        canaryProofRefs=(),
        observations=_observations().model_copy(update={"evalScore": 0.0}),
    )
    forged_result = evaluate_promotion_request(_request())

    assert forged_result.allowed is True
    with pytest.raises(ValidationError, match="evaluated request"):
        PromotionGateRecord(
            request=failing_request,
            result=forged_result,
            recordDigest=f"sha256:{_digest('6')}",
        )
    bypassed_record = BaseModel.model_construct.__func__(
        PromotionGateRecord,
        request=failing_request,
        result=forged_result,
        record_digest=f"sha256:{_digest('6')}",
    )
    with pytest.raises(PydanticSerializationError, match="evaluated request"):
        BaseModel.model_dump(bypassed_record, by_alias=True, mode="json")


def test_promotion_gate_record_preserves_raw_projection_leak_condition() -> None:
    failing_request = _request(rawProjectionLeakDetected=True)
    forged_result = evaluate_promotion_request(_request())

    assert evaluate_promotion_request(failing_request).reason_codes == ("raw_projection_leak",)
    with pytest.raises(ValidationError, match="evaluated request") as exc_info:
        PromotionGateRecord(
            request=failing_request,
            result=forged_result,
            recordDigest=f"sha256:{_digest('6')}",
        )

    _assert_no_review_secret_leak(exc_info.value.errors(include_input=True))


def test_promotion_gate_record_stale_forged_result_error_is_sanitized_at_constructor() -> None:
    class ForgedResult(PromotionResult):
        model_config = ConfigDict(extra="allow")

        raw_output: str | None = None

    failing_request = _request(
        rawProjectionLeakDetected=True,
        rollbackRef=None,
        ownerApprovalRefs=(),
        canaryProofRefs=(),
    )
    forged_result = BaseModel.model_construct.__func__(
        ForgedResult,
        **evaluate_promotion_request(_request()).model_dump(by_alias=False, mode="python"),
        raw_output="raw prompt bearer token /Users/kevin/.env",
    )

    with pytest.raises(ValidationError, match="evaluated request") as exc_info:
        PromotionGateRecord(
            request=failing_request,
            result=forged_result,
            recordDigest=f"sha256:{_digest('6')}",
        )

    _assert_no_review_secret_leak(exc_info.value.errors(include_input=True))


def test_threshold_policy_cannot_disable_cost_tool_or_eval_guards() -> None:
    invalid_thresholds = (
        {"maxCostMicros": 10_000_001},
        {"maxToolInvocations": 251},
        {"minEvalScore": 0.49},
        {"maxEvalFailureRate": 0.26},
        {"verified": False},
    )

    for override in invalid_thresholds:
        payload = _thresholds().model_dump(by_alias=True, mode="python") | override
        with pytest.raises(ValidationError):
            EvalThresholds(**payload)


def test_evaluate_revalidates_forged_request_thresholds() -> None:
    forged_thresholds = BaseModel.model_construct.__func__(
        EvalThresholds,
        max_cost_micros=999_999_999_999,
        max_tool_invocations=999_999_999,
        min_eval_score=0.0,
        max_eval_failure_rate=1.0,
        threshold_policy_digest=f"sha256:{_digest('7')}",
        verified=False,
    )
    forged_request = BaseModel.model_construct.__func__(
        PromotionRequest,
        promotion_id="promotion-release-gate-0001",
        candidate_snapshot_digest=f"sha256:{_digest('5')}",
        target_stage="canary",
        thresholds=forged_thresholds,
        observations=_observations().model_copy(
            update={
                "costMicros": 999_999_999,
                "toolInvocations": 999_999,
                "evalScore": 0.0,
                "evalFailureRate": 1.0,
            },
        ),
        projection=_projection(),
        selector_decision=_selector_decision(),
        canary_proof_refs=(_canary_proof_ref(),),
        rollback_ref=_rollback_ref(),
        owner_approval_refs=(_owner_approval_ref(),),
        plugin_sandbox_observations=(_plugin_observation(),),
        hard_invariant_evaluations=(_hard_invariant(),),
        raw_projection_leak_detected=False,
        authority_flags=ReleaseGateAuthorityFlags(),
    )

    with pytest.raises(ValidationError, match="threshold policy"):
        evaluate_promotion_request(forged_request)


def test_release_gate_contract_documents_local_adk_evaluation_boundary_without_importing_adk_eval() -> None:
    assert ADK_EVALUATION_BOUNDARY == {
        "adkEvaluationImported": False,
        "boundary": "contract_only_local_evaluation_boundary",
        "rationale": (
            "Release gate contracts validate digest-only promotion evidence locally and do not "
            "replace ADK Evaluation suites."
        ),
        "futureAdkPrimitive": "Evaluation",
    }

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys
module = importlib.import_module("magi_agent.evals.release_gates")
assert module.ADK_EVALUATION_BOUNDARY["adkEvaluationImported"] is False
forbidden = (
    "google.adk.evaluation",
    "google.adk.evaluators",
    "google.adk.runners",
    "google.adk.models",
    "magi_agent.transport",
    "magi_agent.tools",
    "magi_agent.runtime.control",
)
loaded = [name for name in sys.modules if any(name == item or name.startswith(f"{item}.") for item in forbidden)]
if loaded:
    raise AssertionError(loaded)
""",
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=PYTHON_ROOT,
    )

    assert completed.returncode == 0, completed.stderr


def test_release_gate_source_has_no_model_network_tool_or_live_runtime_imports() -> None:
    source = MODULE_PATH.read_text(encoding="utf-8")
    forbidden_fragments = (
        "import google.adk",
        "from google.adk",
        "import requests",
        "from requests",
        "import httpx",
        "from httpx",
        "import urllib",
        "from urllib",
        "import socket",
        "from socket",
        "import subprocess",
        "from subprocess",
        "import magi_agent.transport",
        "from magi_agent.transport",
        "import magi_agent.tools",
        "from magi_agent.tools",
        "import magi_agent.runtime.control",
        "from magi_agent.runtime.control",
        "Runner(",
        "FunctionTool",
        "ToolHost",
        "model_called=True",
    )

    for fragment in forbidden_fragments:
        assert fragment not in source

    module = importlib.import_module("magi_agent.evals.release_gates")
    assert module.__name__ == "magi_agent.evals.release_gates"


def test_release_gate_validation_errors_do_not_echo_private_or_secret_refs() -> None:
    unsafe_values = (
        (
            SelectorGateDecision,
            {
                "selectorRef": "selector:release",
                "selectedRef": ["/Users/kevin/.env"],
                "expectedGoverned": True,
                "actualGoverned": True,
                "governedPolicyDigest": f"sha256:{_digest('e')}",
            },
            "/users/kevin/.env",
        ),
        (
            CanaryProofRef,
            {
                "proofRef": "canary:shadow-suite-0001",
                "proofDigest": "bearer-token-value",
                "verified": True,
            },
            "bearer-token-value",
        ),
        (
            OwnerApprovalRef,
            {
                "approvalRef": "approval:owner-release-0001",
                "ownerRef": "owner:private-session-key",
                "approvalDigest": f"sha256:{_digest('2')}",
                "approved": True,
                "verified": True,
            },
            "owner:private-session-key",
        ),
        (
            DigestOnlyProjection,
            {
                "projectionDigest": f"sha256:{_digest('a')}",
                "policyDigest": f"sha256:{_digest('b')}",
                "decisionDigest": f"sha256:{_digest('c')}",
                "sourceSnapshotDigest": f"sha256:{_digest('d')}",
                "publicMetadata": ["/Users/kevin/.env"],
            },
            "/users/kevin/.env",
        ),
        (
            PromotionRequest,
            {
                "promotionId": "promotion-/Users/kevin/.env",
                "candidateSnapshotDigest": f"sha256:{_digest('5')}",
                "targetStage": "canary",
                "thresholds": _thresholds(),
                "observations": _observations(),
                "projection": _projection(),
                "selectorDecision": _selector_decision(),
                "canaryProofRefs": (_canary_proof_ref(),),
                "rollbackRef": _rollback_ref(),
                "ownerApprovalRefs": (_owner_approval_ref(),),
                "pluginSandboxObservations": (_plugin_observation(),),
                "hardInvariantEvaluations": (_hard_invariant(),),
            },
            "/users/kevin/.env",
        ),
        (
            PromotionGateRecord,
            {
                "request": _request(),
                "result": evaluate_promotion_request(_request()),
                "recordDigest": "/Users/kevin/.env",
            },
            "/users/kevin/.env",
        ),
    )

    for model_type, payload, leaked_value in unsafe_values:
        with pytest.raises(ValidationError) as exc_info:
            model_type(**payload)
        encoded_errors = json.dumps(exc_info.value.errors(include_input=True), default=str).lower()
        assert leaked_value.lower() not in encoded_errors
