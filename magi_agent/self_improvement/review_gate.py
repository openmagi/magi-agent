from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal, Self, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from magi_agent.runtime.receipt_utils import (
    canonical_digest,
    has_unsafe_marker,
    sanitize_public_text,
)


SelfImprovementPromotionScope: TypeAlias = Literal[
    "recipe",
    "harness_config",
    "plugin_config",
    "test_fixture",
    "docs",
]
SelfImprovementReviewDecision: TypeAlias = Literal[
    "approved_for_promotion",
    "changes_requested",
    "rejected",
]
SelfImprovementReviewStatus: TypeAlias = Literal[
    "disabled",
    "blocked",
    "review_recorded_local_fake",
]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_SHA256_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_SAFE_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:@=-]{1,191}$")
_SAFE_REASON_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")


class SelfImprovementAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    model_call_enabled: Literal[False] = Field(default=False, alias="modelCallEnabled")
    live_adk_runner_enabled: Literal[False] = Field(
        default=False,
        alias="liveAdkRunnerEnabled",
    )
    tool_execution_enabled: Literal[False] = Field(default=False, alias="toolExecutionEnabled")
    repo_mutation_enabled: Literal[False] = Field(default=False, alias="repoMutationEnabled")
    config_mutation_enabled: Literal[False] = Field(
        default=False,
        alias="configMutationEnabled",
    )
    deploy_enabled: Literal[False] = Field(default=False, alias="deployEnabled")
    secret_mutation_enabled: Literal[False] = Field(
        default=False,
        alias="secretMutationEnabled",
    )
    db_mutation_enabled: Literal[False] = Field(default=False, alias="dbMutationEnabled")
    production_write_enabled: Literal[False] = Field(
        default=False,
        alias="productionWriteEnabled",
    )
    user_visible_output_enabled: Literal[False] = Field(
        default=False,
        alias="userVisibleOutputEnabled",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_false(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        for field_name, field in cls.model_fields.items():
            payload[field.alias or field_name] = False
            payload.pop(field_name, None)
        return payload

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set, values
        return cls()

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = update, deep
        return type(self)()

    def copy(
        self,
        *,
        include: Any = None,
        exclude: Any = None,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = include, exclude, update, deep
        return type(self)()

    @field_serializer(
        "model_call_enabled",
        "live_adk_runner_enabled",
        "tool_execution_enabled",
        "repo_mutation_enabled",
        "config_mutation_enabled",
        "deploy_enabled",
        "secret_mutation_enabled",
        "db_mutation_enabled",
        "production_write_enabled",
        "user_visible_output_enabled",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class SelfImprovementReviewConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    local_fake_review_enabled: bool = Field(default=False, alias="localFakeReviewEnabled")
    live_adk_runner_enabled: Literal[False] = Field(
        default=False,
        alias="liveAdkRunnerEnabled",
    )
    automatic_promotion_enabled: Literal[False] = Field(
        default=False,
        alias="automaticPromotionEnabled",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_live_flags_false(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        payload["liveAdkRunnerEnabled"] = False
        payload.pop("live_adk_runner_enabled", None)
        payload["automaticPromotionEnabled"] = False
        payload.pop("automatic_promotion_enabled", None)
        return payload

    @field_serializer("live_adk_runner_enabled", "automatic_promotion_enabled")
    def _serialize_false(self, _value: object) -> bool:
        return False

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        return cls.model_validate(values)

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = deep
        payload = self.model_dump(by_alias=True)
        if update:
            payload.update(update)
        return type(self).model_validate(payload)

    def copy(
        self,
        *,
        include: Any = None,
        exclude: Any = None,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = include, exclude, update, deep
        return self.model_copy(update=update)


class SelfImprovementReviewRequest(BaseModel):
    model_config = _MODEL_CONFIG

    review_id: str = Field(alias="reviewId")
    proposal_digest: str = Field(alias="proposalDigest")
    affected_digest_refs: tuple[str, ...] = Field(alias="affectedDigestRefs")
    promotion_scope: SelfImprovementPromotionScope = Field(alias="promotionScope")
    policy_snapshot_digest: str = Field(alias="policySnapshotDigest")
    reviewer_refs: tuple[str, ...] = Field(alias="reviewerRefs")
    decision: SelfImprovementReviewDecision
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    summary: str | None = None
    raw_prompt: str | None = Field(default=None, alias="rawPrompt", exclude=True, repr=False)
    raw_output: str | None = Field(default=None, alias="rawOutput", exclude=True, repr=False)
    raw_private_path: str | None = Field(
        default=None,
        alias="rawPrivatePath",
        exclude=True,
        repr=False,
    )
    tool_logs: str | None = Field(default=None, alias="toolLogs", exclude=True, repr=False)
    hidden_reasoning: str | None = Field(
        default=None,
        alias="hiddenReasoning",
        exclude=True,
        repr=False,
    )

    @model_validator(mode="before")
    @classmethod
    def _normalize_field_names(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        payload = dict(value)
        aliases = {
            "review_id": "reviewId",
            "proposal_digest": "proposalDigest",
            "affected_digest_refs": "affectedDigestRefs",
            "promotion_scope": "promotionScope",
            "policy_snapshot_digest": "policySnapshotDigest",
            "reviewer_refs": "reviewerRefs",
            "reason_codes": "reasonCodes",
        }
        for field_name, alias in aliases.items():
            if field_name in payload:
                payload[alias] = payload.pop(field_name)
        return payload

    @field_validator("review_id")
    @classmethod
    def _validate_review_id(cls, value: str) -> str:
        return _safe_ref(value, "reviewId", prefixes=("self-improvement-review:", "ref:"))

    @field_validator("proposal_digest", "policy_snapshot_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return _safe_digest(value, "digest")

    @field_validator("affected_digest_refs", mode="before")
    @classmethod
    def _validate_affected_digest_refs(cls, value: object) -> tuple[str, ...]:
        refs = _string_tuple(value)
        if not refs:
            raise ValueError("affectedDigestRefs must not be empty")
        return tuple(_safe_digest(ref, "affectedDigestRefs") for ref in refs)

    @field_validator("reviewer_refs", mode="before")
    @classmethod
    def _validate_reviewer_refs(cls, value: object) -> tuple[str, ...]:
        refs = _string_tuple(value)
        if not refs:
            raise ValueError("reviewerRefs must not be empty")
        return tuple(_safe_ref(ref, "reviewerRefs", prefixes=("reviewer:", "approver:", "ref:")) for ref in refs)

    @field_validator("reason_codes", mode="before")
    @classmethod
    def _validate_reason_codes(cls, value: object) -> tuple[str, ...]:
        return tuple(_safe_reason_code(item, "reasonCodes") for item in _string_tuple(value))

    @field_validator("summary")
    @classmethod
    def _validate_summary(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _safe_public_text(value, "summary")


class SelfImprovementReviewRecord(BaseModel):
    model_config = _MODEL_CONFIG

    schema_version: Literal["selfImprovementReviewRecord.v1"] = Field(
        default="selfImprovementReviewRecord.v1",
        alias="schemaVersion",
    )
    review_id: str = Field(alias="reviewId")
    review_digest: str = Field(alias="reviewDigest")
    proposal_digest: str = Field(alias="proposalDigest")
    affected_digest_refs: tuple[str, ...] = Field(alias="affectedDigestRefs")
    promotion_scope: SelfImprovementPromotionScope = Field(alias="promotionScope")
    policy_snapshot_digest: str = Field(alias="policySnapshotDigest")
    reviewer_refs: tuple[str, ...] = Field(alias="reviewerRefs")
    decision: SelfImprovementReviewDecision
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    summary: str | None = None
    execution_default: Literal["denied"] = Field(default="denied", alias="executionDefault")
    authority_flags: SelfImprovementAuthorityFlags = Field(
        default_factory=SelfImprovementAuthorityFlags,
        alias="authorityFlags",
    )

    @field_validator("review_id")
    @classmethod
    def _validate_review_id(cls, value: str) -> str:
        return _safe_ref(value, "reviewId", prefixes=("self-improvement-review:", "ref:"))

    @field_validator("review_digest", "proposal_digest", "policy_snapshot_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return _safe_digest(value, "digest")

    @field_validator("affected_digest_refs", mode="before")
    @classmethod
    def _validate_affected_digest_refs(cls, value: object) -> tuple[str, ...]:
        refs = _string_tuple(value)
        if not refs:
            raise ValueError("affectedDigestRefs must not be empty")
        return tuple(_safe_digest(ref, "affectedDigestRefs") for ref in refs)

    @field_validator("reviewer_refs", mode="before")
    @classmethod
    def _validate_reviewer_refs(cls, value: object) -> tuple[str, ...]:
        refs = _string_tuple(value)
        if not refs:
            raise ValueError("reviewerRefs must not be empty")
        return tuple(_safe_ref(ref, "reviewerRefs", prefixes=("reviewer:", "approver:", "ref:")) for ref in refs)

    @field_validator("reason_codes", mode="before")
    @classmethod
    def _validate_reason_codes(cls, value: object) -> tuple[str, ...]:
        return tuple(_safe_reason_code(item, "reasonCodes") for item in _string_tuple(value))

    @field_validator("summary")
    @classmethod
    def _validate_summary(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _safe_public_text(value, "summary")

    @model_validator(mode="after")
    def _validate_review_digest(self) -> Self:
        if self.review_digest != canonical_digest(_review_digest_payload(self)):
            raise ValueError("reviewDigest mismatch")
        return self

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        return cls.model_validate(values)

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = update, deep
        raise ValueError("model_copy is disabled for SelfImprovementReviewRecord")

    def copy(
        self,
        *,
        include: Any = None,
        exclude: Any = None,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = include, exclude, update, deep
        raise ValueError("copy is disabled for SelfImprovementReviewRecord")

    @field_serializer("execution_default")
    def _serialize_execution_default(self, _value: object) -> str:
        return "denied"


class SelfImprovementReviewResult(BaseModel):
    model_config = _MODEL_CONFIG

    schema_version: Literal["selfImprovementReviewResult.v1"] = Field(
        default="selfImprovementReviewResult.v1",
        alias="schemaVersion",
    )
    status: SelfImprovementReviewStatus
    review_record: SelfImprovementReviewRecord | None = Field(
        default=None,
        alias="reviewRecord",
    )
    blocked_reason: str | None = Field(default=None, alias="blockedReason")
    authority_flags: SelfImprovementAuthorityFlags = Field(
        default_factory=SelfImprovementAuthorityFlags,
        alias="authorityFlags",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_authority_false(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        if "blocked_reason" in payload:
            payload["blockedReason"] = payload.pop("blocked_reason")
        if "review_record" in payload:
            payload["reviewRecord"] = payload.pop("review_record")
        payload["authorityFlags"] = SelfImprovementAuthorityFlags().model_dump(by_alias=True)
        payload.pop("authority_flags", None)
        return payload

    @field_validator("blocked_reason")
    @classmethod
    def _validate_blocked_reason(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _safe_reason_code(value, "blockedReason")

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        return cls.model_validate(values)

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = deep
        payload = self.model_dump(by_alias=True)
        if update:
            payload.update(update)
        return type(self).model_validate(payload)

    def copy(
        self,
        *,
        include: Any = None,
        exclude: Any = None,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = include, exclude, update, deep
        return self.model_copy(update=update)


class SelfImprovementReviewGate:
    def __init__(
        self,
        config: SelfImprovementReviewConfig | Mapping[str, object] | None = None,
    ) -> None:
        self.config = (
            config
            if isinstance(config, SelfImprovementReviewConfig)
            else SelfImprovementReviewConfig.model_validate(config or {})
        )

    def review(
        self,
        request: SelfImprovementReviewRequest | Mapping[str, object],
    ) -> SelfImprovementReviewResult:
        parsed = (
            SelfImprovementReviewRequest.model_validate(request.model_dump(by_alias=True))
            if isinstance(request, SelfImprovementReviewRequest)
            else SelfImprovementReviewRequest.model_validate(request)
        )
        if not self.config.enabled:
            return SelfImprovementReviewResult(
                status="disabled",
                blockedReason="self_improvement_review_disabled",
            )
        if not self.config.local_fake_review_enabled:
            return SelfImprovementReviewResult(
                status="blocked",
                blockedReason="self_improvement_local_fake_review_disabled",
            )
        record = _build_review_record(parsed)
        return SelfImprovementReviewResult(
            status="review_recorded_local_fake",
            reviewRecord=record,
        )


def _build_review_record(request: SelfImprovementReviewRequest) -> SelfImprovementReviewRecord:
    payload = {
        "schemaVersion": "selfImprovementReviewRecord.v1",
        "reviewId": request.review_id,
        "proposalDigest": request.proposal_digest,
        "affectedDigestRefs": request.affected_digest_refs,
        "promotionScope": request.promotion_scope,
        "policySnapshotDigest": request.policy_snapshot_digest,
        "reviewerRefs": request.reviewer_refs,
        "decision": request.decision,
        "reasonCodes": request.reason_codes,
        "summary": request.summary,
        "executionDefault": "denied",
        "authorityFlags": SelfImprovementAuthorityFlags().model_dump(by_alias=True),
    }
    return SelfImprovementReviewRecord.model_validate(
        payload | {"reviewDigest": canonical_digest(payload)}
    )


def _review_digest_payload(record: SelfImprovementReviewRecord) -> dict[str, object]:
    return record.model_dump(by_alias=True, exclude={"review_digest"})


def _safe_digest(value: str, field_name: str) -> str:
    raw = str(value).strip()
    if not _SHA256_RE.fullmatch(raw):
        raise ValueError(f"{field_name} must be sha256:<64 lowercase hex>")
    return raw


def _safe_ref(value: str, field_name: str, *, prefixes: tuple[str, ...]) -> str:
    raw = str(value).strip()
    if not raw or not raw.startswith(prefixes) or not _SAFE_REF_RE.fullmatch(raw):
        raise ValueError(f"{field_name} must be a safe public ref")
    if has_unsafe_marker(raw) or sanitize_public_text(raw) != raw:
        raise ValueError(f"{field_name} contains private or unsafe material")
    return raw


def _safe_public_text(value: str, field_name: str) -> str:
    raw = str(value)
    safe = sanitize_public_text(raw)
    if not safe:
        raise ValueError(f"{field_name} must not be empty")
    if safe != raw or has_unsafe_marker(safe):
        raise ValueError(f"{field_name} contains private or unsafe material")
    return safe[:400]


def _safe_reason_code(value: str, field_name: str) -> str:
    token = str(value).strip().lower().replace(" ", "_")
    if not token or not _SAFE_REASON_RE.fullmatch(token):
        raise ValueError(f"{field_name} must be a safe reason code")
    if has_unsafe_marker(token) or sanitize_public_text(token) != token:
        raise ValueError(f"{field_name} contains private or unsafe material")
    return token


def _string_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence) and not isinstance(value, bytes):
        return tuple(str(item) for item in value)
    return (str(value),)


__all__ = [
    "SelfImprovementAuthorityFlags",
    "SelfImprovementPromotionScope",
    "SelfImprovementReviewConfig",
    "SelfImprovementReviewDecision",
    "SelfImprovementReviewGate",
    "SelfImprovementReviewRecord",
    "SelfImprovementReviewRequest",
    "SelfImprovementReviewResult",
    "SelfImprovementReviewStatus",
]
