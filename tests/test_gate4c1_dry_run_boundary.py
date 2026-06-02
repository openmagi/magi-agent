from __future__ import annotations

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
from magi_agent.shadow.gate4c1_dry_run_boundary import (
    Gate4C1DryRunBoundaryConfig,
    Gate4C1DryRunBoundaryFlags,
    evaluate_gate4c1_dry_run_boundary,
)


BOT_DIGEST = "sha256:" + "a" * 64
ORG_DIGEST = "sha256:" + "b" * 64
SESSION_DIGEST = "sha256:" + "c" * 64
BUNDLE_DIGEST = "sha256:" + "d" * 64
PROFILE_DIGEST = "sha256:" + "e" * 64


def _gate4c0_config(*, enabled: bool = True, kill_switch: bool = False) -> Gate4C0ShadowConfig:
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
            provider="openai",
            model="gpt-5.4",
            modelProfile="production-default",
            routingProfileId="prod-routing-shadow-equivalent",
            credentialRef="shadow-provider-credential-ref",
            modelSelectionSource="bot_config_fallback",
        ),
        recipeProfile=Gate4C0RecipeProfileMetadata(
            recipeSnapshotId="recipe-snapshot:gate4c0",
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
            redactionVerified=True,
            inputSizeBytes=4096,
            eventCount=8,
        ),
        redactionPolicy=Gate4C0RedactionPolicy(maxInputBytes=8192, maxEventCount=16),
        toolPolicy=Gate4C0ToolPolicy(mode="disabled"),
        memoryPolicy=Gate4C0MemoryPolicy(mode="read_only"),
        outputIsolation=Gate4C0OutputIsolationPolicy(),
        budget=Gate4C0BudgetPolicy(
            maxLatencyMs=30000,
            maxQueueDepth=25,
            maxDailyShadowRuns=100,
            maxCostUsd=1.25,
        ),
        killSwitch=Gate4C0KillSwitchMetadata(killSwitchEnabled=kill_switch),
    )


def test_gate4c1_dry_run_accepts_ready_gate4c0_config_without_runner_or_model() -> None:
    result = evaluate_gate4c1_dry_run_boundary(
        Gate4C1DryRunBoundaryConfig(enabled=True, gate4c0Config=_gate4c0_config())
    )
    payload = result.model_dump(by_alias=True, mode="json")

    assert result.status == "ready_pending_runner_approval"
    assert result.reason == "gate4c1_requires_runner_implementation_approval"
    assert result.dry_run_only is True
    assert payload["schemaVersion"] == "gate4c1.dryRunBoundaryDecision.v1"
    assert payload["attachmentFlags"]["adkRunnerImported"] is False
    assert payload["attachmentFlags"]["adkRunnerInvoked"] is False
    assert payload["attachmentFlags"]["modelCalled"] is False
    assert payload["attachmentFlags"]["promptConstructed"] is False
    assert payload["attachmentFlags"]["toolHostDispatched"] is False
    assert payload["attachmentFlags"]["userVisibleOutputAttached"] is False


def test_gate4c1_dry_run_skips_when_disabled() -> None:
    result = evaluate_gate4c1_dry_run_boundary(
        Gate4C1DryRunBoundaryConfig(enabled=False, gate4c0Config=_gate4c0_config())
    )

    assert result.status == "skipped"
    assert result.reason == "dry_run_disabled"
    assert result.attachment_flags.adk_runner_invoked is False


def test_gate4c1_dry_run_skips_when_gate4c0_not_accepted() -> None:
    result = evaluate_gate4c1_dry_run_boundary(
        Gate4C1DryRunBoundaryConfig(
            enabled=True,
            gate4c0Config=_gate4c0_config(kill_switch=True),
        )
    )

    assert result.status == "skipped"
    assert result.reason == "gate4c0_not_accepted"
    assert result.gate4c0_reason == "kill_switch_enabled"


def test_gate4c1_dry_run_flags_cannot_be_enabled_by_copy_or_construct() -> None:
    flags = Gate4C1DryRunBoundaryFlags()

    copied = flags.model_copy(
        update={
            "adkRunnerImported": True,
            "adkRunnerInvoked": True,
            "modelCalled": True,
            "promptConstructed": True,
            "toolHostDispatched": True,
            "userVisibleOutputAttached": True,
        }
    )
    constructed = Gate4C1DryRunBoundaryFlags.model_construct(
        adk_runner_imported=True,
        adk_runner_invoked=True,
        model_called=True,
        prompt_constructed=True,
        toolhost_dispatched=True,
        user_visible_output_attached=True,
    )

    for payload in (
        copied.model_dump(by_alias=True, mode="json"),
        constructed.model_dump(by_alias=True, mode="json"),
    ):
        assert all(value is False for value in payload.values())
