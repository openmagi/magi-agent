from __future__ import annotations

import json
import sys

import pytest
from pydantic import ValidationError

from magi_agent.self_improvement.eval_capture import (
    EvalCaptureConfig,
    EvalCaptureRequest,
    EvalCaptureResult,
    EvalObservation,
    EvalValidatorResult,
    SelfImprovementEvalCapture,
)
from magi_agent.self_improvement.failure_cluster import FailureClusterer


def _unsafe_value(*parts: str) -> str:
    return "".join(parts)


def _request(**overrides: object) -> EvalCaptureRequest:
    unsafe_bearer = _unsafe_value("si", "-", "opaque", "-", "12345678")
    payload: dict[str, object] = {
        "evalId": "eval:self-improvement.local",
        "runId": "run:self-improvement-1",
        "turnId": "turn:self-improvement-1",
        "recipeId": "recipe:self-improvement-eval",
        "policySnapshotDigest": "sha256:" + "a" * 64,
        "terminalState": "failed",
        "validatorResults": (
            {
                "validatorId": "validator:unsupported-claim",
                "status": "fail",
                "evidenceRefs": ("evidence:claim-gap",),
                "reasonCodes": ("unsupported_claim",),
                "summary": f"Unsupported claim. Authorization: Bearer {unsafe_bearer}",
            },
            {
                "validatorId": "validator:raw-projection",
                "status": "blocked",
                "evidenceRefs": ("evidence:projection-redacted",),
                "reasonCodes": ("raw_projection_blocked",),
                "summary": "raw prompt should not project",
            },
        ),
        "metricRefs": ("eval-metric:unsupported-claims",),
        "publicSummary": (
            "Failure observed.\n"
            "raw output: private model output\n"
            f"Cookie: session={_unsafe_value('si', '-session-', 'opaque')}"
        ),
        "adkEvaluationRef": "adk-eval:self-improvement-local-fixture",
        "rawPrompt": "private user prompt",
        "rawOutput": "private model output",
        "rawPrivatePath": "/Users/kevin/private/self-improvement",
        "toolLogs": f"Authorization: Bearer {unsafe_bearer}",
        "authHeaders": {"Authorization": f"Bearer {unsafe_bearer}"},
        "cookies": {"Cookie": "session=si-session-opaque"},
        "hiddenReasoning": "private chain of thought",
    }
    payload.update(overrides)
    return EvalCaptureRequest.model_validate(payload)


def test_eval_capture_is_disabled_by_default_and_authority_free() -> None:
    capture = SelfImprovementEvalCapture(EvalCaptureConfig())

    result = capture.capture(_request())

    assert result.status == "disabled"
    assert result.observation is None
    assert result.blocked_reason == "self_improvement_eval_capture_disabled"
    assert result.mutation_decision == "denied"
    assert set(result.authority_flags.model_dump(by_alias=True).values()) == {False}


def test_local_fake_eval_capture_records_digest_only_observation() -> None:
    capture = SelfImprovementEvalCapture(
        EvalCaptureConfig(enabled=True, localFakeCaptureEnabled=True)
    )

    result = capture.capture(_request())

    assert result.status == "captured_local_fake"
    assert result.observation is not None
    assert result.mutation_decision == "denied"
    assert result.adk_primitive == "google.adk.evaluation.AgentEvaluator"
    assert set(result.authority_flags.model_dump(by_alias=True).values()) == {False}

    observation = result.observation
    assert observation.policy_snapshot_digest == "sha256:" + "a" * 64
    assert observation.recipe_id == "recipe:self-improvement-eval"
    assert observation.terminal_state == "failed"
    assert observation.observation_digest.startswith("sha256:")
    assert observation.failure_signature_digest.startswith("sha256:")
    assert observation.denied_mutation_refs == ()
    assert observation.validator_results[0].status == "fail"
    assert observation.validator_results[0].validator_id == "validator:unsupported-claim"
    assert observation.validator_results[0].reason_codes == ("unsupported_claim",)
    assert observation.validator_results[0].evidence_refs == ("evidence:claim-gap",)

    encoded = json.dumps(result.model_dump(by_alias=True), sort_keys=True)
    for fragment in (
        "private user prompt",
        "private model output",
        "/Users/kevin",
        "Authorization: Bearer",
        "si-opaque-12345678",
        "Cookie: session=",
        "private chain of thought",
        "raw prompt should not project",
        "raw output:",
    ):
        assert fragment not in encoded
    assert "Failure observed." in encoded


