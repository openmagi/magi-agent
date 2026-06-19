from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_serializer, field_validator, model_validator

from magi_agent.ops.safety import (
    canonical_digest,
    require_digest,
    require_safe_ref,
    safe_metadata,
    sanitize_validation_error,
    serialize_safe_value,
)


DecisionKind = Literal["policy", "kernel", "rollback", "fallback", "compliance_report"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_ZERO_DIGEST = "sha256:" + "0" * 64
_AUTHORITY_FLAG_FIELDS = (
    "public_route_attached",
    "production_write",
    "user_visible_output_allowed",
    "model_called",
    "toolhost_dispatched",
    "network_call_allowed",
    "raw_payload_attached",
)


def _digest_payload(payload: Mapping[str, object]) -> str:
    return canonical_digest(payload)


def _utc_datetime(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _utc_iso(value: datetime) -> str:
    return _utc_datetime(value, field_name="timestamp").isoformat().replace("+00:00", "Z")


def _safe_reason_codes(value: tuple[str, ...]) -> tuple[str, ...]:
    if not value:
        raise ValueError("reasonCodes are required")
    safe = tuple(require_safe_ref(item, field_name="reasonCodes") for item in value)
    if len(set(safe)) != len(safe):
        raise ValueError("reasonCodes must be unique")
    return tuple(sorted(safe))


def _safe_compliance_metadata(value: Mapping[str, object]) -> dict[str, object]:
    safe = safe_metadata(value)
    for key in safe:
        compact = "".join(character for character in key.lower() if character.isalnum())
        if "session" in compact:
            raise ValueError("metadata must not expose raw, private, or credential material")
    return safe


class _ComplianceModel(BaseModel):
    model_config = _MODEL_CONFIG

    def __init__(self, **data: Any) -> None:
        try:
            super().__init__(**data)
        except ValidationError as exc:
            raise sanitize_validation_error(exc, title=type(self).__name__) from None

    @classmethod
    def model_validate(cls, obj: object, *args: Any, **kwargs: Any) -> Self:
        try:
            return super().model_validate(obj, *args, **kwargs)
        except ValidationError as exc:
            raise sanitize_validation_error(exc, title=cls.__name__) from None

    @classmethod
    def model_validate_json(
        cls,
        json_data: str | bytes | bytearray,
        *args: Any,
        **kwargs: Any,
    ) -> Self:
        try:
            return super().model_validate_json(json_data, *args, **kwargs)
        except ValidationError as exc:
            raise sanitize_validation_error(exc, title=cls.__name__) from None

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: object) -> Self:
        _ = _fields_set
        return cls.model_validate(values)

    def model_copy(self, *, update: Mapping[str, object] | None = None, deep: bool = False) -> Self:
        payload = self.model_dump(by_alias=True, mode="python")
        if update:
            payload.update(update)
        _ = deep
        return type(self).model_validate(payload)


class ComplianceAuthorityFlags(_ComplianceModel):
    public_route_attached: Literal[False] = Field(default=False, alias="publicRouteAttached")
    production_write: Literal[False] = Field(default=False, alias="productionWrite")
    user_visible_output_allowed: Literal[False] = Field(
        default=False,
        alias="userVisibleOutputAllowed",
    )
    model_called: Literal[False] = Field(default=False, alias="modelCalled")
    toolhost_dispatched: Literal[False] = Field(default=False, alias="toolHostDispatched")
    network_call_allowed: Literal[False] = Field(default=False, alias="networkCallAllowed")
    raw_payload_attached: Literal[False] = Field(default=False, alias="rawPayloadAttached")

    def __getattribute__(self, name: str) -> object:
        if name in _AUTHORITY_FLAG_FIELDS:
            return False
        return super().__getattribute__(name)

    @model_validator(mode="before")
    @classmethod
    def _force_false(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        for field_name, field in cls.model_fields.items():
            payload[field.alias or field_name] = False
            payload.pop(field_name, None)
        return payload

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: object) -> Self:
        _ = _fields_set, values
        raise ValueError("model_construct is disabled for compliance authority flags")

    def model_copy(self, *, update: Mapping[str, object] | None = None, deep: bool = False) -> Self:
        if update:
            raise ValueError("model_copy update is disabled for compliance authority flags")
        _ = deep
        return type(self).model_validate(self.model_dump(by_alias=True, mode="json"))

    @field_serializer(
        "public_route_attached",
        "production_write",
        "user_visible_output_allowed",
        "model_called",
        "toolhost_dispatched",
        "network_call_allowed",
        "raw_payload_attached",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False

    def public_projection(self) -> dict[str, bool]:
        return {
            "publicRouteAttached": False,
            "productionWrite": False,
            "userVisibleOutputAllowed": False,
            "modelCalled": False,
            "toolHostDispatched": False,
            "networkCallAllowed": False,
            "rawPayloadAttached": False,
        }


class RollbackFallbackDiagnosticRef(_ComplianceModel):
    diagnostic_ref: str = Field(alias="diagnosticRef")
    diagnostic_digest: str = Field(alias="diagnosticDigest")
    route_ref: str = Field(alias="routeRef")
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")
    metadata: Mapping[str, object] = Field(default_factory=dict)

    @field_validator("diagnostic_ref", "route_ref")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        return require_safe_ref(value, field_name="diagnostic ref")

    @field_validator("diagnostic_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return require_digest(value)

    @field_validator("reason_codes")
    @classmethod
    def _validate_reasons(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _safe_reason_codes(value)

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(cls, value: Mapping[str, object]) -> Mapping[str, object]:
        return _safe_compliance_metadata(value)

    def public_projection(self) -> dict[str, object]:
        return {
            "diagnosticRef": require_safe_ref(self.diagnostic_ref, field_name="diagnostic ref"),
            "diagnosticDigest": require_digest(self.diagnostic_digest),
            "routeRef": require_safe_ref(self.route_ref, field_name="diagnostic ref"),
            "reasonCodes": list(_safe_reason_codes(self.reason_codes)),
            "metadata": {
                key: serialize_safe_value(value)
                for key, value in _safe_compliance_metadata(self.metadata).items()
            },
        }


class ComplianceReportRef(_ComplianceModel):
    report_ref: str = Field(alias="reportRef")
    report_digest: str = Field(alias="reportDigest")
    policy_snapshot_digest: str = Field(alias="policySnapshotDigest")
    evidence_ledger_digest: str = Field(alias="evidenceLedgerDigest")
    generated_at: datetime = Field(alias="generatedAt")
    metadata: Mapping[str, object] = Field(default_factory=dict)

    @field_validator("report_ref")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        return require_safe_ref(value, field_name="compliance report ref")

    @field_validator("report_digest", "policy_snapshot_digest", "evidence_ledger_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return require_digest(value)

    @field_validator("generated_at")
    @classmethod
    def _validate_generated_at(cls, value: datetime) -> datetime:
        return _utc_datetime(value, field_name="generatedAt")

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(cls, value: Mapping[str, object]) -> Mapping[str, object]:
        return _safe_compliance_metadata(value)

    def public_projection(self) -> dict[str, object]:
        return {
            "reportRef": require_safe_ref(self.report_ref, field_name="compliance report ref"),
            "reportDigest": require_digest(self.report_digest),
            "policySnapshotDigest": require_digest(self.policy_snapshot_digest),
            "evidenceLedgerDigest": require_digest(self.evidence_ledger_digest),
            "generatedAt": _utc_iso(self.generated_at),
            "metadata": {
                key: serialize_safe_value(value)
                for key, value in _safe_compliance_metadata(self.metadata).items()
            },
        }


class PolicyKernelDecisionRecord(_ComplianceModel):
    schema_version: Literal["openmagi.security.compliance_decision.v1"] = Field(
        default="openmagi.security.compliance_decision.v1",
        alias="schemaVersion",
    )
    decision_id: str = Field(alias="decisionId")
    decision_kind: DecisionKind = Field(alias="decisionKind")
    subject_ref: str = Field(alias="subjectRef")
    policy_snapshot_digest: str = Field(alias="policySnapshotDigest")
    kernel_snapshot_digest: str = Field(alias="kernelSnapshotDigest")
    evidence_digest: str = Field(alias="evidenceDigest")
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")
    fallback_diagnostic: RollbackFallbackDiagnosticRef | None = Field(
        default=None,
        alias="fallbackDiagnostic",
    )
    compliance_report: ComplianceReportRef | None = Field(
        default=None,
        alias="complianceReport",
    )
    decided_at: datetime = Field(default_factory=lambda: datetime.now(UTC), alias="decidedAt")
    metadata: Mapping[str, object] = Field(default_factory=dict)
    decision_digest: str = Field(default=_ZERO_DIGEST, alias="decisionDigest")
    authority_flags: ComplianceAuthorityFlags = Field(
        default_factory=ComplianceAuthorityFlags,
        alias="authorityFlags",
    )

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: object) -> Self:
        _ = _fields_set
        values["authorityFlags"] = ComplianceAuthorityFlags()
        return cls.model_validate(values)

    def model_copy(self, *, update: Mapping[str, object] | None = None, deep: bool = False) -> Self:
        payload = self.model_dump(by_alias=True, mode="python")
        if update:
            payload.update(update)
        payload["authorityFlags"] = ComplianceAuthorityFlags()
        _ = deep
        return type(self).model_validate(payload)

    @field_validator("decision_id", "subject_ref")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        return require_safe_ref(value, field_name="decision ref")

    @field_validator(
        "policy_snapshot_digest",
        "kernel_snapshot_digest",
        "evidence_digest",
        "decision_digest",
    )
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return require_digest(value)

    @field_validator("reason_codes")
    @classmethod
    def _validate_reasons(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _safe_reason_codes(value)

    @field_validator("decided_at")
    @classmethod
    def _validate_decided_at(cls, value: datetime) -> datetime:
        return _utc_datetime(value, field_name="decidedAt")

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(cls, value: Mapping[str, object]) -> Mapping[str, object]:
        return _safe_compliance_metadata(value)

    @model_validator(mode="after")
    def _validate_digest_state(self) -> Self:
        if self.decision_digest == _ZERO_DIGEST:
            return self
        expected = _decision_digest(self)
        if self.decision_digest != expected:
            raise ValueError("decisionDigest must bind the compliance decision payload")
        return self

    def public_projection(self) -> dict[str, object]:
        return {
            "schemaVersion": "openmagi.security.compliance.public.v1",
            "decisionId": require_safe_ref(self.decision_id, field_name="decision ref"),
            "decisionKind": self.decision_kind,
            "subjectRef": require_safe_ref(self.subject_ref, field_name="decision ref"),
            "policySnapshotDigest": require_digest(self.policy_snapshot_digest),
            "kernelSnapshotDigest": require_digest(self.kernel_snapshot_digest),
            "evidenceDigest": require_digest(self.evidence_digest),
            "reasonCodes": list(_safe_reason_codes(self.reason_codes)),
            "decidedAt": _utc_iso(self.decided_at),
            "decisionDigest": require_digest(self.decision_digest),
            "fallbackDiagnostic": (
                self.fallback_diagnostic.public_projection()
                if self.fallback_diagnostic is not None
                else None
            ),
            "complianceReport": (
                self.compliance_report.public_projection()
                if self.compliance_report is not None
                else None
            ),
            "metadata": {
                key: serialize_safe_value(value)
                for key, value in _safe_compliance_metadata(self.metadata).items()
            },
            "authorityFlags": self.authority_flags.public_projection(),
        }


def build_compliance_report_ref(
    *,
    reportRef: str,
    policySnapshotDigest: str,
    evidenceLedgerDigest: str,
    generatedAt: datetime,
    metadata: Mapping[str, object] | None = None,
) -> ComplianceReportRef:
    safe_ref = require_safe_ref(reportRef, field_name="compliance report ref")
    safe_policy = require_digest(policySnapshotDigest)
    safe_evidence = require_digest(evidenceLedgerDigest)
    safe_generated = _utc_datetime(generatedAt, field_name="generatedAt")
    safe_meta = _safe_compliance_metadata(metadata or {})
    report_digest = _digest_payload(
        {
            "schemaVersion": "openmagi.security.compliance_report_ref.v1",
            "reportRef": safe_ref,
            "policySnapshotDigest": safe_policy,
            "evidenceLedgerDigest": safe_evidence,
            "generatedAt": _utc_iso(safe_generated),
            "metadata": safe_meta,
        }
    )
    return ComplianceReportRef(
        reportRef=safe_ref,
        reportDigest=report_digest,
        policySnapshotDigest=safe_policy,
        evidenceLedgerDigest=safe_evidence,
        generatedAt=safe_generated,
        metadata=safe_meta,
    )


def record_policy_kernel_decision(
    *,
    decisionId: str,
    decisionKind: DecisionKind,
    subjectRef: str,
    policySnapshotDigest: str,
    kernelSnapshotDigest: str,
    evidenceDigest: str,
    reasonCodes: tuple[str, ...],
    fallbackDiagnostic: RollbackFallbackDiagnosticRef | None = None,
    complianceReport: ComplianceReportRef | None = None,
    decidedAt: datetime | None = None,
    metadata: Mapping[str, object] | None = None,
) -> PolicyKernelDecisionRecord:
    record = PolicyKernelDecisionRecord(
        decisionId=decisionId,
        decisionKind=decisionKind,
        subjectRef=subjectRef,
        policySnapshotDigest=policySnapshotDigest,
        kernelSnapshotDigest=kernelSnapshotDigest,
        evidenceDigest=evidenceDigest,
        reasonCodes=reasonCodes,
        fallbackDiagnostic=fallbackDiagnostic,
        complianceReport=complianceReport,
        decidedAt=decidedAt or datetime.now(UTC),
        metadata=metadata or {},
        authorityFlags=ComplianceAuthorityFlags(),
    )
    return record.model_copy(update={"decisionDigest": _decision_digest(record)})


def _decision_digest(record: PolicyKernelDecisionRecord) -> str:
    return _digest_payload(
        {
            "schemaVersion": record.schema_version,
            "decisionId": record.decision_id,
            "decisionKind": record.decision_kind,
            "subjectRef": record.subject_ref,
            "policySnapshotDigest": record.policy_snapshot_digest,
            "kernelSnapshotDigest": record.kernel_snapshot_digest,
            "evidenceDigest": record.evidence_digest,
            "reasonCodes": list(record.reason_codes),
            "fallbackDiagnostic": (
                record.fallback_diagnostic.public_projection()
                if record.fallback_diagnostic is not None
                else None
            ),
            "complianceReport": (
                record.compliance_report.public_projection()
                if record.compliance_report is not None
                else None
            ),
            "decidedAt": _utc_iso(record.decided_at),
            "metadata": _safe_compliance_metadata(record.metadata),
        }
    )
