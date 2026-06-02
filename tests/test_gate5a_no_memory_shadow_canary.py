from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from magi_agent.shadow.gate4_consumer import Gate4LocalHandoff
from magi_agent.shadow.gate4c0_shadow_config import (
    Gate4C0AllowlistMetadata,
    Gate4C0BudgetPolicy,
    Gate4C0InputEnvelopeMetadata,
    Gate4C0KillSwitchMetadata,
    Gate4C0MemoryPolicy,
    Gate4C0ModelRoutingMetadata,
    Gate4C0OutputIsolationPolicy,
    Gate4C0RecipeProfileMetadata,
    Gate4C0RedactionPolicy,
    Gate4C0ShadowConfig,
    Gate4C0ToolPolicy,
)
from magi_agent.shadow.gate4c1_runner_shadow_invoker import (
    Gate4C1RunnerShadowInvocationConfig,
    Gate4C1RunnerShadowInvocationResult,
)
from magi_agent.shadow.gate5a_no_memory_shadow_canary import (
    Gate5ANoMemoryShadowCanaryAuthorityFlags,
    Gate5ANoMemoryShadowCanaryConfig,
    Gate5ANoMemoryShadowCanaryPolicy,
    resolve_gate5a_no_memory_shadow_canary,
    run_gate5a_no_memory_shadow_canary,
)


BOT_DIGEST = "sha256:" + "a" * 64
ORG_DIGEST = "sha256:" + "b" * 64
SESSION_DIGEST = "sha256:" + "c" * 64
BUNDLE_DIGEST = "sha256:" + "d" * 64
PROFILE_DIGEST = "sha256:" + "e" * 64


def _output_dir(tmp_path: Path) -> Path:
    path = tmp_path / "adk-shadow-capture" / "gate5a"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _gate4c0_config(
    *,
    enabled: bool = True,
    kill_switch: bool = False,
    redaction_verified: bool = True,
    input_size_bytes: int = 128,
    event_count: int = 2,
    max_input_bytes: int = 8192,
    max_event_count: int = 16,
    max_daily_shadow_runs: int = 100,
    max_queue_depth: int = 25,
    max_cost_usd: float = 1.25,
    tool_mode: str = "disabled",
    memory_mode: str = "disabled",
) -> Gate4C0ShadowConfig:
    return Gate4C0ShadowConfig(
        enabled=enabled,
        allowlist=Gate4C0AllowlistMetadata(
            selectedBotDigest=BOT_DIGEST,
            selectedOrgDigest=ORG_DIGEST,
            environment="staging",
            botAllowlistDigests=(BOT_DIGEST,),
            orgAllowlistDigests=(ORG_DIGEST,),
            environmentAllowlist=("staging",),
        ),
        modelRouting=Gate4C0ModelRoutingMetadata(
            provider="google-adk",
            model="gemini-2.5-pro",
            modelProfile="production-default",
            routingProfileId="prod-routing-shadow-equivalent",
            credentialRef="shadow-provider-credential-ref",
            modelSelectionSource="bot_config_fallback",
        ),
        recipeProfile=Gate4C0RecipeProfileMetadata(
            recipeSnapshotId="recipe-snapshot:gate5a",
            profileId="openmagi-opinionated",
            profileSnapshotDigest=PROFILE_DIGEST,
            selectedPackIds=("openmagi.research",),
        ),
        inputEnvelope=Gate4C0InputEnvelopeMetadata(
            source="gate4b_local_shadow_handoff",
            bundleIdDigest=BUNDLE_DIGEST,
            sessionIdDigest=SESSION_DIGEST,
            turnId="turn-20260518-0001",
            schemaVersion="gate4.localShadowHandoff.v1",
            redactionVerified=redaction_verified,
            inputSizeBytes=input_size_bytes,
            eventCount=event_count,
        ),
        redactionPolicy=Gate4C0RedactionPolicy(
            maxInputBytes=max_input_bytes,
            maxEventCount=max_event_count,
        ),
        toolPolicy=Gate4C0ToolPolicy(mode=tool_mode),  # type: ignore[arg-type]
        memoryPolicy=Gate4C0MemoryPolicy(mode=memory_mode),  # type: ignore[arg-type]
        outputIsolation=Gate4C0OutputIsolationPolicy(),
        budget=Gate4C0BudgetPolicy(
            maxLatencyMs=30000,
            maxQueueDepth=max_queue_depth,
            maxDailyShadowRuns=max_daily_shadow_runs,
            maxCostUsd=max_cost_usd,
        ),
        killSwitch=Gate4C0KillSwitchMetadata(killSwitchEnabled=kill_switch),
    )


