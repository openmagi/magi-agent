from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator


_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_SAFE_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,220}$")
_UNSAFE_REF_RE = re.compile(
    r"(?:"
    r"\s|"
    r"[\\/'\"`$=;|&<>]|"
    r"\.\.|"
    r"://|"
    r"\bbearer\b|"
    r"authorization|"
    r"cookie|"
    r"api[_-]?key|"
    r"secret|"
    r"token|"
    r"password|"
    r"private|"
    r"session[_-]?key|"
    r"raw[_-]?(?:transcript|output|log)|"
    r"hidden[_-]?reasoning|"
    r"^sk-|"
    r"gh[opusr]_|"
    r"github_pat_|"
    r"xox[a-z]-|"
    r"AIza"
    r")",
    re.IGNORECASE,
)


class _Pr21Model(BaseModel):
    model_config = _MODEL_CONFIG

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        return cls(**values)

    def model_copy(
        self,
        *,
        update: Mapping[str, object] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            name_to_alias = {
                name: field.alias
                for name, field in self.__class__.model_fields.items()
                if field.alias is not None
            }
            data.update({name_to_alias.get(key, key): value for key, value in update.items()})
        return self.__class__.model_validate(data)


class QueryStateAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    live_model_call_allowed: Literal[False] = Field(
        default=False,
        alias="liveModelCallAllowed",
    )
    tool_execution_allowed: Literal[False] = Field(
        default=False,
        alias="toolExecutionAllowed",
    )
    memory_provider_call_allowed: Literal[False] = Field(
        default=False,
        alias="memoryProviderCallAllowed",
    )
    memory_write_allowed: Literal[False] = Field(default=False, alias="memoryWriteAllowed")
    production_transcript_write_allowed: Literal[False] = Field(
        default=False,
        alias="productionTranscriptWriteAllowed",
    )
    user_visible_output_allowed: Literal[False] = Field(
        default=False,
        alias="userVisibleOutputAllowed",
    )
    live_attachment_allowed: Literal[False] = Field(
        default=False,
        alias="liveAttachmentAllowed",
    )

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        return cls(**_false_flag_payload(cls))

    def model_copy(
        self,
        *,
        update: Mapping[str, object] | None = None,
        deep: bool = False,
    ) -> Self:
        return self.__class__()

    @model_validator(mode="before")
    @classmethod
    def _force_false_inputs(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        return _false_flag_payload(cls)

    @field_serializer(
        "live_model_call_allowed",
        "tool_execution_allowed",
        "memory_provider_call_allowed",
        "memory_write_allowed",
        "production_transcript_write_allowed",
        "user_visible_output_allowed",
        "live_attachment_allowed",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class QueryState(_Pr21Model):
    schema_version: Literal["openmagi.queryState.v1"] = Field(
        default="openmagi.queryState.v1",
        alias="schemaVersion",
    )
    current_turn_id: str = Field(alias="currentTurnId")
    session_id: str = Field(alias="sessionId")
    compacted_transcript_summary_ref: str | None = Field(
        default=None,
        alias="compactedTranscriptSummaryRef",
    )
    compacted_transcript_digest: str | None = Field(
        default=None,
        alias="compactedTranscriptDigest",
    )
    restore_provenance_digest: str | None = Field(
        default=None,
        alias="restoreProvenanceDigest",
    )
    recent_event_refs: tuple[str, ...] = Field(default=(), alias="recentEventRefs")
    outstanding_control_request_refs: tuple[str, ...] = Field(
        default=(),
        alias="outstandingControlRequestRefs",
    )
    latest_read_ledger_digests: tuple[str, ...] = Field(
        default=(),
        alias="latestReadLedgerDigests",
    )
    pending_tool_result_refs: tuple[str, ...] = Field(
        default=(),
        alias="pendingToolResultRefs",
    )
    child_agent_summary_refs: tuple[str, ...] = Field(
        default=(),
        alias="childAgentSummaryRefs",
    )
    child_agent_evidence_refs: tuple[str, ...] = Field(
        default=(),
        alias="childAgentEvidenceRefs",
    )
    verification_evidence_refs: tuple[str, ...] = Field(
        default=(),
        alias="verificationEvidenceRefs",
    )
    model_context_config_refs: tuple[str, ...] = Field(
        default=(),
        alias="modelContextConfigRefs",
    )
    cache_safe_param_refs: tuple[str, ...] = Field(
        default=(),
        alias="cacheSafeParamRefs",
    )
    cache_safe_param_digests: tuple[str, ...] = Field(
        default=(),
        alias="cacheSafeParamDigests",
    )
    authority_flags: QueryStateAuthorityFlags = Field(
        default_factory=QueryStateAuthorityFlags,
        alias="authorityFlags",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_schema_and_authority(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        data.pop("schema_version", None)
        data.pop("authority_flags", None)
        data["schemaVersion"] = "openmagi.queryState.v1"
        data["authorityFlags"] = QueryStateAuthorityFlags().model_dump(by_alias=True)
        return data

    @field_validator("current_turn_id", "session_id")
    @classmethod
    def _validate_identity_ref(cls, value: str) -> str:
        return validate_safe_ref(value)

    @field_validator(
        "compacted_transcript_summary_ref",
        "recent_event_refs",
        "outstanding_control_request_refs",
        "pending_tool_result_refs",
        "child_agent_summary_refs",
        "child_agent_evidence_refs",
        "verification_evidence_refs",
        "model_context_config_refs",
        "cache_safe_param_refs",
    )
    @classmethod
    def _validate_refs(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            return validate_safe_ref(value)
        if isinstance(value, tuple):
            return tuple(dict.fromkeys(validate_safe_ref(item) for item in value))
        return value

    @field_validator("compacted_transcript_digest", "restore_provenance_digest")
    @classmethod
    def _validate_optional_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_digest(value)

    @field_validator("latest_read_ledger_digests", "cache_safe_param_digests")
    @classmethod
    def _validate_digests(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(validate_digest(item) for item in value))

    def public_projection(self) -> dict[str, object]:
        return self.model_dump(by_alias=True, mode="json", exclude_none=True)


def validate_digest(value: str) -> str:
    if not _DIGEST_RE.fullmatch(value):
        raise ValueError("digest values must be sha256 digests")
    return value


def validate_safe_ref(value: str) -> str:
    text = value.strip()
    if not _SAFE_REF_RE.fullmatch(text) or _UNSAFE_REF_RE.search(text):
        raise ValueError("safe refs must be sanitized public refs")
    return text


def _false_flag_payload(cls: type[BaseModel]) -> dict[str, bool]:
    return {field.alias or name: False for name, field in cls.model_fields.items()}


__all__ = [
    "QueryState",
    "QueryStateAuthorityFlags",
    "validate_digest",
    "validate_safe_ref",
]