def test_eval_capture_does_not_allow_automatic_code_or_config_mutation() -> None:
    capture = SelfImprovementEvalCapture(
        EvalCaptureConfig(enabled=True, localFakeCaptureEnabled=True)
    )

    result = capture.capture(
        _request(
            requestedMutations=(
                "code_patch",
                "config_update",
                "deploy_change",
                "secret_change",
            )
        )
    )

    assert result.status == "captured_local_fake"
    assert result.observation is not None
    assert result.mutation_decision == "denied"
    assert result.observation.denied_mutation_refs == (
        "mutation:code_patch",
        "mutation:config_update",
        "mutation:deploy_change",
        "mutation:secret_change",
    )
    assert result.denied_mutation_refs == (
        "mutation:code_patch",
        "mutation:config_update",
        "mutation:deploy_change",
        "mutation:secret_change",
    )
    control = capture.capture(_request())
    assert control.observation is not None
    assert result.observation.observation_digest != control.observation.observation_digest
    flags = result.authority_flags.model_dump(by_alias=True)
    assert flags["codeMutationEnabled"] is False
    assert flags["configMutationEnabled"] is False
    assert flags["deployMutationEnabled"] is False
    assert flags["secretMutationEnabled"] is False


def test_capture_requires_policy_snapshot_recipe_validator_and_terminal_state() -> None:
    with pytest.raises(ValidationError, match="policySnapshotDigest"):
        _request(policySnapshotDigest="policy:raw-snapshot")

    with pytest.raises(ValidationError, match="validatorResults"):
        _request(validatorResults=())

    with pytest.raises(ValidationError, match="terminalState"):
        _request(terminalState="running")

    with pytest.raises(ValidationError, match="recipeId"):
        _request(recipeId="raw:recipe-prompt")

    with pytest.raises(ValidationError, match="evalId"):
        _request(evalId="eval:/Users/kevin/worktree")

    with pytest.raises(ValidationError, match="runId"):
        _request(runId="run:/workspace/private/run")

    with pytest.raises(ValidationError, match="turnId"):
        _request(turnId="turn:/data/bots/private/turn")


def test_capture_revalidates_typed_request_instances_before_projection() -> None:
    capture = SelfImprovementEvalCapture(
        EvalCaptureConfig(enabled=True, localFakeCaptureEnabled=True)
    )
    request = _request()
    with pytest.raises(ValidationError, match="evalId"):
        request.model_copy(
            update={
                "eval_id": "eval:/Users/kevin/private",
                "public_summary": "raw output: private\nVisible summary",
            }
        )

    with pytest.raises(ValidationError, match="evalId"):
        request.model_copy(
            update={
                "eval_id": "eval:/Users/kevin/private",
                "public_summary": (
                    "raw output: private\n"
                    "Authorization: Bearer self-improvement-opaque\n"
                    "/Users/kevin/private"
                ),
            }
        )

    with pytest.raises(ValueError, match="copy is disabled"):
        request.copy(update={"eval_id": "eval:/Users/kevin/private"})

    with pytest.raises(ValidationError, match="evalId"):
        EvalCaptureRequest.model_construct(
            evalId="eval:/Users/kevin/private",
            runId="run:forged",
            turnId="turn:forged",
            recipeId="recipe:forged",
            policySnapshotDigest="sha256:" + "a" * 64,
            terminalState="failed",
            validatorResults=(
                {
                    "validatorId": "validator:forged",
                    "status": "fail",
                    "evidenceRefs": ("evidence:forged",),
                    "reasonCodes": ("forged",),
                },
            ),
            publicSummary="raw prompt: private\nVisible summary",
        )

    constructed = EvalCaptureRequest.model_construct(
        evalId="eval:forged",
        runId="run:forged",
        turnId="turn:forged",
        recipeId="recipe:forged",
        policySnapshotDigest="sha256:" + "a" * 64,
        terminalState="failed",
        validatorResults=(
            {
                "validatorId": "validator:forged",
                "status": "fail",
                "evidenceRefs": ("evidence:forged",),
                "reasonCodes": ("forged",),
            },
        ),
        publicSummary="raw prompt: private\nVisible summary",
    )
    assert constructed.eval_id == "eval:forged"
    assert constructed.public_summary == "Visible summary"
    result = capture.capture(constructed)
    encoded = json.dumps(result.model_dump(by_alias=True), sort_keys=True)
    assert "/Users/kevin" not in encoded
    assert "raw prompt" not in encoded
    assert "Visible summary" in encoded


