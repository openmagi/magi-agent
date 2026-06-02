from __future__ import annotations

import pytest

from magi_agent.shadow.gate5b4_internal_endpoint_contract import (
    Gate5B4EndpointAuthorityFlags,
    Gate5B4EndpointContractConfig,
    Gate5B4InternalEndpointContract,
    build_gate5b4_internal_endpoint_contract,
)


BOT_DIGEST = "sha256:" + "a" * 64
ORG_DIGEST = "sha256:" + "b" * 64


def _config(**overrides: object) -> Gate5B4EndpointContractConfig:
    base: dict[str, object] = {
        "selectedBotDigest": BOT_DIGEST,
        "selectedOrgDigest": ORG_DIGEST,
        "environment": "staging",
    }
    base.update(overrides)
    return Gate5B4EndpointContractConfig(**base)


def test_gate5b4_default_contract_is_health_only_and_non_authoritative() -> None:
    contract = build_gate5b4_internal_endpoint_contract(_config())

    assert contract.health_status == "healthy"
    assert contract.readiness_status == "not_ready"
    assert contract.mode == "health_only"
    assert contract.canary_capability_status == "health_only"
    assert contract.supported_modes == (
        "health_only",
        "shadow_diagnostic_only",
        "candidate_user_visible_pending_approval",
    )
    assert contract.response_authority == "none"
    assert contract.metadata_source == "validated_config"
    assert contract.selected_bot_digest == BOT_DIGEST
    assert contract.selected_org_digest == ORG_DIGEST
    assert contract.environment == "staging"
    assert contract.authority_flags.user_visible_output_allowed is False
    assert contract.authority_flags.transcript_write_allowed is False
    assert contract.authority_flags.sse_write_allowed is False
    assert contract.authority_flags.channel_delivery_allowed is False
    assert contract.authority_flags.db_write_allowed is False
    assert contract.authority_flags.workspace_mutation_allowed is False
    assert contract.authority_flags.memory_write_allowed is False
    assert contract.authority_flags.tool_dispatch_allowed is False
    assert contract.authority_flags.canary_routing_allowed is False
    assert contract.chat_proxy_call_allowed is False
    assert contract.runtime_selector_activation_allowed is False
    assert contract.model_call_endpoint_exposed is False
    assert contract.adk_runner_endpoint_exposed is False
    assert contract.public_route_exposed is False


def test_gate5b4_candidate_user_visible_mode_remains_pending_approval() -> None:
    contract = build_gate5b4_internal_endpoint_contract(
        _config(
            mode="candidate_user_visible_pending_approval",
            diagnosticPreview="candidate endpoint contract only",
        )
    )

    assert contract.mode == "candidate_user_visible_pending_approval"
    assert contract.readiness_status == "not_ready"
    assert contract.canary_capability_status == "pending_approval"
    assert contract.response_authority == "diagnostic_only"
    assert contract.user_visible_response_envelope_possible is False
    assert contract.authority_flags.user_visible_output_allowed is False
    assert contract.authority_flags.canary_routing_allowed is False


def test_gate5b4_shadow_diagnostic_mode_is_local_diagnostic_only() -> None:
    contract = build_gate5b4_internal_endpoint_contract(
        _config(mode="shadow_diagnostic_only")
    )

    assert contract.mode == "shadow_diagnostic_only"
    assert contract.readiness_status == "ready"
    assert contract.canary_capability_status == "shadow_diagnostic_only"
    assert contract.response_authority == "diagnostic_only"
    assert contract.user_visible_response_envelope_possible is False
    assert contract.authority_flags.user_visible_output_allowed is False


