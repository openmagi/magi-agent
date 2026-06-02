from __future__ import annotations

import pytest

from magi_agent.shadow.gate4c0_shadow_config import (
    Gate4C0ModelRoutingMetadata,
)
from magi_agent.shadow.gate5b_user_visible_routing_canary import (
    Gate5BNoMemoryRoutingCanaryAuthorityFlags,
    Gate5BNoMemoryRoutingCanaryConfig,
    Gate5BNoMemoryRoutingCanaryPolicy,
    Gate5BNoMemoryRoutingCanaryRuntimeSelector,
    resolve_gate5b_no_memory_routing_canary_status,
)


BOT_DIGEST = "sha256:" + "a" * 64
ORG_DIGEST = "sha256:" + "b" * 64
TARGET_DIGEST = "sha256:" + "c" * 64


def _model_routing(
    *,
    provider: str = "google-adk",
    model: str = "gemini-2.5-pro",
    source: str = "per_turn_injected",
) -> Gate4C0ModelRoutingMetadata:
    return Gate4C0ModelRoutingMetadata(
        provider=provider,
        model=model,
        modelProfile="production-default",
        routingProfileId="prod-routing-shadow-equivalent",
        credentialRef="shadow-provider-credential-ref",
        modelSelectionSource=source,
    )


def _config(**overrides: object) -> Gate5BNoMemoryRoutingCanaryConfig:
    base: dict[str, object] = {
        "selectedBotDigest": BOT_DIGEST,
        "selectedOrgDigest": ORG_DIGEST,
        "environment": "staging",
        "modelRouting": _model_routing(),
    }
    base.update(overrides)
    return Gate5BNoMemoryRoutingCanaryConfig(**base)


def test_gate5b_schema_is_disabled_and_non_authoritative_by_default() -> None:
    status = resolve_gate5b_no_memory_routing_canary_status(_config())

    assert status.status == "disabled"
    assert status.reason == "canary_disabled"
    assert status.canary_mode == "disabled"
    assert status.runtime_selector.desired_state == "inactive"
    assert status.runtime_selector.active_routing_percentage == 0
    assert status.policy.no_memory_required is True
    assert status.policy.tools_disabled_required is True
    assert status.policy.memory_disabled_required is True
    assert status.authority_flags.user_visible_output_attached is False
    assert status.authority_flags.runtime_selector_active is False
    assert status.authority_flags.canary_routed is False


def test_gate5b_requires_future_approval_marker_for_active_user_visible_mode() -> None:
    with pytest.raises(ValueError, match="future approval"):
        _config(enabled=True, canaryMode="active_user_visible")

    status = resolve_gate5b_no_memory_routing_canary_status(
        _config(
            enabled=True,
            canaryMode="active_user_visible",
            killSwitchEnabled=False,
            futureApprovalMarker="future-approval:gate5b-runtime-routing",
            botAllowlistDigests=(BOT_DIGEST,),
            orgAllowlistDigests=(ORG_DIGEST,),
            environmentAllowlist=("staging",),
        )
    )

    assert status.status == "pending_approval"
    assert status.reason == "runtime_routing_not_authorized"
    assert status.authority_flags.user_visible_output_attached is False
    assert status.authority_flags.runtime_selector_active is False


def test_gate5b_runtime_selector_metadata_is_inactive_even_with_planned_targets() -> None:
    runtime_selector = Gate5BNoMemoryRoutingCanaryRuntimeSelector(
        plannedRoutingPercentage=10,
        plannedFixedTargetDigests=(TARGET_DIGEST,),
        desiredState="active",
        runtimeSelectorActive=True,
    )

    assert runtime_selector.desired_state == "inactive"
    assert runtime_selector.runtime_selector_active is False
    assert runtime_selector.active_routing_percentage == 0
    assert runtime_selector.planned_routing_percentage == 10
    assert runtime_selector.planned_fixed_target_digests == (TARGET_DIGEST,)