def test_validator_results_are_sanitized_and_reject_raw_refs() -> None:
    with pytest.raises(ValidationError, match="evidenceRefs"):
        EvalValidatorResult.model_validate(
            {
                "validatorId": "validator:raw-ref",
                "status": "fail",
                "evidenceRefs": ("raw-tool-log:/Users/kevin/private",),
                "reasonCodes": ("raw tool log",),
                "summary": "raw prompt: private",
            }
        )

    result = EvalValidatorResult.model_validate(
        {
            "validatorId": "validator:public-redaction",
            "status": "fail",
            "evidenceRefs": ("evidence:redacted",),
            "reasonCodes": ("Authorization Bearer unsafe",),
            "summary": "raw prompt: private\nSafe public failure",
        }
    )
    assert result.reason_codes == ("eval_validator_reason",)
    assert result.summary == "Safe public failure"


def test_failure_clustering_is_deterministic_and_digest_only() -> None:
    capture = SelfImprovementEvalCapture(
        EvalCaptureConfig(enabled=True, localFakeCaptureEnabled=True)
    )
    first = capture.capture(_request(runId="run:self-improvement-1")).observation
    second = capture.capture(_request(runId="run:self-improvement-2")).observation
    assert first is not None
    assert second is not None

    cluster_set = FailureClusterer().cluster((second, first))
    replayed = FailureClusterer().cluster((first, second))

    assert cluster_set.cluster_set_digest == replayed.cluster_set_digest
    assert len(cluster_set.clusters) == 1
    cluster = cluster_set.clusters[0]
    assert cluster.cluster_id.startswith("failure-cluster:")
    assert cluster.observation_digest_refs == tuple(
        sorted((first.observation_digest, second.observation_digest))
    )
    assert cluster.validator_ids == ("validator:raw-projection", "validator:unsupported-claim")
    assert cluster.reason_codes == ("raw_projection_blocked", "unsupported_claim")
    assert cluster.terminal_states == ("failed",)

    encoded = json.dumps(cluster_set.model_dump(by_alias=True), sort_keys=True)
    for fragment in (
        "private user prompt",
        "private model output",
        "Authorization: Bearer",
        "/Users/kevin",
        "raw prompt",
    ):
        assert fragment not in encoded

    with pytest.raises(ValidationError, match="clusterSetDigest"):
        type(cluster_set).model_validate(
            cluster_set.model_dump(by_alias=True) | {"clusterSetDigest": "sha256:" + "b" * 64}
        )
    with pytest.raises(ValueError, match="model_copy"):
        cluster_set.model_copy(update={"clusterSetDigest": "sha256:" + "b" * 64})
    with pytest.raises(ValueError, match="copy is disabled"):
        cluster_set.copy(update={"cluster_set_digest": "sha256:" + "b" * 64})

    constructed = type(cluster_set).model_construct(
        clusters=cluster_set.clusters,
        observationCount=cluster_set.observation_count,
        failureCount=cluster_set.failure_count,
        clusterSetDigest="sha256:" + "b" * 64,
    )
    assert constructed.cluster_set_digest == cluster_set.cluster_set_digest

    with pytest.raises(ValidationError, match="clusterId"):
        type(cluster).model_validate(
            cluster.model_dump(by_alias=True) | {"clusterId": "failure-cluster:/Users/kevin/private"}
        )
    with pytest.raises(ValidationError, match="clusterId"):
        type(cluster).model_validate(
            cluster.model_dump(by_alias=True)
            | {"clusterId": "failure-cluster:" + "c" * 32}
        )
    with pytest.raises(ValueError, match="model_copy"):
        cluster.model_copy(update={"clusterId": "failure-cluster:/Users/kevin/private"})
    with pytest.raises(ValueError, match="copy is disabled"):
        cluster.copy(update={"cluster_id": "failure-cluster:/Users/kevin/private"})
    with pytest.raises(ValidationError, match="clusterId"):
        type(cluster).model_construct(
            clusterId="failure-cluster:/Users/kevin/private",
            failureSignatureDigest=cluster.failure_signature_digest,
            observationDigestRefs=cluster.observation_digest_refs,
            validatorIds=cluster.validator_ids,
            reasonCodes=cluster.reason_codes,
            terminalStates=cluster.terminal_states,
            occurrenceCount=cluster.occurrence_count,
        )
    with pytest.raises(ValidationError, match="clusterId"):
        type(cluster).model_construct(
            clusterId="failure-cluster:" + "c" * 32,
            failureSignatureDigest=cluster.failure_signature_digest,
            observationDigestRefs=cluster.observation_digest_refs,
            validatorIds=cluster.validator_ids,
            reasonCodes=cluster.reason_codes,
            terminalStates=cluster.terminal_states,
            occurrenceCount=cluster.occurrence_count,
        )
    with pytest.raises(ValidationError, match="failureSignatureDigest"):
        type(cluster).model_validate(
            cluster.model_dump(by_alias=True) | {"failureSignatureDigest": "raw:/Users/kevin"}
        )
    with pytest.raises(ValidationError, match="observationDigestRefs"):
        type(cluster).model_validate(
            cluster.model_dump(by_alias=True)
            | {"observationDigestRefs": ("raw:/Users/kevin/private",)}
        )

    with pytest.raises(ValidationError, match="clusterId"):
        type(cluster_set).model_construct(
            clusters=(
                cluster.model_dump(by_alias=True)
                | {"clusterId": "failure-cluster:/Users/kevin/private"},
            ),
            observationCount=1,
            failureCount=1,
        )


