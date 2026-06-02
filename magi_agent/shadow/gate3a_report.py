from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from magi_agent.shadow.fixture_runner import (
    _reject_non_json_like_comparison_metadata,
    _reject_production_like_value,
)


Gate3AParityStatus: TypeAlias = Literal[
    "pass",
    "mismatch",
    "missing",
    "extra",
    "redaction_violation",
    "runner_failure",
    "invalid_bundle",
    "audit_only",
    "not_applicable",
]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_SECRET_PREVIEW_RE = re.compile(
    r"(?:Authorization:\s*Bearer\s+\S+|Bearer\s+\S+|sk-[A-Za-z0-9_-]{8,}|api[_-]?key\s*[:=]\s*\S+|password\s*[:=]\s*\S+|secret\s*[:=]\s*\S+)",
    re.IGNORECASE,
)
_GATE3A_CREDENTIAL_TEXT_RE = re.compile(
    r"(?:"
    r"\b(?:"
    r"GITHUB_TOKEN|STRIPE_SECRET_KEY|SUPABASE_SERVICE_ROLE_KEY|OPENAI_API_KEY|"
    r"[A-Z][A-Z0-9_]*(?:_TOKEN|_SECRET|_PASSWORD|_API_KEY)"
    r")\s*=\s*(?:'[^'\r\n]*'|\"[^\"\r\n]*\"|[^\s'\"`;,]+)|"
    r"\b(?:gh[opusr]_[A-Za-z0-9_]{8,}|github_pat_[A-Za-z0-9_]+)"
    r")",
    re.IGNORECASE,
)
_UNSAFE_REPORT_TEXT_RE = re.compile(
    r"(?:"
    r"\b[a-z][a-z0-9+.-]*://\S+|"
    r"\bmagi\.pro\b\S*|"
    r"/(?:data|workspace|mnt|var|private|tmp)\S*|"
    r"\bbot-[A-Za-z0-9_-]+|"
    r"\b[a-z0-9._-]*\.kube[a-z0-9._/-]*|"
    r"\b[a-z0-9._/-]*(?:k3s|secret|secrets|mission-store|scheduler-store)[a-z0-9._/-]*"
    r")",
    re.IGNORECASE,
)
_GENERAL_ABSOLUTE_PATH_RE = re.compile(
    r"(?:^|[\s('\"`=:;,])(?:/(?!/)\S+|[a-zA-Z]:[\\/]\S*)"
)


class _Gate3AReportModel(BaseModel):
    model_config = _MODEL_CONFIG

    def model_copy(
        self,
        *,
        update: Mapping[str, object] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=False, mode="python", warnings=False)
        if update:
            alias_to_name = {
                field.alias: name
                for name, field in self.__class__.model_fields.items()
                if field.alias is not None
            }
            data.update({alias_to_name.get(key, key): value for key, value in update.items()})
        return self.__class__.model_validate(data)


class Gate3AAttachmentFlags(_Gate3AReportModel):
    live_capture_attached: Literal[False] = Field(default=False, alias="liveCaptureAttached")
    production_route_attached: Literal[False] = Field(
        default=False,
        alias="productionRouteAttached",
    )
    production_storage_attached: Literal[False] = Field(
        default=False,
        alias="productionStorageAttached",
    )
    user_visible_output_attached: Literal[False] = Field(
        default=False,
        alias="userVisibleOutputAttached",
    )
    telegram_attached: Literal[False] = Field(default=False, alias="telegramAttached")
    tool_side_effects_attached: Literal[False] = Field(
        default=False,
        alias="toolSideEffectsAttached",
    )
    evidence_block_mode_attached: Literal[False] = Field(
        default=False,
        alias="evidenceBlockModeAttached",
    )

    @model_validator(mode="before")
    @classmethod
    def _reject_non_false_input(cls, value: object) -> object:
        if isinstance(value, Gate3AAttachmentFlags):
            _reject_raw_attachment_flag_state(value)
            return value.model_dump(by_alias=True, mode="python", warnings=False)
        if isinstance(value, Mapping):
            for flag_value in value.values():
                if flag_value is not False:
                    raise ValueError("Gate 3A attachment flags must be false")
        return value

    @model_validator(mode="after")
    def _reject_constructed_true_state(self) -> Self:
        _reject_raw_attachment_flag_state(self)
        return self

    @field_serializer(
        "live_capture_attached",
        "production_route_attached",
        "production_storage_attached",
        "user_visible_output_attached",
        "telegram_attached",
        "tool_side_effects_attached",
        "evidence_block_mode_attached",
    )
    def _serialize_false_flags(self, value: object) -> bool:
        return False


