from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from magi_agent.evidence.types import (
    EvidenceContractFailure,
    EvidenceContractVerdict,
    EvidenceRecord,
    EvidenceRequirement,
)
from magi_agent.ops.authority import FalseOnlyAuthorityModel
from magi_agent.shared.tool_preview import sanitize_tool_preview


_SECRET_FIELD_FRAGMENTS = (
    "api_key",
    "apikey",
    "auth_token",
    "bearer_token",
    "client_secret",
    "id_token",
    "password",
    "passphrase",
    "private_key",
    "refresh_token",
    "secret",
    "service_role_key",
    "session_token",
    "token",
)
_SECRET_FIELD_FAILURE_PAYLOAD_KEYS = frozenset(
    (
        "actual",
        "actualValue",
        "expected",
        "expectedPattern",
        "expectedValue",
        "value",
    )
)
_PUBLIC_CREDENTIAL_FIELD_NAMES = frozenset(
    (
        "authorization",
        "proxy_authorization",
        "proxyauthorization",
        "cookie",
        "set_cookie",
        "setcookie",
        "credential",
        "credentials",
    )
)
_PUBLIC_IDENTIFIER_FIELD_PREFIXES = {
    "agentid": "agent",
    "childagentid": "agent",
    "childexecutionid": "exec",
    "childtaskid": "task",
    "executionid": "exec",
    "parentagentid": "agent",
    "parentexecutionid": "exec",
    "policysnapshotid": "policy",
    "taskid": "task",
}
_PRIVATE_PATH_TEXT_RE = re.compile(
    r"(?:"
    r"~[\\/][^,\s\"'{}\]\)]+|"
    r"(?<![A-Za-z0-9:/])/(?:[^/,\s\"'{}\]\)]+)(?:/[^,\s\"'{}\]\)]+)*|"
    r"[A-Za-z]:[\\/][^,\s\"'{}\]\)]+|"
    r"\\\\[^,\s\"'{}\]\)]+|"
    r"pvc-[A-Za-z0-9-]+"
    r")",
    re.IGNORECASE,
)
_SOURCE_LOCATOR_TEXT_RE = re.compile(
    r"(?:"
    r"\b(?:https?|s3|gs|file|ssh|git)://[^\s\"'{}\]\)]+|"
    r"\bgit@[A-Za-z0-9_.-]+:[^\s\"'{}\]\)]+|"
    r"(?<![A-Za-z0-9])(?:search|source|ref):[^\s\"'{}\]\)]+"
    r")",
    re.IGNORECASE,
)
_MAX_REQUIREMENT_PATTERN_PREVIEW = 160
_REDACTED = "[redacted]"