def test_non_failure_observations_do_not_form_failure_clusters() -> None:
    capture = SelfImprovementEvalCapture(
        EvalCaptureConfig(enabled=True, localFakeCaptureEnabled=True)
    )
    observation = capture.capture(
        _request(
            terminalState="passed",
            validatorResults=(
                {
                    "validatorId": "validator:final-gate",
                    "status": "pass",
                    "evidenceRefs": ("evidence:pass",),
                    "reasonCodes": ("passed",),
                    "summary": "All checks passed",
                },
            ),
        )
    ).observation
    assert observation is not None

    clusters = FailureClusterer().cluster((observation,))

    assert clusters.clusters == ()
    assert clusters.observation_count == 1
    assert clusters.failure_count == 0


def test_authority_flags_cannot_be_forged_by_construct_or_copy() -> None:
    capture = SelfImprovementEvalCapture(
        EvalCaptureConfig(enabled=True, localFakeCaptureEnabled=True)
    )
    result = capture.capture(_request())
    assert result.authority_flags.model_dump(by_alias=True)

    forged = result.authority_flags.model_copy(update={"codeMutationEnabled": True})
    constructed = type(result.authority_flags).model_construct(codeMutationEnabled=True)

    assert set(forged.model_dump(by_alias=True).values()) == {False}
    assert set(constructed.model_dump(by_alias=True).values()) == {False}
    deprecated_copy = result.authority_flags.copy(update={"code_mutation_enabled": True})
    assert set(deprecated_copy.model_dump(by_alias=True).values()) == {False}


def test_eval_capture_config_cannot_enable_live_authority_by_construct_or_copy() -> None:
    constructed = EvalCaptureConfig.model_construct(
        enabled=True,
        localFakeCaptureEnabled=True,
        liveAdkEvaluationEnabled=True,
        automaticMutationEnabled=True,
        productionWriteEnabled=True,
    )
    copied = EvalCaptureConfig().model_copy(
        update={
            "liveAdkEvaluationEnabled": True,
            "automaticMutationEnabled": True,
            "productionWriteEnabled": True,
        }
    )
    deprecated_copy = EvalCaptureConfig().copy(
        update={
            "live_adk_evaluation_enabled": True,
            "automatic_mutation_enabled": True,
            "production_write_enabled": True,
        }
    )

    for config in (constructed, copied, deprecated_copy):
        dump = config.model_dump(by_alias=True)
        assert dump["liveAdkEvaluationEnabled"] is False
        assert dump["automaticMutationEnabled"] is False
        assert dump["productionWriteEnabled"] is False

    forged = EvalCaptureConfig.model_construct(
        enabled=True,
        localFakeCaptureEnabled=True,
        liveAdkEvaluationEnabled=True,
        automaticMutationEnabled=True,
        productionWriteEnabled=True,
    )
    capture = SelfImprovementEvalCapture(forged)

    assert capture.config.enabled is True
    assert capture.config.local_fake_capture_enabled is True
    assert capture.config.live_adk_evaluation_enabled is False
    assert capture.config.automatic_mutation_enabled is False
    assert capture.config.production_write_enabled is False