def _runner_config(tmp_path: Path, **overrides: object) -> Gate4C1RunnerShadowInvocationConfig:
    base: dict[str, object] = {
        "enabled": True,
        "gate4c0Config": _gate4c0_config(),
        "sanitizedInputText": "Summarize the redacted duplicate turn in one sentence.",
        "outputDir": _output_dir(tmp_path),
        "maxInputChars": 512,
        "maxOutputChars": 96,
        "timeoutMs": 250,
    }
    base.update(overrides)
    return Gate4C1RunnerShadowInvocationConfig(**base)


def _handoff() -> Gate4LocalHandoff:
    return Gate4LocalHandoff(
        bundleId="bundle_gate5a_local_shadow",
        sourceBundleId="source_bundle_gate5a",
        sourcePath="adk-shadow-capture/gate4/report-000001.json",
        generatedAt="2026-05-18T00:00:00Z",
        parityStatus="match",
        redactionVerified=True,
        reportPath=Path("/tmp/adk-shadow-capture/gate4/reports/report-000001.json"),
    )


def _config(tmp_path: Path, **overrides: object) -> Gate5ANoMemoryShadowCanaryConfig:
    base: dict[str, object] = {
        "enabled": True,
        "killSwitchEnabled": False,
        "selectedBotDigest": BOT_DIGEST,
        "selectedOrgDigest": ORG_DIGEST,
        "environment": "staging",
        "botAllowlistDigests": (BOT_DIGEST,),
        "orgAllowlistDigests": (ORG_DIGEST,),
        "environmentAllowlist": ("staging",),
        "runnerConfig": _runner_config(tmp_path),
        "handoff": _handoff(),
        "tsRecordedOutputPreview": "The redacted answer is ready.",
        "outputDir": _output_dir(tmp_path),
        "policy": Gate5ANoMemoryShadowCanaryPolicy(),
        "currentDailyCanaryCount": 0,
        "maxDailyCanaryCount": 100,
        "currentPendingShadowRuns": 0,
        "maxConcurrentShadowRuns": 2,
        "maxInputBytes": 8192,
        "maxOutputChars": 96,
        "timeoutMs": 250,
        "maxCostUsd": 1.25,
    }
    base.update(overrides)
    return Gate5ANoMemoryShadowCanaryConfig(**base)


def _completed_runner_result(**overrides: object) -> Gate4C1RunnerShadowInvocationResult:
    base: dict[str, object] = {
        "status": "completed",
        "reason": "runner_completed",
        "runnerInvoked": True,
        "modelCallViaAdkRunnerAttempted": True,
        "eventCount": 2,
        "outputPreview": "The redacted answer is ready.",
        "outputTruncated": False,
        "outputRedactionApplied": False,
        "latencyMs": 125,
        "timeoutMs": 250,
        "maxOutputChars": 96,
        "maxCostUsd": 1.25,
        "maxQueueDepth": 25,
        "agentKwargsKeys": (
            "description",
            "generate_content_config",
            "instruction",
            "model",
            "name",
            "tools",
        ),
        "runnerKwargsKeys": (
            "agent",
            "app_name",
            "auto_create_session",
            "session_service",
        ),
        "runAsyncKwargsKeys": ("new_message", "session_id", "user_id"),
    }
    base.update(overrides)
    return Gate4C1RunnerShadowInvocationResult(**base)


def _runner_that_must_not_run(_config: Gate4C1RunnerShadowInvocationConfig):
    raise AssertionError("Gate 5A must not invoke Runner for skipped or dropped decisions")


def test_gate5a_is_default_off_without_runner_or_path_access(tmp_path: Path) -> None:
    result = run_gate5a_no_memory_shadow_canary(
        _config(
            tmp_path,
            enabled=False,
            outputDir=Path("/workspace/adk-shadow-capture/gate5a"),
        ),
        runner_invoker=_runner_that_must_not_run,
    )

    assert result.status == "skipped"
    assert result.reason == "canary_disabled"
    assert result.runner_result is None
    assert result.comparison_report is None
    assert result.diagnostics_snapshot is None
    assert result.artifact_path is None
    assert result.attachment_flags.user_visible_output_attached is False
    assert result.attachment_flags.canary_routed is False


