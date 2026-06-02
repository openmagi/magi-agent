from __future__ import annotations

import json

from fastapi.testclient import TestClient

from openmagi_core_agent.app import create_app
from openmagi_core_agent.config.models import (
    BuildInfo,
    PythonContextContinuityConfig,
    RuntimeConfig,
)
from openmagi_core_agent.gates.pregate8_continuity_canary import (
    PreGate8ContinuityCanaryEvidence,
)
from openmagi_core_agent.runtime.openmagi_runtime import OpenMagiRuntime


def _digest(label: str) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()


def _evidence(**overrides: object) -> PreGate8ContinuityCanaryEvidence:
    payload = {
        "status": "pass",
        "fallbackStatus": "none",
        "importedEventCount": 4,
        "rejectedEntryCount": 1,
        "compactionApplied": True,
        "projectionDigest": _digest("projection"),
        "modelVisibleDigest": _digest("model-visible"),
        "sourceTranscriptHeadDigest": _digest("source-head"),
        "observedAdkSessionDigest": _digest("adk-session"),
        "observedModelVisibleDigest": _digest("observed-message"),
        "antecedentDigest": _digest("antecedent"),
        "currentFollowupDigest": _digest("followup"),
        "antecedentPresentInAdkSession": True,
        "currentFollowupPresentInModelVisibleMessage": True,
        "privatePayloadRejected": True,
        "reasonCodes": (
            "runner_completed",
            "antecedent_present",
            "followup_present",
            "private_payload_rejected",
            "fallback_none",
        ),
    }
    payload.update(overrides)
    return PreGate8ContinuityCanaryEvidence.model_validate(payload)


def _runtime(context: PythonContextContinuityConfig) -> OpenMagiRuntime:
    return OpenMagiRuntime(
        config=RuntimeConfig(
            bot_id="bot-test",
            user_id="user-test",
            gateway_token="gateway-token",
            api_proxy_url="http://api-proxy.local",
            chat_proxy_url="http://chat-proxy.local",
            redis_url="redis://redis.local:6379/0",
            model="gpt-5.2",
            build=BuildInfo(version="0.1.0-adk-scaffold", build_sha="sha-test"),
            contextContinuity=context,
        )
    )


def test_context_continuity_config_from_verified_evidence_keeps_gate8_metadata_only() -> None:
    context = PythonContextContinuityConfig.from_canary_evidence(_evidence())

    assert context.enabled is True
    assert context.mode == "selected_canary"
    assert context.canary_status == "pass"
    assert context.imported_event_count == 4
    assert context.rejected_entry_count == 1
    assert context.compaction_applied is True
    assert context.projection_digest_present is True
    assert context.model_visible_digest_present is True
    assert context.source_transcript_head_digest_present is True
    assert context.canary_evidence_verified is True
    assert context.canary_evidence_source == "local_verified_evidence"
    assert context.fallback_status == "none"
    assert context.continuity_canary_ready is True
    assert context.gate8_block_reason == "pre_gate8_continuity_canary_pass"
    assert context.production_authority_allowed is False
    assert context.transcript_write_allowed is False
    assert context.sse_write_allowed is False
    assert context.db_write_allowed is False

    serialized = json.dumps(context.health_metadata, sort_keys=True)
    assert _digest("projection") not in serialized
    assert _digest("model-visible") not in serialized
    assert _digest("source-head") not in serialized


def test_context_continuity_config_blocks_gate8_when_evidence_fails() -> None:
    context = PythonContextContinuityConfig.from_canary_evidence(
        _evidence(
            status="fail",
            antecedentPresentInAdkSession=False,
            reasonCodes=("runner_completed", "antecedent_missing", "fallback_none"),
        )
    )

    assert context.canary_status == "fail"
    assert context.canary_evidence_verified is False
    assert context.continuity_canary_ready is False
    assert context.gate8_block_reason == "pre_gate8_continuity_canary_failed"
    assert context.health_metadata["continuityCanaryReady"] is False


def test_context_continuity_config_blocks_gate8_when_fallback_was_needed() -> None:
    context = PythonContextContinuityConfig.from_canary_evidence(
        _evidence(
            status="fail",
            fallbackStatus="typescript_fallback",
            reasonCodes=("runner_completed", "antecedent_present", "fallback_active"),
        )
    )

    assert context.canary_status == "fail"
    assert context.canary_evidence_verified is False
    assert context.fallback_status == "typescript_fallback"
    assert context.continuity_canary_ready is False
    assert context.gate8_block_reason == "pre_gate8_continuity_fallback_active"