def test_gate5b4_authority_flags_cannot_be_enabled_by_model_bypass() -> None:
    flags = Gate5B4EndpointAuthorityFlags.model_construct(
        userVisibleOutputAllowed=True,
        transcriptWriteAllowed=True,
        sseWriteAllowed=True,
        channelDeliveryAllowed=True,
        dbWriteAllowed=True,
        workspaceMutationAllowed=True,
        memoryWriteAllowed=True,
        toolDispatchAllowed=True,
        canaryRoutingAllowed=True,
    )
    copied = flags.model_copy(
        update={
            "userVisibleOutputAllowed": True,
            "canaryRoutingAllowed": True,
            "memoryWriteAllowed": True,
        }
    )

    assert flags.user_visible_output_allowed is False
    assert flags.transcript_write_allowed is False
    assert flags.sse_write_allowed is False
    assert flags.channel_delivery_allowed is False
    assert flags.db_write_allowed is False
    assert flags.workspace_mutation_allowed is False
    assert flags.memory_write_allowed is False
    assert flags.tool_dispatch_allowed is False
    assert flags.canary_routing_allowed is False
    assert copied.user_visible_output_allowed is False
    assert copied.canary_routing_allowed is False
    assert copied.memory_write_allowed is False


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("selectedBotDigest", "bot-raw-id"),
        ("selectedOrgDigest", "org-raw-id"),
        ("environment", "unknown"),
        ("environment", " staging "),
    ),
)
def test_gate5b4_rejects_unvalidated_bot_org_env_metadata(
    field: str,
    value: str,
) -> None:
    with pytest.raises(ValueError):
        _config(**{field: value})


def test_gate5b4_diagnostic_preview_is_redacted_and_capped() -> None:
    contract = build_gate5b4_internal_endpoint_contract(
        _config(
            diagnosticPreview=(
                "shadow preview Authorization: Bearer secret-token "
                "/workspace/raw/path "
                + "x" * 500
            )
        )
    )

    assert contract.diagnostic_preview_public is not None
    assert "Bearer" not in contract.diagnostic_preview_public
    assert "/workspace/raw/path" not in contract.diagnostic_preview_public
    assert "[redacted]" in contract.diagnostic_preview_public
    assert len(contract.diagnostic_preview_public) <= 240


def test_gate5b4_contract_cannot_model_user_visible_response_envelope() -> None:
    baseline = build_gate5b4_internal_endpoint_contract(
        _config(mode="candidate_user_visible_pending_approval")
    )
    contract = Gate5B4InternalEndpointContract.model_validate(
        baseline.model_dump(by_alias=True, mode="python")
        | {
            "userVisibleResponseEnvelopePossible": True,
            "responseAuthority": "python",
            "publicRouteExposed": True,
        }
    )

    assert contract.response_authority == "diagnostic_only"
    assert contract.user_visible_response_envelope_possible is False
    assert contract.public_route_exposed is False
    assert contract.authority_flags.user_visible_output_allowed is False


def test_gate5b4_contract_revalidates_metadata_on_direct_model_validation() -> None:
    baseline = build_gate5b4_internal_endpoint_contract(_config())

    with pytest.raises(ValueError):
        Gate5B4InternalEndpointContract.model_validate(
            baseline.model_dump(by_alias=True, mode="python")
            | {"selectedBotDigest": "bot-raw-id"}
        )
    with pytest.raises(ValueError):
        Gate5B4InternalEndpointContract.model_validate(
            baseline.model_dump(by_alias=True, mode="python")
            | {"environment": " production "}
        )


def test_gate5b4_contract_redacts_direct_public_preview_inputs() -> None:
    baseline = build_gate5b4_internal_endpoint_contract(_config())
    contract = Gate5B4InternalEndpointContract.model_validate(
        baseline.model_dump(by_alias=True, mode="python")
        | {
            "diagnosticPreviewPublic": (
                "unsafe Bearer secret-token /workspace/raw/path " + "x" * 500
            )
        }
    )

    assert contract.diagnostic_preview_public is not None
    assert "Bearer" not in contract.diagnostic_preview_public
    assert "/workspace/raw/path" not in contract.diagnostic_preview_public
    assert "[redacted]" in contract.diagnostic_preview_public
    assert len(contract.diagnostic_preview_public) <= 240


def test_gate5b4_contract_derives_status_tuple_from_mode_on_direct_validation() -> None:
    baseline = build_gate5b4_internal_endpoint_contract(
        _config(mode="candidate_user_visible_pending_approval")
    )
    contract = Gate5B4InternalEndpointContract.model_validate(
        baseline.model_dump(by_alias=True, mode="python")
        | {
            "readinessStatus": "ready",
            "canaryCapabilityStatus": "shadow_diagnostic_only",
            "responseAuthority": "none",
        }
    )

    assert contract.mode == "candidate_user_visible_pending_approval"
    assert contract.readiness_status == "not_ready"
    assert contract.canary_capability_status == "pending_approval"
    assert contract.response_authority == "diagnostic_only"