def test_gate5b_policy_and_authority_flags_cannot_be_enabled_by_model_bypass() -> None:
    flags = Gate5BNoMemoryRoutingCanaryAuthorityFlags.model_construct(
        userVisibleOutputAttached=True,
        runtimeSelectorActive=True,
        toolHostDispatched=True,
        memoryWritten=True,
        canaryRouted=True,
    )
    copied_flags = flags.model_copy(
        update={
            "userVisibleOutputAttached": True,
            "runtimeSelectorActive": True,
            "canaryRouted": True,
        }
    )

    assert flags.user_visible_output_attached is False
    assert flags.runtime_selector_active is False
    assert flags.toolhost_dispatched is False
    assert flags.memory_written is False
    assert flags.canary_routed is False
    assert copied_flags.user_visible_output_attached is False
    assert copied_flags.runtime_selector_active is False
    assert copied_flags.canary_routed is False

    policy = Gate5BNoMemoryRoutingCanaryPolicy.model_construct(
        noMemoryRequired=False,
        toolsDisabledRequired=False,
        memoryDisabledRequired=False,
        workspaceMutationDisabled=False,
        childExecutionDisabled=False,
        evidenceBlockDisabled=False,
        pythonResponseAdoptionDisabled=False,
    )
    copied_policy = policy.model_copy(
        update={
            "noMemoryRequired": False,
            "toolsDisabledRequired": False,
            "pythonResponseAdoptionDisabled": False,
        }
    )

    assert policy.no_memory_required is True
    assert policy.tools_disabled_required is True
    assert policy.memory_disabled_required is True
    assert policy.workspace_mutation_disabled is True
    assert policy.child_execution_disabled is True
    assert policy.evidence_block_disabled is True
    assert policy.python_response_adoption_disabled is True
    assert copied_policy.no_memory_required is True
    assert copied_policy.tools_disabled_required is True
    assert copied_policy.python_response_adoption_disabled is True


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
def test_gate5b_eligibility_metadata_requires_allowlisted_scope(
    updates: dict[str, object],
    reason: str,
) -> None:
    base_updates: dict[str, object] = {
        "enabled": True,
        "killSwitchEnabled": False,
        "canaryMode": "candidate_user_visible",
        "futureApprovalMarker": "future-approval:gate5b-runtime-routing",
        "botAllowlistDigests": (BOT_DIGEST,),
        "orgAllowlistDigests": (ORG_DIGEST,),
        "environmentAllowlist": ("staging",),
    }
    base_updates.update(updates)
    config = _config(
        **base_updates,
    )

    status = resolve_gate5b_no_memory_routing_canary_status(config)

    assert status.status == "skipped"
    assert status.reason == reason
    assert status.authority_flags.user_visible_output_attached is False
    assert status.authority_flags.runtime_selector_active is False


@pytest.mark.parametrize(
    "bad_value",
    (
        "bot-raw-id",
        "sha256:nothex",
        "sk-secret-token",
        "/workspace/raw/path",
    ),
)
def test_gate5b_rejects_invalid_or_unsafe_allowlist_metadata(bad_value: str) -> None:
    with pytest.raises(ValueError):
        _config(botAllowlistDigests=(bad_value,))


@pytest.mark.parametrize(
    ("environment", "environment_allowlist"),
    (
        ("", ("staging",)),
        ("   ", ("staging",)),
        ("staging", ("",)),
        ("staging", ("   ",)),
        ("unknown", ("unknown",)),
    ),
)
def test_gate5b_rejects_blank_or_unknown_environment_metadata(
    environment: str,
    environment_allowlist: tuple[str, ...],
) -> None:
    with pytest.raises(ValueError):
        _config(
            environment=environment,
            environmentAllowlist=environment_allowlist,
        )


def test_gate5b_status_records_turn_scoped_model_selection_source() -> None:
    status = resolve_gate5b_no_memory_routing_canary_status(
        _config(
            modelRouting=_model_routing(
                provider="openai",
                model="gpt-5.4",
                source="per_turn_injected",
            )
        )
    )

    assert status.model_selection_source == "per_turn_injected"
    assert status.selected_provider == "openai"
    assert status.selected_model == "gpt-5.4"

    fallback_status = resolve_gate5b_no_memory_routing_canary_status(
        _config(
            modelRouting=_model_routing(
                provider="google-adk",
                model="gemini-2.5-pro",
                source="bot_config_fallback",
            )
        )
    )

    assert fallback_status.model_selection_source == "bot_config_fallback"
    assert fallback_status.selected_model == "gemini-2.5-pro"
