from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from magi_agent.config.models import (
    PythonContextContinuityConfig,
    PythonGate8ReadinessConfig,
)
from magi_agent.evidence.gate1a_egress_correlation import (
    GATE1A_EGRESS_CORRELATION_MODE,
    GATE1A_EGRESS_TELEMETRY_SOURCE,
)


_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_SAFE_ENVIRONMENTS = frozenset({"local", "development", "staging", "production"})


class Gate8PreGate8ContinuityReceipt(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        populate_by_name=True,
        extra="forbid",
        hide_input_in_errors=True,
    )

    schema_version: Literal["gate8.preGate8ContinuityReceipt.v1"] = Field(
        default="gate8.preGate8ContinuityReceipt.v1",
        alias="schemaVersion",
    )
    status: Literal["pass"]
    evidence_source: Literal["local_verified_evidence"] = Field(
        alias="evidenceSource",
    )
    evidence_digest: str = Field(alias="evidenceDigest")
    receipt_digest: str = Field(alias="receiptDigest")
    observed_at_epoch_seconds: int = Field(ge=0, alias="observedAtEpochSeconds")
    max_age_seconds: int = Field(ge=1, le=86_400, alias="maxAgeSeconds")
    imported_event_count: int = Field(ge=1, alias="importedEventCount")
    rejected_entry_count: int = Field(ge=0, alias="rejectedEntryCount")
    compaction_applied: bool = Field(alias="compactionApplied")
    projection_digest_present: Literal[True] = Field(
        default=True,
        alias="projectionDigestPresent",
    )
    model_visible_digest_present: Literal[True] = Field(
        default=True,
        alias="modelVisibleDigestPresent",
    )
    source_transcript_head_digest_present: Literal[True] = Field(
        default=True,
        alias="sourceTranscriptHeadDigestPresent",
    )
    fallback_status: Literal["none"] = Field(default="none", alias="fallbackStatus")
    private_payload_rejected: bool = Field(alias="privatePayloadRejected")
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")

    @model_validator(mode="after")
    def _validate_digest_fields(self) -> Self:
        if _DIGEST_RE.fullmatch(self.evidence_digest) is None:
            raise ValueError("evidenceDigest must be a sha256 digest")
        if _DIGEST_RE.fullmatch(self.receipt_digest) is None:
            raise ValueError("receiptDigest must be a sha256 digest")
        return self

    @classmethod
    def from_evidence(
        cls,
        evidence: object,
        *,
        observed_at_epoch_seconds: int,
        now_epoch_seconds: int,
        max_age_seconds: int = 600,
    ) -> Self:
        if getattr(evidence, "forbidden_payload_observed", False) is True:
            raise ValueError("private Pre-Gate8 continuity evidence is not admissible")
        context = PythonContextContinuityConfig.from_canary_evidence(evidence)
        if hasattr(evidence, "model_dump"):
            evidence_payload = evidence.model_dump(
                by_alias=True,
                mode="json",
                warnings=False,
            )
        else:
            evidence_payload = {
                key: getattr(evidence, key)
                for key in dir(evidence)
                if not key.startswith("_") and not callable(getattr(evidence, key))
            }
        return cls.from_context_config(
            context,
            observed_at_epoch_seconds=observed_at_epoch_seconds,
            now_epoch_seconds=now_epoch_seconds,
            max_age_seconds=max_age_seconds,
            evidence_digest=_sha256_json(evidence_payload),
        )

    @classmethod
    def from_context_config(
        cls,
        context: PythonContextContinuityConfig,
        *,
        observed_at_epoch_seconds: int,
        now_epoch_seconds: int,
        max_age_seconds: int = 600,
        evidence_digest: str | None = None,
    ) -> Self:
        if now_epoch_seconds - observed_at_epoch_seconds > max_age_seconds:
            raise ValueError("stale Pre-Gate8 continuity evidence")
        if not context.continuity_canary_ready:
            raise ValueError("verified Pre-Gate8 continuity PASS evidence is required")
        safe_payload: dict[str, object] = {
            "schemaVersion": "gate8.preGate8ContinuityReceipt.v1",
            "status": "pass",
            "evidenceSource": context.canary_evidence_source,
            "evidenceDigest": evidence_digest
            or _sha256_json(context.health_metadata),
            "observedAtEpochSeconds": observed_at_epoch_seconds,
            "maxAgeSeconds": max_age_seconds,
            "importedEventCount": context.imported_event_count,
            "rejectedEntryCount": context.rejected_entry_count,
            "compactionApplied": context.compaction_applied,
            "projectionDigestPresent": context.projection_digest_present,
            "modelVisibleDigestPresent": context.model_visible_digest_present,
            "sourceTranscriptHeadDigestPresent": (
                context.source_transcript_head_digest_present
            ),
            "fallbackStatus": context.fallback_status,
            "privatePayloadRejected": (
                context.rejected_entry_count == 0
                or "private_payload_rejected" in context.reason_codes
            ),
            "reasonCodes": context.reason_codes,
        }
        safe_payload["receiptDigest"] = _sha256_json(safe_payload)
        return cls.model_validate(safe_payload)


