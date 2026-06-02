from __future__ import annotations

from collections.abc import Mapping
import hashlib
import re
from typing import Any, Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from magi_agent.shadow.gate5b4c3_shadow_generation_contract import (
    Gate5B4C3ModelRoutingSource,
    Gate5B4C3ShadowGenerationAuthorityFlags,
    Gate5B4C3ShadowGenerationDiagnostic,
)
from magi_agent.shadow.gate5b4c3_shadow_counter_store import (
    Gate5B4C3ShadowCounterState,
)


Gate5B4C3ShadowGenerationRunnerReportStatus: TypeAlias = Literal[
    "skipped",
    "dropped",
    "completed",
    "error",
]
Gate5B4C3ShadowGenerationRunnerReportReason: TypeAlias = Literal[
    "not_accepted",
    "input_adapter_drop",
    "runner_completed",
    "runner_timeout",
    "runner_error",
    "model_provider_error",
    "counter_store_unavailable",
    "counter_store_error",
    "counter_blocked",
    "idempotency_replay",
]
Gate5B4C3ShadowGenerationOutputRejectionReason: TypeAlias = Literal[
    "none",
    "unsafe_output",
    "output_too_large",
]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_MAX_OUTPUT_BYTES = 16_384
_DEFAULT_PREVIEW_BYTES = 512
_SOFT_PREVIEW_REDACTION_RE = re.compile(
    r"(?:"
    r"(?P<email>\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b)|"
    r"(?P<token>\b[A-F0-9]{32,}\b)"
    r")",
    re.IGNORECASE,
)
_UNSAFE_OUTPUT_RE = re.compile(
    r"(?:"
    r"Authorization:\s*Bearer\s+\S+|"
    r"(?:Cookie|Set-Cookie):\s*[^;\r\n]+(?:;[^\r\n]*)?|"
    r"Bearer\s+\S+|"
    r"sk-[A-Za-z0-9_-]{8,}|"
    r"AIza[A-Za-z0-9_-]{20,}|"
    r"xox[a-z]-[A-Za-z0-9-]{8,}|"
    r"\b\d{5,}:[A-Za-z0-9_-]{8,}|"
    r"\b(?:gh[opusr]_[A-Za-z0-9_]{8,}|github_pat_[A-Za-z0-9_]+)|"
    r"[\"']?(?:access[_-]?token|refresh[_-]?token|api[_-]?key|"
    r"client[_-]?secret|private[_-]?key|session[_-]?key)[\"']?\s*:"
    r"\s*[\"'][^\"'\r\n]{4,}[\"']|"
    r"\b(?:[A-Z][A-Z0-9_]*(?:_TOKEN|_SECRET|_SECRET_KEY|_PASSWORD|"
    r"_API_KEY|_SERVICE_ROLE_KEY))"
    r"\s*=\s*(?:'[^'\r\n]*'|\"[^\"\r\n]*\"|[^\s'\"`;,]+)|"
    r"\b(?:api[_-]?key|token|secret|password|service[_-]?role[_-]?key)"
    r"\s*[:=]\s*\S+|"
    r"hidden_reasoning|chain_of_thought|private_reasoning|reasoning_trace|"
    r"private_tool_preview|private_tool_input|private_tool_output|raw_tool_preview|"
    r"/(?:data/bots|workspace|var/lib/kubelet|mnt|private|Users)\S*|"
    r"\b(?:kubectl|helm|kustomize|sealed-secrets|kubeconfig)\b|"
    r"\bmagi\.pro\b\S*|"
    r"https?://\S+|"
    r"s3://\S+"
    r")",
    re.IGNORECASE,
)