@pytest.mark.parametrize(
    ("updates", "reason"),
    (
        ({"botAllowlistDigests": ()}, "missing_bot_allowlist"),
        ({"orgAllowlistDigests": ()}, "missing_org_allowlist"),
        ({"environmentAllowlist": ()}, "missing_environment_allowlist"),
        ({"selectedBotDigest": "sha256:" + "1" * 64}, "bot_not_allowlisted"),
        ({"selectedOrgDigest": "sha256:" + "2" * 64}, "org_not_allowlisted"),
        ({"environment": "production"}, "environment_not_allowlisted"),
    ),
)
def test_gate5a_requires_explicit_bot_org_and_environment_allowlists(
    tmp_path: Path,
    updates: dict[str, object],
    reason: str,
) -> None:
    decision = resolve_gate5a_no_memory_shadow_canary(_config(tmp_path, **updates))

    assert decision.status == "skipped"
    assert decision.reason == reason
    assert decision.attachment_flags.user_visible_output_attached is False
    assert decision.attachment_flags.canary_routed is False


def test_gate5a_kill_switch_skips_without_runner(tmp_path: Path) -> None:
    result = run_gate5a_no_memory_shadow_canary(
        _config(tmp_path, killSwitchEnabled=True),
        runner_invoker=_runner_that_must_not_run,
    )

    assert result.status == "skipped"
    assert result.reason == "kill_switch_enabled"
    assert result.counters.skipped == 1
    assert result.counters.accepted == 0
    assert result.runner_result is None


@pytest.mark.parametrize(
    ("updates", "reason"),
    (
        ({"currentDailyCanaryCount": 100, "maxDailyCanaryCount": 100}, "daily_limit_exhausted"),
        (
            {"currentPendingShadowRuns": 2, "maxConcurrentShadowRuns": 2},
            "concurrency_limit_exhausted",
        ),
    ),
)
def test_gate5a_drops_when_budget_limits_are_exhausted(
    tmp_path: Path,
    updates: dict[str, object],
    reason: str,
) -> None:
    result = run_gate5a_no_memory_shadow_canary(
        _config(tmp_path, **updates),
        runner_invoker=_runner_that_must_not_run,
    )

    assert result.status == "dropped"
    assert result.reason == reason
    assert result.counters.dropped == 1
    assert result.runner_result is None


def test_gate5a_drops_when_runner_identity_does_not_match_canary_target(
    tmp_path: Path,
) -> None:
    mismatched_gate4c0 = _gate4c0_config().model_copy(
        update={
            "allowlist": Gate4C0AllowlistMetadata(
                selectedBotDigest="sha256:" + "1" * 64,
                selectedOrgDigest=ORG_DIGEST,
                environment="staging",
                botAllowlistDigests=("sha256:" + "1" * 64,),
                orgAllowlistDigests=(ORG_DIGEST,),
                environmentAllowlist=("staging",),
            )
        }
    )

    result = run_gate5a_no_memory_shadow_canary(
        _config(
            tmp_path,
            runnerConfig=_runner_config(tmp_path, gate4c0Config=mismatched_gate4c0),
        ),
        runner_invoker=_runner_that_must_not_run,
    )

    assert result.status == "dropped"
    assert result.reason == "shadow_config_mismatch"
    assert result.runner_result is None


def test_gate5a_drops_when_runner_budget_exceeds_canary_caps(tmp_path: Path) -> None:
    result = run_gate5a_no_memory_shadow_canary(
        _config(
            tmp_path,
            timeoutMs=100,
            maxOutputChars=48,
            runnerConfig=_runner_config(tmp_path, timeoutMs=250, maxOutputChars=96),
        ),
        runner_invoker=_runner_that_must_not_run,
    )

    assert result.status == "dropped"
    assert result.reason == "timeout_limit_exceeded"
    assert result.runner_result is None


def test_gate5a_drops_when_cost_cap_is_exhausted_without_runner(tmp_path: Path) -> None:
    result = run_gate5a_no_memory_shadow_canary(
        _config(tmp_path, maxCostUsd=0),
        runner_invoker=_runner_that_must_not_run,
    )

    assert result.status == "dropped"
    assert result.reason == "cost_limit_exhausted"
    assert result.counters.dropped == 1
    assert result.runner_result is None


