from __future__ import annotations

import subprocess
import sys

from magi_agent.shadow.gate5b4c3_shadow_generation_contract import (
    Gate5B4C3ShadowGenerationConfig,
    Gate5B4C3ShadowGenerationRequest,
    build_gate5b4c3_shadow_generation_diagnostic,
)
from magi_agent.shadow.gate5b4c3_shadow_generation_report import (
    Gate5B4C3ShadowGenerationRunnerReport,
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
BOT_CONFIG_DIGEST = "sha256:" + "4" * 64


def _payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "schemaVersion": "gate5b4c3.chatProxyShadowGeneration.v1",
        "mode": "shadow_generation_diagnostic",
        "responseAuthority": "typescript",
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
            "sanitizedCurrentTurnText": "Please summarize the approved redacted note.",
            "sanitizedInputTextDigest": SANITIZED_DIGEST,
            "channelName": "app_channel",
            "tsResponseCorrelationId": "ts_corr_001",
        },
        "modelRouting": {
            "routingSource": "per_turn_injected",
            "providerLabel": "anthropic",
            "modelLabel": "claude-3-5-sonnet-latest",
            "routerDecisionDigest": ROUTER_DIGEST,
            "routingProfileDigest": PROFILE_DIGEST,
            "botConfigModelDigest": BOT_CONFIG_DIGEST,
            "shadowCredentialRef": "server-shadow-ref",
            "credentialRefSource": "server_config",
            "temperature": 0.2,
            "maxOutputTokens": 512,
        },
        "recipeProfile": {
            "recipeId": "office-assistant",
            "recipeVersion": "2026-05-19",
            "profileId": "selected-bot-shadow",
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
        "budgets": {},
        "redaction": {
            "sanitizerId": "chat-proxy-sanitizer",
            "sanitizerVersion": "v1",
            "policyId": "gate5b4c3-redaction",
            "status": "passed",
            "redactedAt": 1779200000001,
            "redactedByteCount": 47,
            "forbiddenFieldScan": "passed",
            "sanitizedPayloadDigest": SANITIZED_DIGEST,
        },
        "authority": {},
    }
    base.update(overrides)
    return base


def _request() -> Gate5B4C3ShadowGenerationRequest:
    return Gate5B4C3ShadowGenerationRequest.model_validate(_payload())


def _diagnostic() -> object:
    return build_gate5b4c3_shadow_generation_diagnostic(
        _request(),
        config=Gate5B4C3ShadowGenerationConfig(
            enabled=True,
            killSwitchActive=False,
            capStateInitialized=True,
            providerProjectSpendControlsVerified=True,
            selectedBotDigest=BOT_DIGEST,
            trustedOwnerUserIdDigest=OWNER_DIGEST,
            environment="production",
            allowedProviderLabels=("anthropic",),
            allowedModelLabels=("claude-3-5-sonnet-latest",),
            allowedModelRoutes=("anthropic:claude-3-5-sonnet-latest",),
            allowedShadowCredentialRefs=("server-shadow-ref",),
        ),
    )


def test_shadow_generation_report_hashes_output_without_preview() -> None:
    report = build_gate5b4c3_shadow_generation_report(
        diagnostic=_diagnostic(),
        status="completed",
        reason="runner_completed",
        adk_runner_invoked=True,
        model_call_attempted=True,
        event_count=2,
        output_text="safe diagnostic output " + ("x" * 300),
        latency_ms=25,
        runner_timeout_ms=30_000,
        max_output_tokens=512,
        max_estimated_input_tokens=2048,
        max_total_estimated_tokens=2560,
        routing_source="per_turn_injected",
        router_decision_digest=ROUTER_DIGEST,
        routing_profile_digest=PROFILE_DIGEST,
        bot_config_model_digest=BOT_CONFIG_DIGEST,
        fallback_approved=False,
        shadow_credential_ref="server-shadow-ref",
        retry_policy="none",
        cost_cap_usd=0.05,
        preview_byte_limit=64,
    )

    assert report.status == "completed"
    assert report.reason == "runner_completed"
    assert report.response_authority == "typescript"
    assert report.diagnostic_only is True
    assert report.local_only is True
    assert report.fail_open is True
    assert report.adk_runner_invoked is True
    assert report.model_call_attempted is True
    assert report.runner_timeout_ms == 30_000
    assert report.max_output_tokens == 512
    assert report.max_estimated_input_tokens == 2048
    assert report.max_total_estimated_tokens == 2560
    assert report.routing_source == "per_turn_injected"
    assert report.router_decision_digest == ROUTER_DIGEST
    assert report.routing_profile_digest == PROFILE_DIGEST
    assert report.bot_config_model_digest == BOT_CONFIG_DIGEST
    assert report.fallback_approved is False
    assert report.shadow_credential_ref == "server-shadow-ref"
    assert report.retry_policy == "none"
    assert report.cost_cap_usd == 0.05
    assert report.output_accepted is True
    assert report.output_digest is not None
    assert report.output_preview_internal is None
    assert report.output_truncated is True
    assert report.user_visible_output is None
    assert report.production_write_targets == ()
    assert report.authority.user_visible_output_allowed is False
    assert report.authority.db_writes_allowed is False