class Gate3ARedactionSummary(_Gate3AReportModel):
    input_verified: bool = Field(alias="inputVerified")
    output_verified: bool = Field(alias="outputVerified")
    violations: tuple[str, ...] = ()

    @field_validator("violations")
    @classmethod
    def _reject_unredacted_violations(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        sanitized = tuple(_sanitize_report_dump_text(str(item)) for item in value)
        _reject_production_like_value(sanitized)
        return sanitized


class Gate3AParitySummary(_Gate3AReportModel):
    event_projection: Gate3AParityStatus = Field(alias="eventProjection")
    transcript_projection: Gate3AParityStatus = Field(alias="transcriptProjection")
    sse_projection: Gate3AParityStatus = Field(alias="sseProjection")
    control_projection: Gate3AParityStatus = Field(
        default="not_applicable",
        alias="controlProjection",
    )
    evidence_audit: Gate3AParityStatus = Field(default="audit_only", alias="evidenceAudit")
    tool_projection: Gate3AParityStatus = Field(
        default="not_applicable",
        alias="toolProjection",
    )


class Gate3APublicSummary(_Gate3AReportModel):
    status: Gate3AParityStatus
    preview: str

    @field_validator("preview")
    @classmethod
    def _reject_raw_preview(cls, value: str) -> str:
        sanitized = (
            sanitize_gate3a_public_summary(value)
            if _GATE3A_CREDENTIAL_TEXT_RE.search(value)
            else value
        )
        _reject_production_like_value(sanitized)
        if _SECRET_PREVIEW_RE.search(sanitized):
            raise ValueError("Gate 3A public preview must be redacted")
        return sanitized


class Gate3AComparisonReport(_Gate3AReportModel):
    schema_version: Literal["gate3a.comparisonReport.v1"] = Field(
        default="gate3a.comparisonReport.v1",
        alias="schemaVersion",
    )
    bundle_id: str = Field(alias="bundleId")
    shadow_run_id: str = Field(alias="shadowRunId")
    recipe_snapshot_id: str = Field(alias="recipeSnapshotId")
    source_runtime: Literal["typescript-core-agent"] = Field(
        default="typescript-core-agent",
        alias="sourceRuntime",
    )
    shadow_runtime: Literal["python-adk"] = Field(default="python-adk", alias="shadowRuntime")
    storage_mode: Literal["local_only"] = Field(default="local_only", alias="storageMode")
    adk_primitives: tuple[Literal["Agent", "Runner", "Event"], ...] = Field(
        default=("Agent", "Runner", "Event"),
        alias="adkPrimitives",
    )
    custom_runtime_loop: Literal[False] = Field(default=False, alias="customRuntimeLoop")
    attachment_flags: Gate3AAttachmentFlags = Field(
        default_factory=Gate3AAttachmentFlags,
        alias="attachmentFlags",
    )
    redaction: Gate3ARedactionSummary
    parity: Gate3AParitySummary
    failures: tuple[str, ...] = ()
    public_summary: Gate3APublicSummary = Field(alias="publicSummary")

    @field_validator("bundle_id", "shadow_run_id", "recipe_snapshot_id")
    @classmethod
    def _reject_unsafe_ids(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Gate 3A report IDs must be non-empty")
        _reject_production_like_value(value)
        return value

    @field_validator("failures")
    @classmethod
    def _reject_unsafe_failures(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        sanitized = tuple(_sanitize_report_dump_text(str(item)) for item in value)
        _reject_production_like_value(sanitized)
        return sanitized

    @field_validator("attachment_flags", mode="before")
    @classmethod
    def _reject_tampered_flags(cls, value: object) -> object:
        if isinstance(value, Gate3AAttachmentFlags):
            _reject_raw_attachment_flag_state(value)
        return value

    @model_validator(mode="after")
    def _revalidate_report_boundary(self) -> Self:
        _reject_raw_attachment_flag_state(self.attachment_flags)
        expected_public_status = _aggregate_public_status_from_parity(self.parity)
        if self.public_summary.status != expected_public_status:
            raise ValueError("Gate 3A public summary status must match aggregate parity")
        _reject_non_json_like_comparison_metadata(
            self.model_dump(by_alias=True, mode="json", warnings=False)
        )
        return self

    @field_serializer("attachment_flags")
    def _serialize_attachment_flags(
        self,
        value: Gate3AAttachmentFlags,
    ) -> dict[str, bool]:
        return Gate3AAttachmentFlags().model_dump(
            by_alias=True,
            mode="json",
            warnings=False,
        )

    @field_serializer("storage_mode")
    def _serialize_local_only_storage_mode(self, value: object) -> str:
        return "local_only"

    @field_serializer("adk_primitives")
    def _serialize_adk_primitives(self, value: object) -> tuple[str, str, str]:
        return ("Agent", "Runner", "Event")

    @field_serializer("custom_runtime_loop")
    def _serialize_no_custom_runtime_loop(self, value: object) -> bool:
        return False

    @field_serializer("parity")
    def _serialize_safe_parity(self, value: object) -> dict[str, str]:
        return _safe_parity_dump(value)

    @field_serializer("bundle_id", "shadow_run_id", "recipe_snapshot_id")
    def _serialize_safe_report_id(self, value: object) -> str:
        return _sanitize_report_dump_text(str(value))

    @field_serializer("redaction")
    def _serialize_safe_redaction(self, value: object) -> dict[str, object]:
        return _safe_redaction_dump(value)

    @field_serializer("failures")
    def _serialize_safe_failures(self, value: object) -> tuple[str, ...]:
        if isinstance(value, (list, tuple)):
            return tuple(_sanitize_report_dump_text(str(item)) for item in value)
        return ()

    @field_serializer("public_summary")
    def _serialize_safe_public_summary(self, value: object) -> dict[str, str]:
        safe_summary = _safe_public_summary_dump(value)
        safe_summary["status"] = _aggregate_public_status_from_parity(self.parity)
        return safe_summary


def _reject_raw_attachment_flag_state(flags: Gate3AAttachmentFlags) -> None:
    raw_state = getattr(flags, "__dict__", {})
    if not isinstance(raw_state, Mapping):
        raise ValueError("Gate 3A attachment flags raw state must be a mapping")
    for raw_value in raw_state.values():
        if raw_value is not False:
            raise ValueError("Gate 3A attachment flags must be false")
    raw_extra = getattr(flags, "__pydantic_extra__", None)
    if raw_extra:
        raise ValueError("Gate 3A attachment flags must not contain raw extra state")


def sanitize_gate3a_public_summary(value: str, *, max_chars: int = 180) -> str:
    sanitized = _GATE3A_CREDENTIAL_TEXT_RE.sub("[REDACTED]", value)
    sanitized = _SECRET_PREVIEW_RE.sub("[REDACTED]", sanitized)
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    if len(sanitized) > max_chars:
        sanitized = sanitized[: max(0, max_chars - 3)].rstrip() + "..."
    return sanitized


def sanitize_gate3a_report_failure(value: str, *, max_chars: int = 180) -> str:
    return _sanitize_report_dump_text(value, max_chars=max_chars)


def _sanitize_report_dump_text(value: str, *, max_chars: int = 180) -> str:
    sanitized = _GATE3A_CREDENTIAL_TEXT_RE.sub("[REDACTED]", value)
    sanitized = _SECRET_PREVIEW_RE.sub("[REDACTED]", sanitized)
    sanitized = _GENERAL_ABSOLUTE_PATH_RE.sub("[REDACTED]", sanitized)
    sanitized = _UNSAFE_REPORT_TEXT_RE.sub("[REDACTED]", sanitized)
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    if not sanitized:
        sanitized = "[REDACTED]"
    if len(sanitized) > max_chars:
        sanitized = sanitized[: max(0, max_chars - 3)].rstrip() + "..."
    return sanitized


def _safe_status(value: object) -> Gate3AParityStatus:
    allowed = set(Gate3AParityStatus.__args__)
    if isinstance(value, str) and value in allowed:
        return value  # type: ignore[return-value]
    return "redaction_violation"


def _mapping_value(value: object, *keys: str) -> object:
    if isinstance(value, Mapping):
        for key in keys:
            if key in value:
                return value[key]
    for key in keys:
        attr_value = getattr(value, key, None)
        if attr_value is not None:
            return attr_value
    return None


def _safe_public_summary_dump(value: object) -> dict[str, str]:
    status = _safe_status(_mapping_value(value, "status"))
    preview_value = _mapping_value(value, "preview")
    preview = _sanitize_report_dump_text(str(preview_value or "Redacted local replay completed."))
    return {"status": status, "preview": preview}


def _safe_redaction_dump(value: object) -> dict[str, object]:
    violations_value = _mapping_value(value, "violations") or ()
    if isinstance(violations_value, (list, tuple)):
        violations = tuple(
            _sanitize_report_dump_text(str(violation)) for violation in violations_value
        )
    else:
        violations = ()
    return {
        "inputVerified": _mapping_value(value, "input_verified", "inputVerified") is True,
        "outputVerified": _mapping_value(value, "output_verified", "outputVerified") is True,
        "violations": violations,
    }


def _safe_parity_dump(value: object) -> dict[str, str]:
    event_projection = _safe_status(
        _mapping_value(value, "event_projection", "eventProjection")
    )
    transcript_projection = _safe_status(
        _mapping_value(value, "transcript_projection", "transcriptProjection")
    )
    sse_projection = _safe_status(_mapping_value(value, "sse_projection", "sseProjection"))
    control_projection = _safe_status(
        _mapping_value(value, "control_projection", "controlProjection")
        or "not_applicable"
    )
    evidence_audit = _safe_status(
        _mapping_value(value, "evidence_audit", "evidenceAudit") or "audit_only"
    )
    if evidence_audit == "not_applicable":
        evidence_audit = "audit_only"
    tool_projection = _safe_status(
        _mapping_value(value, "tool_projection", "toolProjection") or "not_applicable"
    )
    return {
        "eventProjection": event_projection,
        "transcriptProjection": transcript_projection,
        "sseProjection": sse_projection,
        "controlProjection": control_projection,
        "evidenceAudit": evidence_audit,
        "toolProjection": tool_projection,
    }


def _aggregate_public_status_from_parity(value: object) -> Gate3AParityStatus:
    return _aggregate_public_status(
        event_projection=_safe_status(
            _mapping_value(value, "event_projection", "eventProjection")
        ),
        transcript_projection=_safe_status(
            _mapping_value(value, "transcript_projection", "transcriptProjection")
        ),
        sse_projection=_safe_status(_mapping_value(value, "sse_projection", "sseProjection")),
        control_projection=_safe_status(
            _mapping_value(value, "control_projection", "controlProjection")
            or "not_applicable"
        ),
        evidence_audit=_safe_status(
            _mapping_value(value, "evidence_audit", "evidenceAudit") or "audit_only"
        ),
        tool_projection=_safe_status(
            _mapping_value(value, "tool_projection", "toolProjection") or "not_applicable"
        ),
    )


def build_gate3a_comparison_report(
    *,
    bundle_id: str,
    shadow_run_id: str,
    recipe_snapshot_id: str,
    event_projection: Gate3AParityStatus,
    transcript_projection: Gate3AParityStatus,
    sse_projection: Gate3AParityStatus,
    control_projection: Gate3AParityStatus = "not_applicable",
    evidence_audit: Gate3AParityStatus = "audit_only",
    tool_projection: Gate3AParityStatus = "not_applicable",
    failures: tuple[str, ...] = (),
    public_preview: str = "Redacted local replay completed.",
) -> Gate3AComparisonReport:
    preview = sanitize_gate3a_public_summary(public_preview)
    public_status = _aggregate_public_status(
        event_projection=event_projection,
        transcript_projection=transcript_projection,
        sse_projection=sse_projection,
        control_projection=control_projection,
        evidence_audit=evidence_audit,
        tool_projection=tool_projection,
    )
    return Gate3AComparisonReport.model_validate(
        {
            "bundleId": bundle_id,
            "shadowRunId": shadow_run_id,
            "recipeSnapshotId": recipe_snapshot_id,
            "attachmentFlags": Gate3AAttachmentFlags(),
            "redaction": {
                "inputVerified": True,
                "outputVerified": True,
                "violations": (),
            },
            "parity": {
                "eventProjection": event_projection,
                "transcriptProjection": transcript_projection,
                "sseProjection": sse_projection,
                "controlProjection": control_projection,
                "evidenceAudit": evidence_audit,
                "toolProjection": tool_projection,
            },
            "failures": failures,
            "publicSummary": {"status": public_status, "preview": preview},
        }
    )


def _aggregate_public_status(
    *,
    event_projection: Gate3AParityStatus,
    transcript_projection: Gate3AParityStatus,
    sse_projection: Gate3AParityStatus,
    control_projection: Gate3AParityStatus,
    evidence_audit: Gate3AParityStatus,
    tool_projection: Gate3AParityStatus,
) -> Gate3AParityStatus:
    for status in (
        event_projection,
        transcript_projection,
        sse_projection,
        control_projection,
        evidence_audit,
        tool_projection,
    ):
        if status not in {"pass", "audit_only", "not_applicable"}:
            return status
    return "pass"


__all__ = [
    "Gate3AAttachmentFlags",
    "Gate3AComparisonReport",
    "Gate3AParityStatus",
    "Gate3AParitySummary",
    "Gate3APublicSummary",
    "Gate3ARedactionSummary",
    "build_gate3a_comparison_report",
    "sanitize_gate3a_report_failure",
    "sanitize_gate3a_public_summary",
]
