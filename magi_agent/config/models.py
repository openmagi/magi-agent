from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, ClassVar, Literal, Self

from pydantic import (
    AnyUrl,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from magi_agent.ops.authority import FalseOnlyAuthorityModel


_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_PUBLIC_CONTEXT_CONTINUITY_REASON_CODES = {
    "antecedent_missing",
    "antecedent_present",
    "committed_history_imported",
    "compaction_boundary_failed",
    "compaction_boundary_respected",
    "continuity_digest_missing",
    "fallback_active",
    "fallback_none",
    "followup_missing",
    "followup_present",
    "forbidden_payload_absent",
    "forbidden_payload_observed",
    "history_budget_truncated",
    "private_payload_rejected",
    "private_payload_rejection_missing",
    "runner_completed",
    "runner_not_completed",
}


class BuildInfo(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    version: str = "0.1.0-adk-scaffold"
    build_sha: str | None = Field(default=None, alias="buildSha")
    image_repo: str | None = Field(default=None, alias="imageRepo")
    image_tag: str | None = Field(default=None, alias="imageTag")
    image_digest: str | None = Field(default=None, alias="imageDigest")


class _FalseOnlyModel(FalseOnlyAuthorityModel):
    """Thin alias for the canonical ``FalseOnlyAuthorityModel`` (C-4 PR-B).

    Pre-C-4 PR-B this class hand-rolled a ``model_validator(mode="before")`` +
    ``model_construct``/``model_copy``/``copy`` overrides driven by a
    per-subclass ``_FALSE_ONLY_FIELDS`` ClassVar tuple. The shared
    ``FalseOnlyAuthorityModel`` introspects ``Literal[False]`` field annotations
    instead, so the tuple is dead weight. The alias is preserved so existing
    ``class Foo(_FalseOnlyModel)`` lines keep working without per-class edits.
    """


class PythonMemoryAdapterConfig(_FalseOnlyModel):
    enabled: bool = False
    mode: Literal["disabled", "readonly_fixture", "readonly_local"] = "disabled"
    adapter: str = "off"
    workspace_root: str | None = Field(default=None, alias="workspaceRoot")
    prompt_projection_enabled: Literal[False] = Field(
        default=False,
        alias="promptProjectionEnabled",
    )
    live_provider_calls_enabled: Literal[False] = Field(
        default=False,
        alias="liveProviderCallsEnabled",
    )
    adk_memory_service_attachment_enabled: Literal[False] = Field(
        default=False,
        alias="adkMemoryServiceAttachmentEnabled",
    )

    @field_validator("adapter")
    @classmethod
    def _validate_adapter_ref(cls, value: str) -> str:
        normalized = value.strip().lower().replace("-", "_")
        if normalized == "off":
            return "off"
        if not normalized.replace("_", "").isalnum() or len(normalized) > 80:
            raise ValueError("memory adapter must be off or a safe provider adapter ref")
        return normalized


class PythonToolHostAttachmentConfig(_FalseOnlyModel):
    enabled: bool = False
    mode: Literal["disabled", "shadow_readonly"] = "disabled"
    production_attachment_enabled: Literal[False] = Field(
        default=False,
        alias="productionAttachmentEnabled",
    )
    live_tool_mutation_enabled: Literal[False] = Field(
        default=False,
        alias="liveToolMutationEnabled",
    )


class PythonSecurityPostureConfig(_FalseOnlyModel):
    enabled: bool = False
    posture_preflight_attached: bool = Field(
        default=False,
        alias="posturePreflightAttached",
    )
    external_surface_dispatch_attached: Literal[False] = Field(
        default=False,
        alias="externalSurfaceDispatchAttached",
    )
    credential_broker_attached: Literal[False] = Field(
        default=False,
        alias="credentialBrokerAttached",
    )
    context_guard_blocks_prompt_projection: Literal[False] = Field(
        default=False,
        alias="contextGuardBlocksPromptProjection",
    )
    supply_chain_startup_banner_attached: Literal[False] = Field(
        default=False,
        alias="supplyChainStartupBannerAttached",
    )

    @model_validator(mode="before")
    @classmethod
    def _derive_enabled_from_preflight(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        payload = dict(value)
        preflight = (
            payload.get("posture_preflight_attached") is True
            or payload.get("posturePreflightAttached") is True
        )
        payload["enabled"] = preflight
        return payload


class PythonContextContinuityConfig(_FalseOnlyModel):
    enabled: bool = False
    mode: Literal["disabled", "local_diagnostic", "selected_canary"] = "disabled"
    canary_status: Literal["missing", "pass", "fail"] = Field(
        default="missing",
        alias="canaryStatus",
    )
    imported_event_count: int = Field(default=0, ge=0, alias="importedEventCount")
    rejected_entry_count: int = Field(default=0, ge=0, alias="rejectedEntryCount")
    compaction_applied: bool = Field(default=False, alias="compactionApplied")
    projection_digest_present: bool = Field(
        default=False,
        alias="projectionDigestPresent",
    )
    model_visible_digest_present: bool = Field(
        default=False,
        alias="modelVisibleDigestPresent",
    )
    source_transcript_head_digest_present: bool = Field(
        default=False,
        alias="sourceTranscriptHeadDigestPresent",
    )
    canary_evidence_verified: bool = Field(
        default=False,
        alias="canaryEvidenceVerified",
    )
    canary_evidence_source: Literal[
        "none",
        "local_verified_evidence",
    ] = Field(default="none", alias="canaryEvidenceSource")
    fallback_status: Literal[
        "missing",
        "none",
        "closed",
        "typescript_fallback",
        "failed",
        "unavailable",
    ] = Field(default="missing", alias="fallbackStatus")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    production_authority_allowed: Literal[False] = Field(
        default=False,
        alias="productionAuthorityAllowed",
    )
    transcript_write_allowed: Literal[False] = Field(
        default=False,
        alias="transcriptWriteAllowed",
    )
    sse_write_allowed: Literal[False] = Field(default=False, alias="sseWriteAllowed")
    db_write_allowed: Literal[False] = Field(default=False, alias="dbWriteAllowed")

    @model_validator(mode="before")
    @classmethod
    def _force_manual_evidence_source_none(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        payload = dict(value)
        payload.pop("canary_evidence_source", None)
        payload["canaryEvidenceSource"] = "none"
        # ``canary_evidence_verified`` is typed ``bool`` (not ``Literal[False]``)
        # so the introspection-based ``FalseOnlyAuthorityModel`` base does NOT
        # force it. The legacy class enumerated it in ``_FALSE_ONLY_FIELDS``
        # specifically to force-false external construction with True; the
        # ``from_canary_evidence`` classmethod then uses ``object.__setattr__``
        # to bypass and set True when evidence verifies. Preserve that
        # protection by force-falsing here.
        payload.pop("canary_evidence_verified", None)
        payload["canaryEvidenceVerified"] = False
        return payload

    @property
    def continuity_canary_ready(self) -> bool:
        return (
            self.enabled
            and self.mode == "selected_canary"
            and self.canary_status == "pass"
            and self.imported_event_count > 0
            and self.projection_digest_present
            and self.model_visible_digest_present
            and self.source_transcript_head_digest_present
            and self.canary_evidence_verified
            and self.canary_evidence_source == "local_verified_evidence"
            and (self.rejected_entry_count == 0 or bool(self.reason_codes))
            and self.fallback_status == "none"
        )

    @property
    def gate8_block_reason(self) -> str:
        if self.continuity_canary_ready:
            return "pre_gate8_continuity_canary_pass"
        if not self.enabled or self.mode != "selected_canary" or self.canary_status == "missing":
            return "pre_gate8_continuity_canary_missing"
        if self.fallback_status != "none":
            return "pre_gate8_continuity_fallback_active"
        if (
            self.imported_event_count <= 0
            or not self.projection_digest_present
            or not self.model_visible_digest_present
            or not self.source_transcript_head_digest_present
        ):
            return "pre_gate8_continuity_evidence_incomplete"
        if self.canary_status == "fail":
            return "pre_gate8_continuity_canary_failed"
        if (
            not self.canary_evidence_verified
            or self.canary_evidence_source != "local_verified_evidence"
        ):
            return "pre_gate8_continuity_evidence_unverified"
        return "pre_gate8_continuity_canary_missing"

    @property
    def health_metadata(self) -> dict[str, object]:
        return {
            "continuityEnabled": self.enabled,
            "continuityCanaryReady": self.continuity_canary_ready,
            "mode": self.mode,
            "canaryStatus": self.canary_status,
            "importedEventCount": self.imported_event_count,
            "rejectedEntryCount": self.rejected_entry_count,
            "compactionApplied": self.compaction_applied,
            "projectionDigestPresent": self.projection_digest_present,
            "modelVisibleDigestPresent": self.model_visible_digest_present,
            "sourceTranscriptHeadDigestPresent": (
                self.source_transcript_head_digest_present
            ),
            "canaryEvidenceVerified": self.canary_evidence_verified,
            "canaryEvidenceSource": self.canary_evidence_source,
            "fallbackStatus": self.fallback_status,
            "reasonCodes": list(self.reason_codes),
            "productionAuthorityAllowed": False,
            "transcriptWriteAllowed": False,
            "sseWriteAllowed": False,
            "dbWriteAllowed": False,
        }

    @classmethod
    def from_canary_evidence(cls, evidence: object) -> Self:
        from magi_agent.gates.pregate8_continuity_canary import (
            PreGate8ContinuityCanaryEvidence,
        )

        is_typed_canary_evidence = isinstance(evidence, PreGate8ContinuityCanaryEvidence)
        status = _safe_literal(
            getattr(evidence, "status", "fail"),
            allowed=("pass", "fail"),
            default="fail",
        )
        fallback_status = _safe_literal(
            getattr(evidence, "fallback_status", "failed"),
            allowed=(
                "none",
                "closed",
                "typescript_fallback",
                "failed",
                "unavailable",
            ),
            default="failed",
        )
        imported_event_count = _nonnegative_int_attr(evidence, "imported_event_count")
        rejected_entry_count = _nonnegative_int_attr(evidence, "rejected_entry_count")
        projection_digest_present = _digest_attr_present(evidence, "projection_digest")
        model_visible_digest_present = _digest_attr_present(evidence, "model_visible_digest")
        source_transcript_head_digest_present = _digest_attr_present(
            evidence,
            "source_transcript_head_digest",
        )
        observed_adk_session_digest_present = _digest_attr_present(
            evidence,
            "observed_adk_session_digest",
        )
        observed_model_visible_digest_present = _digest_attr_present(
            evidence,
            "observed_model_visible_digest",
        )
        antecedent_digest_present = _digest_attr_present(evidence, "antecedent_digest")
        current_followup_digest_present = _digest_attr_present(
            evidence,
            "current_followup_digest",
        )
        reason_codes = _safe_reason_code_tuple(getattr(evidence, "reason_codes", ()))
        private_payload_rejected = getattr(evidence, "private_payload_rejected", False) is True
        evidence_verified = all(
            (
                is_typed_canary_evidence,
                status == "pass",
                getattr(evidence, "local_only", False) is True,
                getattr(evidence, "diagnostic_only", False) is True,
                getattr(evidence, "response_authority", "") == "none",
                imported_event_count > 0,
                projection_digest_present,
                model_visible_digest_present,
                source_transcript_head_digest_present,
                observed_adk_session_digest_present,
                observed_model_visible_digest_present,
                antecedent_digest_present,
                current_followup_digest_present,
                fallback_status == "none",
                "fallback_none" in reason_codes,
                "fallback_active" not in reason_codes,
                getattr(evidence, "compaction_boundary_respected", True) is True,
                getattr(evidence, "forbidden_payload_observed", False) is False,
                rejected_entry_count == 0 or private_payload_rejected,
                (
                    rejected_entry_count == 0
                    or (
                        "private_payload_rejected" in reason_codes
                        and "private_payload_rejection_missing" not in reason_codes
                    )
                ),
                (
                    getattr(evidence, "antecedent_present_in_adk_session", False) is True
                    or getattr(
                        evidence,
                        "antecedent_present_in_model_visible_projection",
                        False,
                    )
                    is True
                ),
                getattr(
                    evidence,
                    "current_followup_present_in_model_visible_message",
                    False,
                )
                is True,
                rejected_entry_count == 0 or bool(reason_codes),
            )
        )
        config = cls(
            enabled=True,
            mode="selected_canary",
            canaryStatus=status,
            importedEventCount=imported_event_count,
            rejectedEntryCount=rejected_entry_count,
            compactionApplied=getattr(evidence, "compaction_applied", False) is True,
            projectionDigestPresent=projection_digest_present,
            modelVisibleDigestPresent=model_visible_digest_present,
            sourceTranscriptHeadDigestPresent=source_transcript_head_digest_present,
            canaryEvidenceVerified=False,
            canaryEvidenceSource="none",
            fallbackStatus=fallback_status,
            reasonCodes=reason_codes,
            productionAuthorityAllowed=False,
            transcriptWriteAllowed=False,
            sseWriteAllowed=False,
            dbWriteAllowed=False,
        )
        if evidence_verified:
            object.__setattr__(config, "canary_evidence_verified", True)
            object.__setattr__(config, "canary_evidence_source", "local_verified_evidence")
        return config

    @field_validator("reason_codes")
    @classmethod
    def _validate_reason_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _safe_reason_code_tuple(value)


def _safe_literal(value: object, *, allowed: tuple[str, ...], default: str) -> str:
    text = str(value).strip().lower()
    return text if text in allowed else default


def _nonnegative_int_attr(value: object, name: str) -> int:
    raw = getattr(value, name, 0)
    if isinstance(raw, bool):
        return 0
    if isinstance(raw, int):
        return max(0, raw)
    return 0


def _digest_attr_present(value: object, name: str) -> bool:
    raw = getattr(value, name, None)
    return isinstance(raw, str) and _DIGEST_RE.fullmatch(raw) is not None


def _safe_reason_code_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        candidates = (value,)
    elif isinstance(value, tuple | list):
        candidates = tuple(str(item) for item in value)
    else:
        candidates = ()
    safe = [code for code in candidates if code in _PUBLIC_CONTEXT_CONTINUITY_REASON_CODES]
    return tuple(dict.fromkeys(safe))


class PythonGate2ReadinessConfig(_FalseOnlyModel):
    enabled: bool = False
    kill_switch_enabled: bool = Field(default=True, alias="killSwitchEnabled")
    local_sandbox_harness_enabled: bool = Field(
        default=False,
        alias="localSandboxHarnessEnabled",
    )
    selected_bot_digest: str = Field(default="", alias="selectedBotDigest")
    selected_owner_user_id_digest: str = Field(
        default="",
        alias="selectedOwnerUserIdDigest",
    )
    environment: str = "local"
    environment_allowlist: tuple[str, ...] = Field(
        default=(),
        alias="environmentAllowlist",
    )
    profile_ref: str = Field(
        default="openmagi.gate2.workspace-canary.v1",
        alias="profileRef",
    )
    profile_digest: str = Field(default="", alias="profileDigest")
    max_mutation_attempts_per_turn: int = Field(
        default=0,
        ge=0,
        le=64,
        alias="maxMutationAttemptsPerTurn",
    )
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")
    production_workspace_mutation_allowed: Literal[False] = Field(
        default=False,
        alias="productionWorkspaceMutationAllowed",
    )
    write_mutation_authority_allowed: Literal[False] = Field(
        default=False,
        alias="writeMutationAuthorityAllowed",
    )
    user_visible_output_allowed: Literal[False] = Field(
        default=False,
        alias="userVisibleOutputAllowed",
    )
    tool_host_dispatch_allowed: Literal[False] = Field(
        default=False,
        alias="toolHostDispatchAllowed",
    )
    live_tool_execution_allowed: Literal[False] = Field(
        default=False,
        alias="liveToolExecutionAllowed",
    )
    memory_write_allowed: Literal[False] = Field(
        default=False,
        alias="memoryWriteAllowed",
    )
    browser_web_channel_allowed: Literal[False] = Field(
        default=False,
        alias="browserWebChannelAllowed",
    )
    scheduler_mutation_allowed: Literal[False] = Field(
        default=False,
        alias="schedulerMutationAllowed",
    )
    connector_credential_use_allowed: Literal[False] = Field(
        default=False,
        alias="connectorCredentialUseAllowed",
    )
    network_egress_allowed: Literal[False] = Field(
        default=False,
        alias="networkEgressAllowed",
    )

    @field_validator("environment_allowlist", mode="before")
    @classmethod
    def _coerce_environment_allowlist(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            return tuple(item.strip() for item in value.split(",") if item.strip())
        if isinstance(value, tuple | list):
            return tuple(str(item).strip() for item in value if str(item).strip())
        return ()


class PythonGate3ReadinessConfig(_FalseOnlyModel):
    enabled: bool = False
    kill_switch_enabled: bool = Field(default=True, alias="killSwitchEnabled")
    local_replay_harness_enabled: bool = Field(
        default=False,
        alias="localReplayHarnessEnabled",
    )
    selected_bot_digest: str = Field(default="", alias="selectedBotDigest")
    selected_owner_user_id_digest: str = Field(
        default="",
        alias="selectedOwnerUserIdDigest",
    )
    environment: str = "local"
    environment_allowlist: tuple[str, ...] = Field(
        default=(),
        alias="environmentAllowlist",
    )
    max_replay_bundles: int = Field(default=0, ge=0, le=64, alias="maxReplayBundles")
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")
    live_capture_allowed: Literal[False] = Field(
        default=False,
        alias="liveCaptureAllowed",
    )
    model_call_allowed: Literal[False] = Field(default=False, alias="modelCallAllowed")
    user_visible_output_allowed: Literal[False] = Field(
        default=False,
        alias="userVisibleOutputAllowed",
    )
    tool_host_dispatch_allowed: Literal[False] = Field(
        default=False,
        alias="toolHostDispatchAllowed",
    )
    workspace_mutation_allowed: Literal[False] = Field(
        default=False,
        alias="workspaceMutationAllowed",
    )
    memory_write_allowed: Literal[False] = Field(default=False, alias="memoryWriteAllowed")
    browser_web_network_allowed: Literal[False] = Field(
        default=False,
        alias="browserWebNetworkAllowed",
    )
    channel_delivery_allowed: Literal[False] = Field(
        default=False,
        alias="channelDeliveryAllowed",
    )
    scheduler_mutation_allowed: Literal[False] = Field(
        default=False,
        alias="schedulerMutationAllowed",
    )
    db_write_allowed: Literal[False] = Field(default=False, alias="dbWriteAllowed")

    @field_validator("environment_allowlist", mode="before")
    @classmethod
    def _coerce_environment_allowlist(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            return tuple(item.strip() for item in value.split(",") if item.strip())
        if isinstance(value, tuple | list):
            return tuple(str(item).strip() for item in value if str(item).strip())
        return ()


class PythonGate4ReadinessConfig(_FalseOnlyModel):
    enabled: bool = False
    kill_switch_enabled: bool = Field(default=True, alias="killSwitchEnabled")
    local_shadow_harness_enabled: bool = Field(
        default=False,
        alias="localShadowHarnessEnabled",
    )
    selected_bot_digest: str = Field(default="", alias="selectedBotDigest")
    selected_owner_user_id_digest: str = Field(
        default="",
        alias="selectedOwnerUserIdDigest",
    )
    environment: str = "local"
    environment_allowlist: tuple[str, ...] = Field(
        default=(),
        alias="environmentAllowlist",
    )
    max_local_bundles: int = Field(default=0, ge=0, le=64, alias="maxLocalBundles")
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")
    adk_runner_invoked: Literal[False] = Field(default=False, alias="adkRunnerInvoked")
    live_runner_attached: Literal[False] = Field(
        default=False,
        alias="liveRunnerAttached",
    )
    model_call_allowed: Literal[False] = Field(default=False, alias="modelCallAllowed")
    user_visible_output_allowed: Literal[False] = Field(
        default=False,
        alias="userVisibleOutputAllowed",
    )
    tool_host_dispatch_allowed: Literal[False] = Field(
        default=False,
        alias="toolHostDispatchAllowed",
    )
    live_tools_executed: Literal[False] = Field(default=False, alias="liveToolsExecuted")
    workspace_mutation_allowed: Literal[False] = Field(
        default=False,
        alias="workspaceMutationAllowed",
    )
    memory_write_allowed: Literal[False] = Field(default=False, alias="memoryWriteAllowed")
    browser_web_network_allowed: Literal[False] = Field(
        default=False,
        alias="browserWebNetworkAllowed",
    )
    channel_delivery_allowed: Literal[False] = Field(
        default=False,
        alias="channelDeliveryAllowed",
    )
    scheduler_mutation_allowed: Literal[False] = Field(
        default=False,
        alias="schedulerMutationAllowed",
    )
    db_write_allowed: Literal[False] = Field(default=False, alias="dbWriteAllowed")

    @field_validator("environment_allowlist", mode="before")
    @classmethod
    def _coerce_environment_allowlist(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            return tuple(item.strip() for item in value.split(",") if item.strip())
        if isinstance(value, tuple | list):
            return tuple(str(item).strip() for item in value if str(item).strip())
        return ()

class PythonGate5ReadinessConfig(_FalseOnlyModel):
    enabled: bool = False
    kill_switch_enabled: bool = Field(default=True, alias="killSwitchEnabled")
    non_user_visible_harness_enabled: bool = Field(
        default=False,
        alias="nonUserVisibleHarnessEnabled",
    )
    selected_bot_digest: str = Field(default="", alias="selectedBotDigest")
    selected_owner_user_id_digest: str = Field(
        default="",
        alias="selectedOwnerUserIdDigest",
    )
    environment: str = "local"
    environment_allowlist: tuple[str, ...] = Field(
        default=(),
        alias="environmentAllowlist",
    )
    max_shadow_checks: int = Field(default=0, ge=0, le=64, alias="maxShadowChecks")
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")
    shadow_endpoint_enabled: Literal[False] = Field(
        default=False,
        alias="shadowEndpointEnabled",
    )
    adk_runner_invoked: Literal[False] = Field(default=False, alias="adkRunnerInvoked")
    live_runner_attached: Literal[False] = Field(
        default=False,
        alias="liveRunnerAttached",
    )
    model_call_allowed: Literal[False] = Field(default=False, alias="modelCallAllowed")
    user_visible_output_allowed: Literal[False] = Field(
        default=False,
        alias="userVisibleOutputAllowed",
    )
    provider_credential_allowed: Literal[False] = Field(
        default=False,
        alias="providerCredentialAllowed",
    )
    proxy_egress_allowed: Literal[False] = Field(
        default=False,
        alias="proxyEgressAllowed",
    )
    tool_host_dispatch_allowed: Literal[False] = Field(
        default=False,
        alias="toolHostDispatchAllowed",
    )
    live_tools_executed: Literal[False] = Field(default=False, alias="liveToolsExecuted")
    workspace_mutation_allowed: Literal[False] = Field(
        default=False,
        alias="workspaceMutationAllowed",
    )
    memory_write_allowed: Literal[False] = Field(default=False, alias="memoryWriteAllowed")
    browser_web_network_allowed: Literal[False] = Field(
        default=False,
        alias="browserWebNetworkAllowed",
    )
    channel_delivery_allowed: Literal[False] = Field(
        default=False,
        alias="channelDeliveryAllowed",
    )
    scheduler_mutation_allowed: Literal[False] = Field(
        default=False,
        alias="schedulerMutationAllowed",
    )
    db_write_allowed: Literal[False] = Field(default=False, alias="dbWriteAllowed")

    @field_validator("environment_allowlist", mode="before")
    @classmethod
    def _coerce_environment_allowlist(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            return tuple(item.strip() for item in value.split(",") if item.strip())
        if isinstance(value, tuple | list):
            return tuple(str(item).strip() for item in value if str(item).strip())
        return ()

class PythonGate7ReadinessConfig(_FalseOnlyModel):
    enabled: bool = False
    kill_switch_enabled: bool = Field(default=True, alias="killSwitchEnabled")
    local_replay_harness_enabled: bool = Field(
        default=False,
        alias="localReplayHarnessEnabled",
    )
    selected_bot_digest: str = Field(default="", alias="selectedBotDigest")
    selected_owner_user_id_digest: str = Field(
        default="",
        alias="selectedOwnerUserIdDigest",
    )
    environment: str = "local"
    environment_allowlist: tuple[str, ...] = Field(
        default=(),
        alias="environmentAllowlist",
    )
    max_local_child_tasks: int = Field(
        default=0,
        ge=0,
        le=8,
        alias="maxLocalChildTasks",
    )
    max_envelope_bytes: int = Field(
        default=0,
        ge=0,
        le=262_144,
        alias="maxEnvelopeBytes",
    )
    max_adoption_preflights: int = Field(
        default=0,
        ge=0,
        le=8,
        alias="maxAdoptionPreflights",
    )
    required_surface_refs: tuple[str, ...] = Field(
        default=(),
        alias="requiredSurfaceRefs",
    )
    optional_surface_refs: tuple[str, ...] = Field(
        default=(),
        alias="optionalSurfaceRefs",
    )
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")
    adk_runner_invoked: Literal[False] = Field(default=False, alias="adkRunnerInvoked")
    child_execution_allowed: Literal[False] = Field(
        default=False,
        alias="childExecutionAllowed",
    )
    real_child_runner_executed: Literal[False] = Field(
        default=False,
        alias="realChildRunnerExecuted",
    )
    workspace_adoption_applied: Literal[False] = Field(
        default=False,
        alias="workspaceAdoptionApplied",
    )
    workspace_mutation_allowed: Literal[False] = Field(
        default=False,
        alias="workspaceMutationAllowed",
    )
    model_call_allowed: Literal[False] = Field(default=False, alias="modelCallAllowed")
    user_visible_output_allowed: Literal[False] = Field(
        default=False,
        alias="userVisibleOutputAllowed",
    )
    provider_credential_allowed: Literal[False] = Field(
        default=False,
        alias="providerCredentialAllowed",
    )
    proxy_egress_allowed: Literal[False] = Field(
        default=False,
        alias="proxyEgressAllowed",
    )
    tool_host_dispatch_allowed: Literal[False] = Field(
        default=False,
        alias="toolHostDispatchAllowed",
    )
    live_tools_executed: Literal[False] = Field(default=False, alias="liveToolsExecuted")
    memory_write_allowed: Literal[False] = Field(default=False, alias="memoryWriteAllowed")
    browser_web_network_allowed: Literal[False] = Field(
        default=False,
        alias="browserWebNetworkAllowed",
    )
    channel_delivery_allowed: Literal[False] = Field(
        default=False,
        alias="channelDeliveryAllowed",
    )
    scheduler_mutation_allowed: Literal[False] = Field(
        default=False,
        alias="schedulerMutationAllowed",
    )
    db_write_allowed: Literal[False] = Field(default=False, alias="dbWriteAllowed")

    @field_validator("environment_allowlist", mode="before")
    @classmethod
    def _coerce_environment_allowlist(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            return tuple(item.strip() for item in value.split(",") if item.strip())
        if isinstance(value, tuple | list):
            return tuple(str(item).strip() for item in value if str(item).strip())
        return ()

class PythonGate8ReadinessConfig(_FalseOnlyModel):
    enabled: bool = False
    kill_switch_enabled: bool = Field(default=True, alias="killSwitchEnabled")
    selected_bot_digest: str = Field(default="", alias="selectedBotDigest")
    selected_owner_user_id_digest: str = Field(
        default="",
        alias="selectedOwnerUserIdDigest",
    )
    environment: str = "local"
    environment_allowlist: tuple[str, ...] = Field(
        default=(),
        alias="environmentAllowlist",
    )
    max_continuity_evidence_age_seconds: int = Field(
        default=600,
        ge=1,
        le=86_400,
        alias="maxContinuityEvidenceAgeSeconds",
    )
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")
    production_route_attached: Literal[False] = Field(
        default=False,
        alias="productionRouteAttached",
    )
    user_visible_output_allowed: Literal[False] = Field(
        default=False,
        alias="userVisibleOutputAllowed",
    )
    write_mutation_allowed: Literal[False] = Field(
        default=False,
        alias="writeMutationAllowed",
    )
    tool_dispatch_allowed: Literal[False] = Field(
        default=False,
        alias="toolDispatchAllowed",
    )
    read_only_tool_dispatch_allowed: Literal[False] = Field(
        default=False,
        alias="readOnlyToolDispatchAllowed",
    )
    transcript_write_allowed: Literal[False] = Field(
        default=False,
        alias="transcriptWriteAllowed",
    )
    sse_write_allowed: Literal[False] = Field(default=False, alias="sseWriteAllowed")
    db_write_allowed: Literal[False] = Field(default=False, alias="dbWriteAllowed")
    memory_write_allowed: Literal[False] = Field(
        default=False,
        alias="memoryWriteAllowed",
    )
    channel_delivery_allowed: Literal[False] = Field(
        default=False,
        alias="channelDeliveryAllowed",
    )
    workspace_mutation_allowed: Literal[False] = Field(
        default=False,
        alias="workspaceMutationAllowed",
    )
    mission_scheduler_allowed: Literal[False] = Field(
        default=False,
        alias="missionSchedulerAllowed",
    )
    background_task_allowed: Literal[False] = Field(
        default=False,
        alias="backgroundTaskAllowed",
    )
    self_improvement_allowed: Literal[False] = Field(
        default=False,
        alias="selfImprovementAllowed",
    )

    @field_validator("environment_allowlist", mode="before")
    @classmethod
    def _coerce_environment_allowlist(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            return tuple(item.strip() for item in value.split(",") if item.strip())
        if isinstance(value, tuple | list):
            return tuple(str(item).strip() for item in value if str(item).strip())
        return ()

class PythonRuntimeAuthorityConfig(_FalseOnlyModel):
    """Runtime authority flags.

    The ``Literal[False]`` fields below are introspected by
    ``FalseOnlyAuthorityModel`` and force-falsed on every construction surface.

    ``user_visible_output_allowed`` and ``canary_routing_allowed`` are typed
    ``bool`` (legitimately mutable through the primary ``__init__`` /
    ``model_validate`` path -- see
    ``tests/test_chat_route_contract.py``), but the ``model_construct`` and
    ``model_copy`` escape hatches MUST force-false them so a caller that
    bypasses validation cannot leak True (see
    ``tests/test_memory_mission_final_review_hardening.py``). The two methods
    below preserve that legacy ``_UNSAFE_CONSTRUCT_COPY_FIELDS`` semantics on
    top of the introspection-based base.
    """

    _UNSAFE_CONSTRUCT_COPY_FIELDS: ClassVar[tuple[str, ...]] = (
        "user_visible_output_allowed",
        "canary_routing_allowed",
    )

    user_visible_output_allowed: bool = Field(
        default=False,
        alias="userVisibleOutputAllowed",
    )
    canary_routing_allowed: bool = Field(
        default=False,
        alias="canaryRoutingAllowed",
    )
    transcript_write_allowed: Literal[False] = Field(
        default=False,
        alias="transcriptWriteAllowed",
    )
    sse_write_allowed: Literal[False] = Field(
        default=False,
        alias="sseWriteAllowed",
    )
    channel_write_allowed: Literal[False] = Field(
        default=False,
        alias="channelWriteAllowed",
    )
    db_write_allowed: Literal[False] = Field(default=False, alias="dbWriteAllowed")
    workspace_mutation_allowed: Literal[False] = Field(
        default=False,
        alias="workspaceMutationAllowed",
    )
    child_execution_allowed: Literal[False] = Field(
        default=False,
        alias="childExecutionAllowed",
    )
    mission_runtime_allowed: Literal[False] = Field(
        default=False,
        alias="missionRuntimeAllowed",
    )
    evidence_block_mode_allowed: Literal[False] = Field(
        default=False,
        alias="evidenceBlockModeAllowed",
    )

    @classmethod
    def _force_unsafe_bool_fields_false(
        cls, values: Mapping[str, Any]
    ) -> dict[str, Any]:
        payload = dict(values)
        for field_name in cls._UNSAFE_CONSTRUCT_COPY_FIELDS:
            field = cls.model_fields[field_name]
            payload.pop(field_name, None)
            payload[field.alias or field_name] = False
        return payload

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        return super().model_construct(
            _fields_set,
            **cls._force_unsafe_bool_fields_false(values),
        )

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = deep
        payload = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            payload.update(dict(update))
        return type(self).model_validate(
            type(self)._force_unsafe_bool_fields_false(payload)
        )


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    bot_id: str = Field(alias="botId")
    user_id: str = Field(alias="userId")
    gateway_token: str = Field(alias="gatewayToken")
    api_proxy_url: AnyUrl = Field(alias="apiProxyUrl")
    chat_proxy_url: AnyUrl = Field(alias="chatProxyUrl")
    redis_url: AnyUrl = Field(alias="redisUrl")
    model: str
    runtime: Literal["magi-agent"] = "magi-agent"
    runtime_engine: Literal["adk-python"] = Field(default="adk-python", alias="runtimeEngine")
    build: BuildInfo = Field(default_factory=BuildInfo)
    memory: PythonMemoryAdapterConfig = Field(default_factory=PythonMemoryAdapterConfig)
    toolhost: PythonToolHostAttachmentConfig = Field(
        default_factory=PythonToolHostAttachmentConfig,
    )
    security_posture: PythonSecurityPostureConfig = Field(
        default_factory=PythonSecurityPostureConfig,
        alias="securityPosture",
    )
    context_continuity: PythonContextContinuityConfig = Field(
        default_factory=PythonContextContinuityConfig,
        alias="contextContinuity",
    )
    gate2_readiness: PythonGate2ReadinessConfig = Field(
        default_factory=PythonGate2ReadinessConfig,
        alias="gate2Readiness",
    )
    gate3_readiness: PythonGate3ReadinessConfig = Field(
        default_factory=PythonGate3ReadinessConfig,
        alias="gate3Readiness",
    )
    gate4_readiness: PythonGate4ReadinessConfig = Field(
        default_factory=PythonGate4ReadinessConfig,
        alias="gate4Readiness",
    )
    gate5_readiness: PythonGate5ReadinessConfig = Field(
        default_factory=PythonGate5ReadinessConfig,
        alias="gate5Readiness",
    )
    gate7_readiness: PythonGate7ReadinessConfig = Field(
        default_factory=PythonGate7ReadinessConfig,
        alias="gate7Readiness",
    )
    gate8_readiness: PythonGate8ReadinessConfig = Field(
        default_factory=PythonGate8ReadinessConfig,
        alias="gate8Readiness",
    )
    authority: PythonRuntimeAuthorityConfig = Field(
        default_factory=PythonRuntimeAuthorityConfig,
    )