def gate8_readiness_health_metadata(
    config: PythonGate8ReadinessConfig,
    context_continuity: PythonContextContinuityConfig,
    *,
    bot_id: str,
    user_id: str,
    observed_egress: Mapping[str, object] | None = None,
) -> dict[str, object]:
    egress_correlation_ready = _egress_correlation_ready(observed_egress)
    selected_scope_matched = _selected_scope_matched(
        config,
        bot_id=bot_id,
        user_id=user_id,
    )
    reason_codes = _reason_codes_for_scope(
        config,
        context_continuity,
        bot_id=bot_id,
        user_id=user_id,
        egress_correlation_ready=egress_correlation_ready,
    )
    readiness_ready = reason_codes == ("gate8_selected_authority_ready",)
    if not config.enabled:
        status = "disabled"
    elif readiness_ready:
        status = "ready"
    else:
        status = "blocked"
    return {
        "enabled": config.enabled,
        "status": status,
        "readinessReady": readiness_ready,
        "selectedScopeMatched": selected_scope_matched,
        "blockedByPreGate8Continuity": (
            not context_continuity.continuity_canary_ready
        ),
        "blockedByEgressTelemetryCorrelation": (
            config.enabled and not egress_correlation_ready
        ),
        "egressTelemetryCorrelationReady": egress_correlation_ready,
        "egressTelemetryCorrelationMode": (
            GATE1A_EGRESS_CORRELATION_MODE if egress_correlation_ready else "none"
        ),
        "egressTelemetryEvidenceSource": (
            GATE1A_EGRESS_TELEMETRY_SOURCE if egress_correlation_ready else "none"
        ),
        "reasonCode": (
            "gate8_selected_authority_ready"
            if readiness_ready
            else _primary_block_reason(reason_codes, context_continuity)
        ),
        "reasonCodes": list(reason_codes),
        "responseAuthorityEligible": readiness_ready,
        "routeAttached": False,
        "productionRouteAttached": False,
        "preGate8ContinuityEvidence": _continuity_evidence_metadata(
            context_continuity
        ),
        "userVisibleOutputAllowed": False,
        "writeMutationAllowed": False,
        "toolDispatchAllowed": False,
        "readOnlyToolDispatchAllowed": False,
        "transcriptWriteAllowed": False,
        "sseWriteAllowed": False,
        "dbWriteAllowed": False,
        "memoryWriteAllowed": False,
        "channelDeliveryAllowed": False,
        "workspaceMutationAllowed": False,
        "missionSchedulerAllowed": False,
        "backgroundTaskAllowed": False,
        "selfImprovementAllowed": False,
    }