def test_eval_capture_result_construct_and_copy_preserve_denied_authority() -> None:
    capture = SelfImprovementEvalCapture(
        EvalCaptureConfig(enabled=True, localFakeCaptureEnabled=True)
    )
    result = capture.capture(_request(requestedMutations=("code_patch",)))
    assert result.observation is not None

    copied = result.model_copy(
        update={
            "mutationDecision": "allowed",
            "authorityFlags": {"codeMutationEnabled": True},
        }
    )
    deprecated_copy = result.copy(
        update={
            "mutation_decision": "allowed",
            "authority_flags": {"codeMutationEnabled": True},
        }
    )
    constructed = EvalCaptureResult.model_construct(
        status="captured_local_fake",
        observation=result.observation,
        mutationDecision="allowed",
        authorityFlags={"codeMutationEnabled": True},
        deniedMutationRefs=("mutation:code_patch",),
    )

    for value in (copied, deprecated_copy, constructed):
        assert value.mutation_decision == "denied"
        assert value.denied_mutation_refs == ("mutation:code_patch",)
        assert set(value.authority_flags.model_dump(by_alias=True).values()) == {False}

    for update in (
        {"deniedMutationRefs": ("mutation:/Users/kevin/private",)},
        {"denied_mutation_refs": ("mutation:/workspace/private",)},
        {"blockedReason": "raw output: /Users/kevin/private"},
        {"blocked_reason": "Authorization: Bearer self-improvement-opaque"},
    ):
        with pytest.raises(ValidationError, match="deniedMutationRefs|blockedReason"):
            result.model_copy(update=update)
        with pytest.raises(ValidationError, match="deniedMutationRefs|blockedReason"):
            result.copy(update=update)

    with pytest.raises(ValidationError, match="blockedReason"):
        EvalCaptureResult.model_validate(
            {
                "status": "blocked",
                "blockedReason": "raw output: /Users/kevin/private",
            }
        )


def test_eval_capture_import_boundary_does_not_initialize_live_adk_or_tools() -> None:
    forbidden = {
        "google.adk.runners",
        "google.adk.agents",
        "magi_agent.tools.host",
        "magi_agent.transport.chat",
        "magi_agent.memory.hipocampus",
        "magi_agent.memory.qmd",
    }
    for module_name in forbidden:
        sys.modules.pop(module_name, None)

    __import__("magi_agent.self_improvement.eval_capture")
    __import__("magi_agent.self_improvement.failure_cluster")

    assert forbidden.isdisjoint(sys.modules)


def test_eval_observation_revalidates_digest_and_no_raw_projection_on_copy() -> None:
    capture = SelfImprovementEvalCapture(
        EvalCaptureConfig(enabled=True, localFakeCaptureEnabled=True)
    )
    observation = capture.capture(_request()).observation
    assert observation is not None

    with pytest.raises(ValidationError, match="observationDigest"):
        EvalObservation.model_validate(
            observation.model_dump(by_alias=True) | {"observationDigest": "sha256:" + "b" * 64}
        )
    with pytest.raises(ValidationError, match="deniedMutationRefs"):
        EvalObservation.model_validate(
            observation.model_dump(by_alias=True)
            | {
                "deniedMutationRefs": ("mutation:/Users/kevin/private",),
                "observationDigest": "sha256:" + "b" * 64,
            }
        )
    with pytest.raises(ValidationError, match="evalId"):
        EvalObservation.model_validate(
            observation.model_dump(by_alias=True)
            | {
                "evalId": "eval:/Users/kevin/private",
                "publicSummary": "raw output: private\nVisible summary",
                "observationDigest": "sha256:" + "b" * 64,
            }
        )

    with pytest.raises(ValueError, match="model_copy"):
        observation.model_copy(update={"rawPrompt": "private"})

    with pytest.raises(ValueError, match="copy is disabled"):
        observation.copy(update={"observation_digest": "sha256:" + "b" * 64})

    with pytest.raises(ValidationError, match="evalId"):
        EvalObservation.model_construct(
            observationId="observation:forged",
            observationDigest="sha256:" + "b" * 64,
            evalId="eval:/Users/kevin/private",
            runId="run:forged",
            turnId="turn:forged",
            recipeId="recipe:forged",
            policySnapshotDigest="sha256:" + "c" * 64,
            terminalState="failed",
            validatorResults=(),
            failureSignatureDigest="sha256:" + "d" * 64,
        )

    constructed = EvalObservation.model_construct(
        observationId="observation:forged",
        observationDigest="sha256:" + "b" * 64,
        evalId="eval:forged",
        runId="run:forged",
        turnId="turn:forged",
        recipeId="recipe:forged",
        policySnapshotDigest="sha256:" + "c" * 64,
        terminalState="failed",
        validatorResults=(),
        failureSignatureDigest="sha256:" + "d" * 64,
    )
    assert constructed.observation_id.startswith("observation:")
    assert constructed.eval_id == "eval:forged"
    assert constructed.observation_digest.startswith("sha256:")
    assert constructed.observation_digest != "sha256:" + "b" * 64