def test_gate5a_rejects_structured_provider_secrets_without_runner(tmp_path: Path) -> None:
    result = run_gate5a_no_memory_shadow_canary(
        _config(
            tmp_path,
            runnerConfig=_runner_config(
                tmp_path,
                sanitizedInputText='{"access_token": "' + "xoxb" + '-1234567890-unsafe"}',
            ),
        ),
        runner_invoker=_runner_that_must_not_run,
    )

    assert result.status == "dropped"
    assert result.reason == "unsafe_input"
    assert result.counters.redaction_failures == 1
    assert result.runner_result is None


def test_gate5a_rejects_raw_slack_token_without_runner(tmp_path: Path) -> None:
    result = run_gate5a_no_memory_shadow_canary(
        _config(
            tmp_path,
            runnerConfig=_runner_config(
                tmp_path,
                sanitizedInputText="xoxc-1234567890-unsafe",
            ),
        ),
        runner_invoker=_runner_that_must_not_run,
    )

    assert result.status == "dropped"
    assert result.reason == "unsafe_input"
    assert result.counters.redaction_failures == 1
    assert result.runner_result is None


def test_gate5a_drops_unverified_gate4c0_redaction_without_runner(tmp_path: Path) -> None:
    result = run_gate5a_no_memory_shadow_canary(
        _config(
            tmp_path,
            runnerConfig=_runner_config(
                tmp_path,
                gate4c0Config=_gate4c0_config(redaction_verified=False),
            ),
        ),
        runner_invoker=_runner_that_must_not_run,
    )

    assert result.status == "dropped"
    assert result.reason == "redaction_not_verified"
    assert result.counters.redaction_failures == 1
    assert result.runner_result is None


def test_gate5a_drops_unsafe_shadow_input_without_runner(tmp_path: Path) -> None:
    result = run_gate5a_no_memory_shadow_canary(
        _config(
            tmp_path,
            runnerConfig=_runner_config(
                tmp_path,
                sanitizedInputText="Authorization: Bearer unsafe-token",
            ),
        ),
        runner_invoker=_runner_that_must_not_run,
    )

    assert result.status == "dropped"
    assert result.reason == "unsafe_input"
    assert result.counters.redaction_failures == 1
    assert result.runner_result is None


def test_gate5a_drops_unredacted_recorded_output_without_runner(tmp_path: Path) -> None:
    result = run_gate5a_no_memory_shadow_canary(
        _config(tmp_path, tsRecordedOutputPreview="Cookie: sessionid=unsafe"),
        runner_invoker=_runner_that_must_not_run,
    )

    assert result.status == "dropped"
    assert result.reason == "unsafe_input"
    assert result.counters.redaction_failures == 1
    assert result.runner_result is None


def test_gate5a_drops_unverified_handoff_without_runner(tmp_path: Path) -> None:
    result = run_gate5a_no_memory_shadow_canary(
        _config(
            tmp_path,
            handoff=_handoff().model_copy(update={"redaction_verified": False}),
        ),
        runner_invoker=_runner_that_must_not_run,
    )

    assert result.status == "dropped"
    assert result.reason == "redaction_not_verified"
    assert result.counters.redaction_failures == 1
    assert result.runner_result is None


def test_gate5a_promotes_comparison_redaction_violation_to_top_level_failure(
    tmp_path: Path,
) -> None:
    result = run_gate5a_no_memory_shadow_canary(
        _config(tmp_path),
        runner_invoker=lambda _config: _completed_runner_result(
            outputPreview="AIza" + "a" * 32
        ),
    )

    assert result.status == "dropped"
    assert result.reason == "redaction_violation"
    assert result.comparison_report is not None
    assert result.comparison_report.status == "redaction_violation"
    assert result.counters.redaction_failures == 1