def _reason_codes_for_scope(
    config: PythonGate8ReadinessConfig,
    context_continuity: PythonContextContinuityConfig,
    *,
    bot_id: str,
    user_id: str,
    egress_correlation_ready: bool,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if not config.enabled:
        reasons.append("gate_disabled")
    if config.enabled and config.kill_switch_enabled:
        reasons.append("kill_switch_enabled")
    if config.enabled and not _digest_present(config.selected_bot_digest):
        reasons.append("malformed_selected_scope")
    if config.enabled and not _digest_present(config.selected_owner_user_id_digest):
        reasons.append("malformed_selected_scope")
    if config.enabled and _digest_present(config.selected_bot_digest):
        if config.selected_bot_digest != _sha256_text_digest(bot_id):
            reasons.append("bot_not_selected")
    if config.enabled and _digest_present(config.selected_owner_user_id_digest):
        if config.selected_owner_user_id_digest != _sha256_text_digest(user_id):
            reasons.append("owner_not_selected")
    if config.enabled and config.environment not in _SAFE_ENVIRONMENTS:
        reasons.append("invalid_environment")
    if config.enabled and config.environment not in config.environment_allowlist:
        reasons.append("environment_not_allowlisted")
    if not context_continuity.continuity_canary_ready:
        reasons.append(context_continuity.gate8_block_reason)
    if config.enabled and not egress_correlation_ready:
        reasons.append("gate8_egress_correlation_not_ready")
    if not reasons:
        return ("gate8_selected_authority_ready",)
    return tuple(dict.fromkeys(reasons))


def _primary_block_reason(
    reason_codes: tuple[str, ...],
    context_continuity: PythonContextContinuityConfig,
) -> str:
    if not context_continuity.continuity_canary_ready:
        return context_continuity.gate8_block_reason
    if "gate8_egress_correlation_not_ready" in reason_codes:
        return "gate8_egress_correlation_not_ready"
    return context_continuity.gate8_block_reason


def _selected_scope_matched(
    config: PythonGate8ReadinessConfig,
    *,
    bot_id: str,
    user_id: str,
) -> bool:
    if not config.enabled:
        return False
    if not _digest_present(config.selected_bot_digest):
        return False
    if not _digest_present(config.selected_owner_user_id_digest):
        return False
    if config.selected_bot_digest != _sha256_text_digest(bot_id):
        return False
    if config.selected_owner_user_id_digest != _sha256_text_digest(user_id):
        return False
    if config.environment not in _SAFE_ENVIRONMENTS:
        return False
    return config.environment in config.environment_allowlist


def _continuity_evidence_metadata(
    context: PythonContextContinuityConfig,
) -> dict[str, object]:
    return {
        "status": context.canary_status,
        "evidenceSource": context.canary_evidence_source,
        "verified": context.canary_evidence_verified,
        "importedEventCount": context.imported_event_count,
        "rejectedEntryCount": context.rejected_entry_count,
        "compactionApplied": context.compaction_applied,
        "projectionDigestPresent": context.projection_digest_present,
        "modelVisibleDigestPresent": context.model_visible_digest_present,
        "sourceTranscriptHeadDigestPresent": (
            context.source_transcript_head_digest_present
        ),
        "fallbackStatus": context.fallback_status,
        "reasonCodes": list(context.reason_codes),
    }


def _sha256_text_digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_json(value: Mapping[str, object] | object) -> str:
    serialized = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return _sha256_text_digest(serialized)


def _digest_present(value: object) -> bool:
    return isinstance(value, str) and _DIGEST_RE.fullmatch(value) is not None


def _egress_correlation_ready(observed_egress: Mapping[str, object] | None) -> bool:
    if not isinstance(observed_egress, Mapping):
        return False
    return (
        observed_egress.get("gate1aEgressEvidenceReady") is True
        and observed_egress.get("egressEvidenceSource") == GATE1A_EGRESS_TELEMETRY_SOURCE
        and observed_egress.get("egressEvidenceReadinessReason")
        == "live_correlation_source_ready"
    )


__all__ = [
    "Gate8PreGate8ContinuityReceipt",
    "gate8_readiness_health_metadata",
]
