from __future__ import annotations

from magi_agent.shadow.gate5b4c3_shadow_comparison import (
    build_gate5b4c3_shadow_comparison_artifact,
)
from magi_agent.shadow.gate5b4c3_shadow_generation_contract import (
    Gate5B4C3ShadowGenerationConfig,
    Gate5B4C3ShadowGenerationRequest,
    build_gate5b4c3_shadow_generation_diagnostic,
)
from magi_agent.shadow.gate5b4c3_shadow_generation_report import (
    build_gate5b4c3_shadow_generation_report,
)


BOT_DIGEST = "sha256:" + "a" * 64
OWNER_DIGEST = "sha256:" + "b" * 64
TURN_DIGEST = "sha256:" + "c" * 64
REQUEST_DIGEST = "sha256:" + "d" * 64
TRACE_DIGEST = "sha256:" + "e" * 64
SESSION_DIGEST = "sha256:" + "f" * 64
SANITIZED_DIGEST = "sha256:" + "1" * 64
ROUTER_DIGEST = "sha256:" + "2" * 64
PROFILE_DIGEST = "sha256:" + "3" * 64
TS_DIGEST = "sha256:" + "4" * 64


def _request() -> Gate5B4C3ShadowGenerationRequest:
    return Gate5B4C3ShadowGenerationRequest.model_validate(
        {
            "schemaVersion": "gate5b4c3.chatProxyShadowGeneration.v1",
            "shadowGenerationId": "shadow_gen_001",
            "requestIdDigest": REQUEST_DIGEST,
            "traceIdDigest": TRACE_DIGEST,
            "createdAt": 1779200000000,
            "selection": {
                "botIdDigest": BOT_DIGEST,
                "ownerUserIdDigest": OWNER_DIGEST,
                "environment": "production",
                "selectedTarget": "gate5b_selected_bot",
                "sessionKeyDigest": SESSION_DIGEST,
            },
            "turn": {
                "turnId": "turn_opaque_001",
                "turnDigest": TURN_DIGEST,
                "sanitizedCurrentTurnText": "Synthetic comparison input only.",
                "sanitizedInputTextDigest": SANITIZED_DIGEST,
                "channelName": "unknown",
                "tsResponseCorrelationId": "ts_corr_001",
                "attachmentMetadata": [],
            },
            "modelRouting": {
                "routingSource": "per_turn_injected",
                "providerLabel": "google",
                "modelLabel": "gemini-3.5-flash",
                "routerDecisionDigest": ROUTER_DIGEST,
                "routingProfileDigest": PROFILE_DIGEST,
                "shadowCredentialRef": "gate5b-google-api-key-smoke-v1",
                "credentialRefSource": "server_config",
            },
            "recipeProfile": {
                "recipeId": "gate5b_shadow_smoke",
                "recipeVersion": "v1",
                "profileId": "no_tools_no_memory_current_turn_only",
                "profileVersion": "v1",
                "runtimeEngine": "adk-python",
                "toolsPolicy": "disabled",
                "memoryMode": "disabled",
                "sourceAuthority": "current_turn_only",
            },
            "policy": {
                "typeScriptResponseAuthority": True,
                "pythonDiagnosticOnly": True,
                "outputIsolation": "local_diagnostic_only",
                "toolsDisabled": True,
                "toolHostDispatchAllowed": False,
                "memoryProviderCallsAllowed": False,
                "memoryWritesAllowed": False,
                "promptMemoryInjectionAllowed": False,
                "workspaceMutationAllowed": False,
                "childExecutionAllowed": False,
                "missionRuntimeAllowed": False,
                "evidenceBlockModeAllowed": False,
            },
            "budgets": {"maxOutputTokens": 64, "maxDiagnosticOutputPreviewBytes": 128},
            "redaction": {
                "sanitizerId": "gate5b_synthetic_sanitizer",
                "sanitizerVersion": "v1",
                "policyId": "gate5b4c3_synthetic_only",
                "status": "passed",
                "redactedAt": 1779200000001,
                "redactedByteCount": 32,
                "forbiddenFieldScan": "passed",
                "sanitizedPayloadDigest": SANITIZED_DIGEST,
                "droppedFieldReasons": [],
            },
            "comparison": {
                "typeScriptFinalAnswerDigest": TS_DIGEST,
                "typeScriptTerminalStatus": "completed",
            },
            "authority": {},
        }
    )


def test_comparison_artifact_is_internal_digest_only_and_non_authoritative() -> None:
    request = _request()
    diagnostic = build_gate5b4c3_shadow_generation_diagnostic(
        request,
        config=Gate5B4C3ShadowGenerationConfig(),
    )
    report = build_gate5b4c3_shadow_generation_report(
        diagnostic=diagnostic,
        status="completed",
        reason="runner_completed",
        adk_runner_invoked=True,
        model_call_attempted=True,
        output_text="Synthetic internal comparison candidate.",
        routing_source="per_turn_injected",
        retry_policy="none",
        cost_cap_usd=0.05,
    )

    artifact = build_gate5b4c3_shadow_comparison_artifact(request, report)
    dumped = artifact.model_dump(by_alias=True, mode="json")

    assert dumped["schemaVersion"] == "gate5b4c3.shadowComparisonArtifact.v1"
    assert dumped["responseAuthority"] == "typescript"
    assert dumped["diagnosticOnly"] is True
    assert dumped["localOnly"] is True
    assert dumped["userVisibleOutput"] is None
    assert dumped["productionWriteTargets"] == []
    assert dumped["comparisonStatus"] == "ready"
    assert dumped["typeScriptFinalAnswerDigest"] == TS_DIGEST
    assert dumped["pythonOutputDigest"] == report.output_digest
    assert dumped["artifactDigest"].startswith("sha256:")
    assert "Synthetic internal comparison candidate." not in str(dumped)


def test_comparison_artifact_records_missing_python_output_without_user_output() -> None:
    request = _request()
    diagnostic = build_gate5b4c3_shadow_generation_diagnostic(
        request,
        config=Gate5B4C3ShadowGenerationConfig(),
    )
    report = build_gate5b4c3_shadow_generation_report(
        diagnostic=diagnostic,
        status="error",
        reason="runner_error",
        error_class="RuntimeError",
        error_preview="provider failed",
        routing_source="per_turn_injected",
        retry_policy="none",
        cost_cap_usd=0.05,
    )

    artifact = build_gate5b4c3_shadow_comparison_artifact(request, report)

    assert artifact.comparison_status == "missing_python_output"
    assert artifact.python_output_digest is None
    assert artifact.user_visible_output is None
    assert artifact.production_write_targets == ()