class _Gate5B4C3RunnerReportModel(BaseModel):
    model_config = _MODEL_CONFIG

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        data = {
            key: value.model_dump(by_alias=True, mode="python", warnings=False)
            if isinstance(value, BaseModel)
            else value
            for key, value in values.items()
        }
        return cls(**data)

    def model_copy(
        self,
        *,
        update: Mapping[str, object] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            name_to_alias = {
                name: field.alias or name
                for name, field in self.__class__.model_fields.items()
            }
            data.update({name_to_alias.get(key, key): value for key, value in update.items()})
        return self.__class__.model_validate(data)


class Gate5B4C3ShadowGenerationRunnerReport(_Gate5B4C3RunnerReportModel):
    schema_version: Literal["gate5b4c3.runnerReport.v1"] = Field(
        default="gate5b4c3.runnerReport.v1",
        alias="schemaVersion",
    )
    diagnostic: Gate5B4C3ShadowGenerationDiagnostic
    status: Gate5B4C3ShadowGenerationRunnerReportStatus
    reason: Gate5B4C3ShadowGenerationRunnerReportReason
    response_authority: Literal["typescript"] = Field(
        default="typescript",
        alias="responseAuthority",
    )
    diagnostic_only: Literal[True] = Field(default=True, alias="diagnosticOnly")
    local_only: Literal[True] = Field(default=True, alias="localOnly")
    fail_open: Literal[True] = Field(default=True, alias="failOpen")
    adk_runner_invoked: bool = Field(default=False, alias="adkRunnerInvoked")
    model_call_attempted: bool = Field(default=False, alias="modelCallAttempted")
    event_count: int = Field(default=0, ge=0, alias="eventCount")
    latency_ms: int = Field(default=0, ge=0, alias="latencyMs")
    runner_timeout_ms: int = Field(default=0, ge=0, alias="runnerTimeoutMs")
    max_output_tokens: int = Field(default=0, ge=0, alias="maxOutputTokens")
    max_estimated_input_tokens: int = Field(
        default=0,
        ge=0,
        alias="maxEstimatedInputTokens",
    )
    max_total_estimated_tokens: int = Field(
        default=0,
        ge=0,
        alias="maxTotalEstimatedTokens",
    )
    routing_source: Gate5B4C3ModelRoutingSource | None = Field(
        default=None,
        alias="routingSource",
    )
    router_decision_digest: str | None = Field(default=None, alias="routerDecisionDigest")
    routing_profile_digest: str | None = Field(default=None, alias="routingProfileDigest")
    bot_config_model_digest: str | None = Field(default=None, alias="botConfigModelDigest")
    fallback_approved: bool = Field(default=False, alias="fallbackApproved")
    shadow_credential_ref: str | None = Field(default=None, alias="shadowCredentialRef")
    retry_policy: Literal["none"] = Field(default="none", alias="retryPolicy")
    cost_cap_usd: float = Field(default=0, ge=0, alias="costCapUsd")
    counter_status: str | None = Field(default=None, alias="counterStatus")
    counter_reason: str | None = Field(default=None, alias="counterReason")
    counter_state: Gate5B4C3ShadowCounterState | None = Field(
        default=None,
        alias="counterState",
    )
    idempotency_key_digest: str | None = Field(default=None, alias="idempotencyKeyDigest")
    comparison_status: str | None = Field(default=None, alias="comparisonStatus")
    comparison_artifact_digest: str | None = Field(
        default=None,
        alias="comparisonArtifactDigest",
    )
    output_accepted: bool = Field(default=False, alias="outputAccepted")
    output_rejection_reason: Gate5B4C3ShadowGenerationOutputRejectionReason = Field(
        default="none",
        alias="outputRejectionReason",
    )
    output_digest: str | None = Field(default=None, alias="outputDigest")
    output_preview_internal: str | None = Field(default=None, alias="outputPreviewInternal")
    output_truncated: bool = Field(default=False, alias="outputTruncated")
    output_redaction_applied: bool = Field(default=False, alias="outputRedactionApplied")
    error_class: str | None = Field(default=None, alias="errorClass")
    error_preview: str | None = Field(default=None, alias="errorPreview")
    user_visible_output: None = Field(default=None, alias="userVisibleOutput")
    production_write_targets: tuple[()] = Field(default=(), alias="productionWriteTargets")
    authority: Gate5B4C3ShadowGenerationAuthorityFlags = Field(
        default_factory=Gate5B4C3ShadowGenerationAuthorityFlags,
    )

    @model_validator(mode="before")
    @classmethod
    def _force_non_authoritative(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        data["responseAuthority"] = "typescript"
        data["diagnosticOnly"] = True
        data["localOnly"] = True
        data["failOpen"] = True
        data["userVisibleOutput"] = None
        data["productionWriteTargets"] = ()
        return data

    @field_serializer("authority")
    def _serialize_authority(self, _value: object) -> dict[str, bool]:
        return Gate5B4C3ShadowGenerationAuthorityFlags().model_dump(
            by_alias=True,
            mode="json",
        )


def build_gate5b4c3_shadow_generation_report(
    *,
    diagnostic: Gate5B4C3ShadowGenerationDiagnostic,
    status: Gate5B4C3ShadowGenerationRunnerReportStatus,
    reason: Gate5B4C3ShadowGenerationRunnerReportReason,
    adk_runner_invoked: bool = False,
    model_call_attempted: bool = False,
    event_count: int = 0,
    output_text: str | None = None,
    latency_ms: int = 0,
    runner_timeout_ms: int = 0,
    max_output_tokens: int = 0,
    max_estimated_input_tokens: int = 0,
    max_total_estimated_tokens: int = 0,
    routing_source: Gate5B4C3ModelRoutingSource | None = None,
    router_decision_digest: str | None = None,
    routing_profile_digest: str | None = None,
    bot_config_model_digest: str | None = None,
    fallback_approved: bool = False,
    shadow_credential_ref: str | None = None,
    retry_policy: Literal["none"] = "none",
    cost_cap_usd: float = 0,
    counter_status: str | None = None,
    counter_reason: str | None = None,
    counter_state: Gate5B4C3ShadowCounterState | None = None,
    idempotency_key_digest: str | None = None,
    comparison_status: str | None = None,
    comparison_artifact_digest: str | None = None,
    error_class: str | None = None,
    error_preview: str | None = None,
    preview_byte_limit: int = _DEFAULT_PREVIEW_BYTES,
) -> Gate5B4C3ShadowGenerationRunnerReport:
    output_digest: str | None = None
    output_preview_internal: str | None = None
    output_accepted = False
    output_truncated = False
    output_redaction_applied = False
    output_rejection_reason: Gate5B4C3ShadowGenerationOutputRejectionReason = "none"
    if output_text is not None:
        output_digest = _sha256_digest(output_text)
        output_bytes = len(output_text.encode("utf-8"))
        if output_bytes > _MAX_OUTPUT_BYTES:
            output_rejection_reason = "output_too_large"
        elif _UNSAFE_OUTPUT_RE.search(output_text):
            output_rejection_reason = "unsafe_output"
            output_redaction_applied = True
        else:
            output_accepted = True
            output_preview_internal = None
            output_truncated = output_bytes > 0
            output_redaction_applied = False
    redacted_error_preview = None
    if error_preview is not None:
        redacted_error_preview = _redact_and_cap(error_preview, preview_byte_limit)

    return Gate5B4C3ShadowGenerationRunnerReport(
        diagnostic=diagnostic.model_dump(by_alias=True, mode="python", warnings=False),
        status=status,
        reason=reason,
        adkRunnerInvoked=adk_runner_invoked,
        modelCallAttempted=model_call_attempted,
        eventCount=event_count,
        latencyMs=max(latency_ms, 0),
        runnerTimeoutMs=max(runner_timeout_ms, 0),
        maxOutputTokens=max(max_output_tokens, 0),
        maxEstimatedInputTokens=max(max_estimated_input_tokens, 0),
        maxTotalEstimatedTokens=max(max_total_estimated_tokens, 0),
        routingSource=routing_source,
        routerDecisionDigest=router_decision_digest,
        routingProfileDigest=routing_profile_digest,
        botConfigModelDigest=bot_config_model_digest,
        fallbackApproved=fallback_approved,
        shadowCredentialRef=shadow_credential_ref,
        retryPolicy=retry_policy,
        costCapUsd=max(cost_cap_usd, 0),
        counterStatus=counter_status,
        counterReason=counter_reason,
        counterState=(
            counter_state.model_dump(by_alias=True, mode="python", warnings=False)
            if counter_state is not None
            else None
        ),
        idempotencyKeyDigest=idempotency_key_digest,
        comparisonStatus=comparison_status,
        comparisonArtifactDigest=comparison_artifact_digest,
        outputAccepted=output_accepted,
        outputRejectionReason=output_rejection_reason,
        outputDigest=output_digest,
        outputPreviewInternal=output_preview_internal,
        outputTruncated=output_truncated,
        outputRedactionApplied=output_redaction_applied,
        errorClass=error_class,
        errorPreview=redacted_error_preview,
    )


def _sha256_digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _cap_utf8(value: str, byte_limit: int) -> tuple[str, bool]:
    if byte_limit <= 0:
        return "", bool(value)
    encoded = value.encode("utf-8")
    if len(encoded) <= byte_limit:
        return value, False
    return encoded[:byte_limit].decode("utf-8", errors="ignore"), True


def _redact_and_cap(value: str, byte_limit: int) -> str:
    redacted = _UNSAFE_OUTPUT_RE.sub("[REDACTED]", value)
    redacted = _SOFT_PREVIEW_REDACTION_RE.sub(_soft_replacement, redacted)
    return _cap_utf8(redacted, max(0, byte_limit))[0]


def _redact_preview(value: str) -> tuple[str, bool]:
    redacted = _UNSAFE_OUTPUT_RE.sub("[REDACTED]", value)
    redacted = _SOFT_PREVIEW_REDACTION_RE.sub(_soft_replacement, redacted)
    return redacted, redacted != value


def _soft_replacement(match: re.Match[str]) -> str:
    if match.group("email") is not None:
        return "[REDACTED_EMAIL]"
    return "[REDACTED_TOKEN]"


__all__ = [
    "Gate5B4C3ShadowGenerationOutputRejectionReason",
    "Gate5B4C3ShadowGenerationRunnerReport",
    "Gate5B4C3ShadowGenerationRunnerReportReason",
    "Gate5B4C3ShadowGenerationRunnerReportStatus",
    "build_gate5b4c3_shadow_generation_report",
]