def test_shadow_generation_report_reduces_unsafe_output_to_hash_only() -> None:
    report = build_gate5b4c3_shadow_generation_report(
        diagnostic=_diagnostic(),
        status="completed",
        reason="runner_completed",
        adk_runner_invoked=True,
        model_call_attempted=True,
        output_text="Authorization: Bearer raw-secret-token",
    )

    assert report.output_accepted is False
    assert report.output_rejection_reason == "unsafe_output"
    assert report.output_preview_internal is None
    assert report.output_digest is not None
    assert report.user_visible_output is None
    assert report.response_authority == "typescript"


def test_shadow_generation_report_does_not_store_raw_or_redacted_output_preview() -> None:
    correlation_id = "".join(["01234567", "89abcdef", "01234567", "89abcdef"])
    report = build_gate5b4c3_shadow_generation_report(
        diagnostic=_diagnostic(),
        status="completed",
        reason="runner_completed",
        output_text=(
            "safe summary for analyst@example.com with correlation "
            f"{correlation_id}"
        ),
        preview_byte_limit=200,
    )

    assert report.output_accepted is True
    assert report.output_digest is not None
    assert report.output_preview_internal is None
    assert "analyst@example.com" not in str(
        report.model_dump(by_alias=True, mode="json")
    )
    assert correlation_id not in str(report.model_dump(by_alias=True, mode="json"))


def test_shadow_generation_report_errors_fail_open_with_redacted_preview() -> None:
    report = build_gate5b4c3_shadow_generation_report(
        diagnostic=_diagnostic(),
        status="error",
        reason="runner_error",
        adk_runner_invoked=True,
        model_call_attempted=True,
        error_class="RuntimeError",
        error_preview="provider error with sk-secret-token and /workspace/private",
        preview_byte_limit=80,
    )

    assert report.status == "error"
    assert report.reason == "runner_error"
    assert report.fail_open is True
    assert report.error_class == "RuntimeError"
    assert report.error_preview is not None
    assert "sk-secret" not in report.error_preview
    assert "/workspace" not in report.error_preview
    assert report.user_visible_output is None


def test_shadow_generation_report_copy_and_construct_cannot_create_authority() -> None:
    report = build_gate5b4c3_shadow_generation_report(
        diagnostic=_diagnostic(),
        status="completed",
        reason="runner_completed",
        output_text="safe diagnostic output",
    )

    copied = report.model_copy(
        update={
            "responseAuthority": "python",
            "diagnosticOnly": False,
            "localOnly": False,
            "failOpen": False,
            "userVisibleOutput": "leak",
            "productionWriteTargets": ("transcript",),
        }
    )
    constructed = Gate5B4C3ShadowGenerationRunnerReport.model_construct(
        diagnostic=report.diagnostic,
        status="completed",
        reason="runner_completed",
        responseAuthority="python",
        diagnosticOnly=False,
        localOnly=False,
        failOpen=False,
        userVisibleOutput="leak",
        productionWriteTargets=("db",),
    )

    for mutated in (copied, constructed):
        assert mutated.response_authority == "typescript"
        assert mutated.diagnostic_only is True
        assert mutated.local_only is True
        assert mutated.fail_open is True
        assert mutated.user_visible_output is None
        assert mutated.production_write_targets == ()
        assert mutated.authority.user_visible_output_allowed is False


def test_shadow_generation_report_import_boundary_has_no_adk_or_runtime_imports() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

module = importlib.import_module(
    "magi_agent.shadow.gate5b4c3_shadow_generation_report"
)
assert module is not None

forbidden = (
    "google.adk",
    "magi_agent.transport.chat",
    "magi_agent.transport.tools",
    "magi_agent.runtime.openmagi_runtime",
    "magi_agent.routing",
    "magi_agent.workspace",
    "magi_agent.children",
    "magi_agent.evidence",
    "magi_agent.memory",
    "openai",
    "anthropic",
)
loaded = [
    name for name in sys.modules
    if any(name == prefix or name.startswith(f"{prefix}.") for prefix in forbidden)
]
if loaded:
    raise AssertionError(f"report loaded forbidden modules: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