def test_gate5a_runs_shadow_invocation_comparison_and_diagnostics_locally(
    tmp_path: Path,
) -> None:
    seen: list[Gate4C1RunnerShadowInvocationConfig] = []

    def fake_runner(config: Gate4C1RunnerShadowInvocationConfig):
        seen.append(config)
        return _completed_runner_result()

    result = run_gate5a_no_memory_shadow_canary(_config(tmp_path), runner_invoker=fake_runner)
    payload = result.model_dump(by_alias=True, mode="json")

    assert result.status == "completed"
    assert result.reason == "shadow_canary_completed"
    assert seen and seen[0].enabled is True
    assert result.runner_result is not None
    assert result.runner_result.status == "completed"
    assert result.runner_result.model_call_via_adk_runner_attempted is True
    assert result.model_selection_source == "bot_config_fallback"
    assert result.comparison_report is not None
    assert result.comparison_report.status == "match"
    assert result.diagnostics_snapshot is not None
    assert result.diagnostics_snapshot.status == "healthy"
    assert result.counters.accepted == 1
    assert result.counters.runner_invoked == 1
    assert result.counters.model_call_via_adk_runner_attempted == 1
    assert result.artifact_path is not None
    assert result.artifact_path.is_file()
    assert payload["schemaVersion"] == "gate5a.noMemoryShadowCanaryRun.v1"
    assert payload["canaryMode"] == "no_memory_shadow_diagnostic"
    assert payload["attachmentFlags"]["userVisibleOutputAttached"] is False
    assert payload["attachmentFlags"]["productionTranscriptWritten"] is False
    assert payload["attachmentFlags"]["productionSseWritten"] is False
    assert payload["attachmentFlags"]["dbWritten"] is False
    assert payload["attachmentFlags"]["channelDelivered"] is False
    assert payload["attachmentFlags"]["toolHostDispatched"] is False
    assert payload["attachmentFlags"]["memoryProviderCalled"] is False
    assert payload["attachmentFlags"]["memoryWritten"] is False
    assert payload["attachmentFlags"]["workspaceMutated"] is False
    assert payload["attachmentFlags"]["canaryRouted"] is False


def test_gate5a_uses_no_memory_and_tools_disabled_policy(tmp_path: Path) -> None:
    result = run_gate5a_no_memory_shadow_canary(
        _config(
            tmp_path,
            runnerConfig=_runner_config(
                tmp_path,
                gate4c0Config=_gate4c0_config(tool_mode="stubbed", memory_mode="read_only"),
            ),
        ),
        runner_invoker=lambda _config: _completed_runner_result(),
    )

    assert result.status == "completed"
    assert result.policy.tools_mode in {"disabled", "stubbed"}
    assert result.policy.memory_mode in {"disabled", "read_only", "test_only"}
    assert result.policy.toolhost_dispatch_attached is False
    assert result.policy.memory_provider_calls_enabled is False
    assert result.policy.memory_writes_enabled is False
    assert result.policy.prompt_injection_enabled is False


def test_gate5a_authority_flags_cannot_be_enabled_by_copy_or_construct() -> None:
    flags = Gate5ANoMemoryShadowCanaryAuthorityFlags()

    copied = flags.model_copy(
        update={
            "userVisibleOutputAttached": True,
            "productionTranscriptWritten": True,
            "productionSseWritten": True,
            "dbWritten": True,
            "channelDelivered": True,
            "workspaceMutated": True,
            "memoryWritten": True,
            "memoryProviderCalled": True,
            "toolHostDispatched": True,
            "liveToolsExecuted": True,
            "productionStorageWritten": True,
            "productionQueueEnqueued": True,
            "telegramAttached": True,
            "billingAuthMutated": True,
            "modelRoutingMutated": True,
            "canaryRouted": True,
        }
    )
    constructed = Gate5ANoMemoryShadowCanaryAuthorityFlags.model_construct(
        user_visible_output_attached=True,
        production_transcript_written=True,
        production_sse_written=True,
        db_written=True,
        channel_delivered=True,
        workspace_mutated=True,
        memory_written=True,
        memory_provider_called=True,
        toolhost_dispatched=True,
        live_tools_executed=True,
        production_storage_written=True,
        production_queue_enqueued=True,
        telegram_attached=True,
        billing_auth_mutated=True,
        model_routing_mutated=True,
        canary_routed=True,
    )

    for item in (copied, constructed):
        assert all(value is False for value in item.model_dump(by_alias=True).values())


def test_gate5a_policy_cannot_enable_live_tools_or_memory_by_copy_or_construct() -> None:
    policy = Gate5ANoMemoryShadowCanaryPolicy()

    copied = policy.model_copy(
        update={
            "toolHostDispatchAttached": True,
            "liveToolsExecuted": True,
            "memoryProviderCallsEnabled": True,
            "memoryWritesEnabled": True,
            "promptInjectionEnabled": True,
        }
    )
    constructed = Gate5ANoMemoryShadowCanaryPolicy.model_construct(
        toolhost_dispatch_attached=True,
        live_tools_executed=True,
        memory_provider_calls_enabled=True,
        memory_writes_enabled=True,
        prompt_injection_enabled=True,
    )

    for item in (copied, constructed):
        assert item.toolhost_dispatch_attached is False
        assert item.live_tools_executed is False
        assert item.memory_provider_calls_enabled is False
        assert item.memory_writes_enabled is False
        assert item.prompt_injection_enabled is False