class PublicEvidenceRecordReport(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    type: str
    status: str
    observed_at: int | float = Field(alias="observedAt")
    source: dict[str, object]
    fields: dict[str, object] = Field(default_factory=dict)
    preview: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class PublicEvidenceFailureReport(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    code: str
    contract_id: str = Field(alias="contractId")
    requirement_type: str | None = Field(default=None, alias="requirementType")
    message: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class PublicEvidenceRequirementReport(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    type: str
    after: str | None = None
    command_pattern: str | None = Field(default=None, alias="commandPattern")
    exit_code: int | None = Field(default=None, alias="exitCode")
    fields: dict[str, object] = Field(default_factory=dict)


class PublicEvidenceVerdictReport(FalseOnlyAuthorityModel):
    contract_id: str = Field(alias="contractId")
    ok: bool
    state: str
    enforcement: str
    missing_requirements: tuple[PublicEvidenceRequirementReport, ...] = Field(
        alias="missingRequirements"
    )
    matched_evidence: tuple[PublicEvidenceRecordReport, ...] = Field(alias="matchedEvidence")
    failures: tuple[PublicEvidenceFailureReport, ...]
    retry_message: str | None = Field(default=None, alias="retryMessage")
    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    execution_attached: Literal[False] = Field(default=False, alias="executionAttached")


def public_evidence_record_report(record: EvidenceRecord) -> PublicEvidenceRecordReport:
    return PublicEvidenceRecordReport(
        type=record.type,
        status=record.status,
        observedAt=record.observed_at,
        source=_redact_mapping(record.source.model_dump(by_alias=True, exclude_none=True)),
        fields=_public_record_fields(record.fields, record.metadata),
        preview=public_projection_safe_text(record.preview) if record.preview is not None else None,
        metadata=_redact_mapping(record.metadata),
    )


def public_evidence_verdict_report(
    verdict: EvidenceContractVerdict,
) -> PublicEvidenceVerdictReport:
    return PublicEvidenceVerdictReport(
        contractId=verdict.contract_id,
        ok=verdict.ok,
        state=verdict.state,
        enforcement=verdict.enforcement,
        missingRequirements=tuple(
            _public_requirement_report(requirement)
            for requirement in verdict.missing_requirements
        ),
        matchedEvidence=tuple(
            public_evidence_record_report(record) for record in verdict.matched_evidence
        ),
        failures=tuple(_public_failure_report(failure) for failure in verdict.failures),
        retryMessage=(
            public_projection_safe_text(verdict.retry_message)
            if verdict.retry_message is not None
            else None
        ),
        trafficAttached=False,
        executionAttached=False,
    )


def public_evidence_metadata_report(metadata: Mapping[str, object]) -> dict[str, object]:
    return _redact_mapping(metadata)


def _public_failure_report(failure: EvidenceContractFailure) -> PublicEvidenceFailureReport:
    return PublicEvidenceFailureReport(
        code=failure.code,
        contractId=failure.contract_id,
        requirementType=failure.requirement_type,
        message=public_projection_safe_text(failure.message) if failure.message is not None else None,
        metadata=_redact_failure_metadata(failure.metadata),
    )


def _public_requirement_report(requirement: EvidenceRequirement) -> PublicEvidenceRequirementReport:
    return PublicEvidenceRequirementReport(
        type=requirement.type,
        after=requirement.after,
        commandPattern=_sanitize_requirement_pattern(requirement.command_pattern),
        exitCode=requirement.exit_code,
        fields={
            field_name: _redact_requirement_matcher(field_name, matcher)
            for field_name, matcher in requirement.fields.items()
        },
    )


def _redact_mapping(value: Mapping[str, object]) -> dict[str, object]:
    return {
        _validate_metadata_key(key): _redact_value(key, nested)
        for key, nested in value.items()
    }


def _validate_metadata_key(key: object) -> str:
    if type(key) is not str:
        raise ValueError("metadata mapping keys must be strings")
    return key


def _redact_failure_metadata(value: Mapping[str, object]) -> dict[str, object]:
    raw_field = value.get("field")
    secret_field = type(raw_field) is str and _is_secret_key(raw_field)
    return {
        key: (
            _redact_secret_requirement_value(nested)
            if secret_field and key in _SECRET_FIELD_FAILURE_PAYLOAD_KEYS
            else _redact_value(key, nested)
        )
        for key, nested in value.items()
    }


def _public_record_fields(
    fields: Mapping[str, object],
    metadata: Mapping[str, object],
) -> dict[str, object]:
    safe_fields = _public_safe_fields(metadata)
    return {
        key: (
            _redact_value(key, value)
            if key in safe_fields and not _is_secret_key(key)
            else _REDACTED
        )
        for key, value in fields.items()
    }


def _public_safe_fields(metadata: Mapping[str, object]) -> frozenset[str]:
    value = metadata.get("publicSafeFields")
    if not isinstance(value, tuple | list):
        return frozenset()
    return frozenset(field_name for field_name in value if type(field_name) is str)


def _redact_value(key: str, value: object) -> object:
    if _is_secret_key(key):
        return _REDACTED
    identifier_prefix = _public_identifier_prefix(key)
    if isinstance(value, str) and identifier_prefix is not None:
        return _public_identifier_value(identifier_prefix, value)
    if isinstance(value, Mapping):
        return _redact_mapping(value)
    if isinstance(value, tuple | list):
        return [_redact_value(key, item) for item in value]
    if isinstance(value, str):
        sanitized = public_projection_safe_text(value)
        if sanitized == _REDACTED:
            return _REDACTED
        return sanitized
    return value


def _is_secret_key(key: str) -> bool:
    normalized = key.replace("-", "_").lower()
    if normalized in _PUBLIC_CREDENTIAL_FIELD_NAMES or normalized.replace("_", "") in (
        _PUBLIC_CREDENTIAL_FIELD_NAMES
    ):
        return True
    return any(fragment in normalized for fragment in _SECRET_FIELD_FRAGMENTS)


def _public_identifier_prefix(key: str) -> str | None:
    normalized = re.sub(r"[^a-z0-9]", "", key.casefold())
    return _PUBLIC_IDENTIFIER_FIELD_PREFIXES.get(normalized)


def _public_identifier_value(prefix: str, value: str) -> str:
    return f"{prefix}:sha256:{sha256(value.encode('utf-8')).hexdigest()}"


def _sanitize_requirement_pattern(value: str | None) -> str | None:
    if value is None:
        return None
    return _truncate_requirement_pattern(public_projection_safe_text(value))


def _truncate_requirement_pattern(value: str) -> str:
    if len(value) > _MAX_REQUIREMENT_PATTERN_PREVIEW:
        return f"{value[: _MAX_REQUIREMENT_PATTERN_PREVIEW - 3]}..."
    return value


def _redact_requirement_matcher(
    field_name: str,
    matcher: object,
) -> dict[str, object]:
    dumped = matcher.model_dump(by_alias=True, exclude_none=True) if hasattr(
        matcher,
        "model_dump",
    ) else matcher
    if not isinstance(dumped, Mapping):
        return {}
    return {
        key: _redact_requirement_matcher_value(field_name, value)
        for key, value in dumped.items()
    }


def _redact_requirement_matcher_value(field_name: str, value: object) -> object:
    if _is_secret_key(field_name):
        return _redact_secret_requirement_value(value)
    if isinstance(value, Mapping):
        return {
            key: (
                _redact_secret_requirement_value(nested)
                if _is_secret_key(str(key))
                else _redact_requirement_matcher_value(field_name, nested)
            )
            for key, nested in value.items()
        }
    if isinstance(value, tuple | list):
        return [_redact_requirement_matcher_value(field_name, item) for item in value]
    if isinstance(value, str):
        sanitized = public_projection_safe_text(value)
        if sanitized != value:
            return _REDACTED
        return _truncate_requirement_pattern(sanitized)
    return value


def _redact_secret_requirement_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _redact_secret_requirement_value(nested) for key, nested in value.items()}
    if isinstance(value, tuple | list):
        return [_redact_secret_requirement_value(item) for item in value]
    return _REDACTED


def public_projection_safe_text(value: str, *, limit: int = 400) -> str:
    sanitized = sanitize_tool_preview(value)
    if (
        _PRIVATE_PATH_TEXT_RE.search(value)
        or _PRIVATE_PATH_TEXT_RE.search(sanitized)
        or _SOURCE_LOCATOR_TEXT_RE.search(value)
        or _SOURCE_LOCATOR_TEXT_RE.search(sanitized)
    ):
        return _REDACTED
    if len(sanitized) > limit:
        return f"{sanitized[: limit - 3]}..."
    return sanitized


__all__ = [
    "PublicEvidenceFailureReport",
    "PublicEvidenceRecordReport",
    "PublicEvidenceRequirementReport",
    "PublicEvidenceVerdictReport",
    "public_evidence_metadata_report",
    "public_evidence_record_report",
    "public_evidence_verdict_report",
    "public_projection_safe_text",
]