def test_context_continuity_config_blocks_gate8_when_required_digests_are_missing() -> None:
    context = PythonContextContinuityConfig.from_canary_evidence(
        _evidence(status="fail", projectionDigest=None)
    )

    assert context.canary_status == "fail"
    assert context.canary_evidence_verified is False
    assert context.projection_digest_present is False
    assert context.continuity_canary_ready is False
    assert context.gate8_block_reason == "pre_gate8_continuity_evidence_incomplete"


def test_context_continuity_config_rejects_non_sha_digest_like_generic_evidence() -> None:
    class _GenericEvidence:
        status = "pass"
        fallback_status = "none"
        local_only = True
        diagnostic_only = True
        response_authority = "none"
        imported_event_count = 4
        rejected_entry_count = 0
        compaction_applied = False
        projection_digest = "sha256:" + "z" * 64
        model_visible_digest = _digest("model-visible")
        source_transcript_head_digest = _digest("source-head")
        reason_codes = ("runner_completed",)
        compaction_boundary_respected = True
        forbidden_payload_observed = False
        antecedent_present_in_adk_session = True
        antecedent_present_in_model_visible_projection = False
        current_followup_present_in_model_visible_message = True

    context = PythonContextContinuityConfig.from_canary_evidence(_GenericEvidence())

    assert context.projection_digest_present is False
    assert context.canary_evidence_verified is False
    assert context.continuity_canary_ready is False
    assert context.gate8_block_reason == "pre_gate8_continuity_evidence_incomplete"


def test_context_continuity_config_rejects_generic_object_even_with_valid_fields() -> None:
    class _GenericEvidence:
        status = "pass"
        fallback_status = "none"
        local_only = True
        diagnostic_only = True
        response_authority = "none"
        imported_event_count = 4
        rejected_entry_count = 0
        compaction_applied = False
        compaction_boundary_respected = True
        projection_digest = _digest("projection")
        model_visible_digest = _digest("model-visible")
        source_transcript_head_digest = _digest("source-head")
        observed_adk_session_digest = _digest("adk-session")
        observed_model_visible_digest = _digest("observed-message")
        antecedent_digest = _digest("antecedent")
        current_followup_digest = _digest("followup")
        antecedent_present_in_adk_session = True
        antecedent_present_in_model_visible_projection = False
        current_followup_present_in_model_visible_message = True
        forbidden_payload_observed = False
        private_payload_rejected = False
        reason_codes = (
            "runner_completed",
            "antecedent_present",
            "followup_present",
            "fallback_none",
        )

    context = PythonContextContinuityConfig.from_canary_evidence(_GenericEvidence())

    assert context.canary_evidence_verified is False
    assert context.canary_evidence_source == "none"
    assert context.continuity_canary_ready is False
    assert context.gate8_block_reason == "pre_gate8_continuity_evidence_unverified"


def test_context_continuity_config_cannot_be_manually_forged_ready() -> None:
    context = PythonContextContinuityConfig(
        enabled=True,
        mode="selected_canary",
        canaryStatus="pass",
        importedEventCount=3,
        projectionDigestPresent=True,
        modelVisibleDigestPresent=True,
        sourceTranscriptHeadDigestPresent=True,
        canaryEvidenceVerified=True,
        canaryEvidenceSource="local_verified_evidence",
        fallbackStatus="none",
    )

    assert context.canary_evidence_verified is False
    assert context.canary_evidence_source == "none"
    assert context.continuity_canary_ready is False
    assert context.gate8_block_reason == "pre_gate8_continuity_evidence_unverified"


def test_context_continuity_config_filters_unsafe_reason_codes_from_health() -> None:
    context = PythonContextContinuityConfig.from_canary_evidence(
        _evidence(
            reasonCodes=(
                "runner_completed",
                "unsafe_trace_label",
                "raw_private_trace_label",
                "private_payload_rejected",
                "fallback_none",
            )
        )
    )

    assert context.reason_codes == (
        "runner_completed",
        "private_payload_rejected",
        "fallback_none",
    )
    assert context.health_metadata["reasonCodes"] == [
        "runner_completed",
        "private_payload_rejected",
        "fallback_none",
    ]


def test_context_continuity_config_requires_private_rejection_when_entries_rejected() -> None:
    context = PythonContextContinuityConfig.from_canary_evidence(
        _evidence(privatePayloadRejected=False)
    )

    assert context.rejected_entry_count == 1
    assert context.canary_evidence_verified is False
    assert context.continuity_canary_ready is False
    assert context.gate8_block_reason == "pre_gate8_continuity_evidence_unverified"


def test_context_continuity_config_requires_private_rejection_reason_code() -> None:
    context = PythonContextContinuityConfig.from_canary_evidence(
        _evidence(
            privatePayloadRejected=True,
            reasonCodes=(
                "runner_completed",
                "antecedent_present",
                "followup_present",
                "fallback_none",
            ),
        )
    )

    assert context.rejected_entry_count == 1
    assert context.canary_evidence_verified is False
    assert context.continuity_canary_ready is False
    assert context.gate8_block_reason == "pre_gate8_continuity_evidence_unverified"


def test_context_continuity_config_rejects_closed_fallback_as_verified_pass() -> None:
    context = PythonContextContinuityConfig.from_canary_evidence(
        _evidence(
            fallbackStatus="closed",
            reasonCodes=(
                "runner_completed",
                "antecedent_present",
                "followup_present",
                "private_payload_rejected",
                "fallback_active",
            ),
        )
    )

    assert context.fallback_status == "closed"
    assert context.canary_evidence_verified is False
    assert context.continuity_canary_ready is False
    assert context.gate8_block_reason == "pre_gate8_continuity_fallback_active"


def test_context_continuity_config_requires_observed_followup_digest() -> None:
    context = PythonContextContinuityConfig.from_canary_evidence(
        _evidence(currentFollowupDigest=None)
    )

    assert context.canary_evidence_verified is False
    assert context.continuity_canary_ready is False
    assert context.gate8_block_reason == "pre_gate8_continuity_evidence_unverified"


def test_healthz_reports_pre_gate8_verified_pass_without_user_visible_authority() -> None:
    context = PythonContextContinuityConfig.from_canary_evidence(_evidence())
    client = TestClient(create_app(_runtime(context)))

    response = client.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    assert body["contextContinuity"]["continuityCanaryReady"] is True
    assert body["contextContinuity"]["canaryEvidenceVerified"] is True
    assert body["gate8Readiness"]["blockedByPreGate8Continuity"] is False
    assert body["gate8Readiness"]["reasonCode"] == "pre_gate8_continuity_canary_pass"
    assert body["userVisibleOutputAllowed"] is False
    assert body["canaryRoutingAllowed"] is False
    assert body["transcriptWritesAllowed"] is False
    assert body["sseWritesAllowed"] is False
    assert body["dbWritesAllowed"] is False


def test_context_continuity_env_can_run_default_off_local_readiness_harness() -> None:
    from openmagi_core_agent.config.env import parse_runtime_env

    config = parse_runtime_env(
        {
            "BOT_ID": "bot-test",
            "USER_ID": "user-test",
            "GATEWAY_TOKEN": "gateway-token",
            "CORE_AGENT_API_PROXY_URL": "http://api-proxy.local",
            "CORE_AGENT_CHAT_PROXY_URL": "http://chat-proxy.local",
            "CORE_AGENT_REDIS_URL": "redis://redis.local:6379/0",
            "CORE_AGENT_MODEL": "gpt-5.2",
            "CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_ENABLED": "1",
            "CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_MODE": "selected_canary",
            "CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_LOCAL_CANARY_HARNESS": "1",
        }
    )

    context = config.context_continuity
    assert context.continuity_canary_ready is True
    assert context.canary_evidence_verified is True
    assert context.canary_evidence_source == "local_verified_evidence"
    assert context.gate8_block_reason == "pre_gate8_continuity_canary_pass"
    assert context.production_authority_allowed is False
    assert context.transcript_write_allowed is False
    assert context.sse_write_allowed is False
    assert context.db_write_allowed is False

    client = TestClient(create_app(OpenMagiRuntime(config=config)))
    body = client.get("/healthz").json()
    assert body["contextContinuity"]["continuityCanaryReady"] is True
    assert body["gate8Readiness"]["blockedByPreGate8Continuity"] is False
    assert body["userVisibleOutputAllowed"] is False
    assert body["canaryRoutingAllowed"] is False
    assert body["transcriptWritesAllowed"] is False
    assert body["sseWritesAllowed"] is False
    assert body["dbWritesAllowed"] is False


def test_context_continuity_local_readiness_harness_still_rejects_authority_forgery() -> None:
    from openmagi_core_agent.config.env import RuntimeEnvError, parse_runtime_env

    try:
        parse_runtime_env(
            {
                "BOT_ID": "bot-test",
                "USER_ID": "user-test",
                "GATEWAY_TOKEN": "gateway-token",
                "CORE_AGENT_API_PROXY_URL": "http://api-proxy.local",
                "CORE_AGENT_CHAT_PROXY_URL": "http://chat-proxy.local",
                "CORE_AGENT_REDIS_URL": "redis://redis.local:6379/0",
                "CORE_AGENT_MODEL": "gpt-5.2",
                "CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_ENABLED": "1",
                "CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_MODE": "selected_canary",
                "CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_LOCAL_CANARY_HARNESS": "1",
                "CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_PRODUCTION_AUTHORITY": "true",
            }
        )
    except RuntimeEnvError as exc:
        assert "CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_PRODUCTION_AUTHORITY" in str(exc)
    else:
        raise AssertionError("context continuity authority forgery was accepted")
