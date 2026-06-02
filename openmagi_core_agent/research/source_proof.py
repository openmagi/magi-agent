from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import datetime
from hashlib import sha256
from typing import Any, Literal, Self
from weakref import finalize

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    field_serializer,
    field_validator,
    model_validator,
)

from openmagi_core_agent.evidence.runtime_issuance import (
    RuntimeIssueAuthority,
    require_runtime_issue_authority,
)


ResearchSourceKind = Literal[
    "web_search",
    "web_fetch",
    "browser",
    "kb",
    "file",
    "external_doc",
    "external_repo",
]
ResearchSourceReceiptKind = Literal[
    "opened_snapshot",
    "local_document_read",
    "discovered_source",
]
ResearchSourceRedactionStatus = Literal["redacted", "metadata_only", "raw"]
ResearchSourceProofStatus = Literal["allowed", "denied"]
ResearchSourceFreshnessVerdict = Literal["current", "stale", "not_checked"]
ResearchSourceProofReason = Literal[
    "source_match",
    "missing_source",
    "unopened_source",
    "source_mismatch",
    "stale_source",
    "source_ref_collision",
    "redaction_missing",
]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_SOURCE_REF_RE = re.compile(r"^src_[1-9][0-9]*$")
_PUBLIC_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,180}$")
_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_SECRET_TEXT_RE = re.compile(
    r"(?:Bearer\s+[A-Za-z0-9._~+/=-]{8,}|gh[opusr]_[A-Za-z0-9_]{8,}|"
    r"github_pat_[A-Za-z0-9_]{8,}|AKIA[0-9A-Z]{12,}|ASIA[0-9A-Z]{12,}|"
    r"AIza[0-9A-Za-z_-]{12,}|xox[baprs]-[A-Za-z0-9-]{8,}|"
    r"sk-(?:live|test)?[-_A-Za-z0-9]{8,}|\b\d{5,}:[A-Za-z0-9_-]{8,}\b|"
    r"[A-Z0-9_]*(?:SECRET|TOKEN|KEY|PASSWORD|COOKIE)[A-Z0-9_]*\s*[:=]\s*"
    r"[^,\s}{\n]{4,})",
    re.IGNORECASE,
)
_PRIVATE_PATH_RE = re.compile(
    r"(?:/Users/[^,\s\"']+|/home/[^,\s\"']+|/root/[^,\s\"']+|"
    r"/workspace/[^,\s\"']+|/data/bots/[^,\s\"']+|"
    r"/var/lib/kubelet/[^,\s\"']+|pvc-[A-Za-z0-9-]+)",
    re.IGNORECASE,
)
_UNSAFE_TEXT_RE = re.compile(
    r"https?://|file://|raw[_ -]?(?:source|transcript|tool|prompt|output|result|log)|"
    r"source[_ -]?(?:body|content|html|text)|hidden[_ -]?reasoning|"
    r"chain[_ -]?of[_ -]?thought|authorization|cookie|set-cookie|"
    r"api[_ -]?key|secret|token|model[_ -]?summary|model[_ -]?generated[_ -]?summary",
    re.IGNORECASE,
)
_FORBIDDEN_RAW_KEY_TOKENS = frozenset(
    {
        "authorization",
        "cookie",
        "html",
        "modelgeneratedsummary",
        "modelsummary",
        "privatepath",
        "prompt",
        "rawhtml",
        "rawoutput",
        "rawprompt",
        "rawsource",
        "rawsourcetext",
        "rawtool",
        "secret",
        "sourcebody",
        "sourcecontent",
        "sourcehtml",
        "sourcetext",
        "token",
        "tooloutput",
        "url",
        "uri",
    }
)
_ADK_USAGE_NOTES = (
    "Metadata only; no ADK Runner, ArtifactService, or FunctionTool is attached."
)
_RUNTIME_ISSUED_SOURCE_OBJECT_IDS: set[int] = set()
_RUNTIME_ISSUED_SOURCE_FINGERPRINTS: dict[int, object] = {}
_RUNTIME_ISSUED_SOURCE_FINALIZERS: dict[int, object] = {}
_VERIFIER_ISSUED_SOURCE_VERDICT_OBJECT_IDS: set[int] = set()
_VERIFIER_ISSUED_SOURCE_VERDICT_FINGERPRINTS: dict[int, object] = {}
_VERIFIER_ISSUED_SOURCE_VERDICT_FINALIZERS: dict[int, object] = {}


class _ResearchSourceModel(BaseModel):
    model_config = _MODEL_CONFIG

    @classmethod
    def model_construct(cls, *args: object, **kwargs: object) -> Self:
        raise TypeError("model_construct is disabled for research source proof contracts")

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            data.update(dict(update))
        return type(self).model_validate(data)


class ResearchSourceExecutionPosture(_ResearchSourceModel):
    default_off: Literal[True] = Field(default=True, alias="defaultOff")
    local_only: Literal[True] = Field(default=True, alias="localOnly")
    fake_provider_only: Literal[True] = Field(default=True, alias="fakeProviderOnly")
    live_execution_allowed: Literal[False] = Field(default=False, alias="liveExecutionAllowed")
    provider_calls_allowed: Literal[False] = Field(default=False, alias="providerCallsAllowed")
    browser_execution_allowed: Literal[False] = Field(
        default=False,
        alias="browserExecutionAllowed",
    )
    tool_execution_allowed: Literal[False] = Field(default=False, alias="toolExecutionAllowed")
    channel_delivery_allowed: Literal[False] = Field(default=False, alias="channelDeliveryAllowed")
    user_visible_python_activation: Literal[False] = Field(
        default=False,
        alias="userVisiblePythonActivation",
    )

    @field_serializer(
        "default_off",
        "local_only",
        "fake_provider_only",
        "live_execution_allowed",
        "provider_calls_allowed",
        "browser_execution_allowed",
        "tool_execution_allowed",
        "channel_delivery_allowed",
        "user_visible_python_activation",
    )
    def _serialize_posture(self, value: object) -> bool:
        return bool(value)


class ResearchSourceOpenReceiptRef(_ResearchSourceModel):
    _issued_by_runtime_boundary: bool = PrivateAttr(default=False)

    source_ref_id: str = Field(alias="sourceRefId")
    source_kind: ResearchSourceKind = Field(alias="sourceKind")
    receipt_kind: ResearchSourceReceiptKind = Field(alias="receiptKind")
    runtime_issued: Literal[True] = Field(alias="runtimeIssued")
    receipt_authority: Literal["openmagi_runtime_boundary"] = Field(alias="receiptAuthority")
    tool_host_mediated: Literal[True] = Field(alias="toolHostMediated")
    opened: bool
    content_digest: str = Field(alias="contentDigest")
    inspected_at: str = Field(alias="inspectedAt")
    span_refs: tuple[str, ...] = Field(alias="spanRefs")
    redaction_status: ResearchSourceRedactionStatus = Field(alias="redactionStatus")
    digest: str
    public_label: str | None = Field(default=None, alias="publicLabel")

    @classmethod
    def issue_runtime_source_ref(
        cls,
        *,
        runtime_authority: RuntimeIssueAuthority | None = None,
        source_ref_id: str,
        source_kind: ResearchSourceKind,
        receipt_kind: ResearchSourceReceiptKind,
        opened: bool,
        content_digest: str,
        inspected_at: str,
        span_refs: tuple[str, ...],
        redaction_status: ResearchSourceRedactionStatus,
        public_label: str | None = None,
    ) -> Self:
        require_runtime_issue_authority(
            runtime_authority,
            scope="research_source_proof",
        )
        source_ref = cls(
            sourceRefId=source_ref_id,
            sourceKind=source_kind,
            receiptKind=receipt_kind,
            runtimeIssued=True,
            receiptAuthority="openmagi_runtime_boundary",
            toolHostMediated=True,
            opened=opened,
            contentDigest=content_digest,
            inspectedAt=inspected_at,
            spanRefs=span_refs,
            redactionStatus=redaction_status,
            digest=_source_receipt_digest(
                source_ref_id=source_ref_id,
                source_kind=source_kind,
                receipt_kind=receipt_kind,
                opened=opened,
                content_digest=content_digest,
                inspected_at=inspected_at,
                span_refs=span_refs,
                redaction_status=redaction_status,
            ),
            publicLabel=public_label,
        )
        _mark_runtime_source_ref_issued(source_ref)
        return source_ref

    @property
    def is_runtime_boundary_issued(self) -> bool:
        return (
            bool(self.__pydantic_private__.get("_issued_by_runtime_boundary"))
            and id(self) in _RUNTIME_ISSUED_SOURCE_OBJECT_IDS
        )

    @model_validator(mode="before")
    @classmethod
    def _validate_raw_source_ref(cls, value: object) -> object:
        if isinstance(value, Mapping):
            _reject_unsafe_raw_value(value)
        return value

    @field_validator("source_ref_id")
    @classmethod
    def _validate_source_ref_id(cls, value: str) -> str:
        return _source_ref(value, "sourceRefId")

    @field_validator("opened", mode="before")
    @classmethod
    def _validate_opened_bool(cls, value: object) -> object:
        if isinstance(value, bool):
            return value
        raise ValueError("opened must be a strict boolean")

    @field_validator("content_digest", "digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        if not _DIGEST_RE.fullmatch(value):
            raise ValueError("digest fields must be sha256 hex digests")
        return value

    @field_validator("inspected_at")
    @classmethod
    def _validate_inspected_at(cls, value: str) -> str:
        clean = value.strip()
        _parse_utcish_datetime(clean, "inspectedAt")
        return clean

    @field_validator("span_refs")
    @classmethod
    def _validate_span_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("spanRefs must be non-empty")
        if len(set(value)) != len(value):
            raise ValueError("spanRefs must not contain duplicates")
        return tuple(_public_ref(item, "spanRef") for item in value)

    @field_validator("public_label")
    @classmethod
    def _validate_public_label(cls, value: str | None) -> str | None:
        if value is None:
            return None
        clean = value.strip()
        if not clean:
            return None
        _reject_unsafe_public_text(clean, "publicLabel")
        if len(clean) > 160:
            raise ValueError("publicLabel must be at most 160 characters")
        return clean

    @model_validator(mode="after")
    def _validate_source_ref_shape(self) -> Self:
        if self.redaction_status == "raw":
            raise ValueError("source proof cannot accept raw redaction status")
        expected = _source_receipt_digest(
            source_ref_id=self.source_ref_id,
            source_kind=self.source_kind,
            receipt_kind=self.receipt_kind,
            opened=self.opened,
            content_digest=self.content_digest,
            inspected_at=self.inspected_at,
            span_refs=self.span_refs,
            redaction_status=self.redaction_status,
        )
        if self.digest != expected:
            raise ValueError("digest must be bound to source receipt metadata")
        return self

    def public_projection(self) -> dict[str, object]:
        return {
            "sourceRefId": self.source_ref_id,
            "sourceKind": self.source_kind,
            "receiptKind": self.receipt_kind,
            "runtimeIssued": self.runtime_issued,
            "receiptAuthority": self.receipt_authority,
            "toolHostMediated": self.tool_host_mediated,
            "opened": self.opened,
            "contentDigest": self.content_digest,
            "inspectedAt": self.inspected_at,
            "spanRefs": self.span_refs,
            "redactionStatus": self.redaction_status,
            "digest": self.digest,
        }


class ResearchSourceProofRequirement(_ResearchSourceModel):
    source_ref_id: str = Field(alias="sourceRefId")
    allowed_source_kinds: tuple[ResearchSourceKind, ...] = Field(alias="allowedSourceKinds")
    required_receipt_kinds: tuple[ResearchSourceReceiptKind, ...] = Field(
        alias="requiredReceiptKinds",
    )
    required_span_refs: tuple[str, ...] = Field(alias="requiredSpanRefs")
    allowed_redaction_statuses: tuple[Literal["redacted", "metadata_only"], ...] = Field(
        default=("redacted", "metadata_only"),
        alias="allowedRedactionStatuses",
    )
    not_before: str | None = Field(default=None, alias="notBefore")
    not_after: str | None = Field(default=None, alias="notAfter")

    @model_validator(mode="before")
    @classmethod
    def _validate_raw_requirement(cls, value: object) -> object:
        if isinstance(value, Mapping):
            _reject_unsafe_raw_value(value)
        return value

    @field_validator("source_ref_id")
    @classmethod
    def _validate_source_ref_id(cls, value: str) -> str:
        return _source_ref(value, "sourceRefId")

    @field_validator("allowed_source_kinds", "required_receipt_kinds")
    @classmethod
    def _validate_non_empty_unique_tuple(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("source proof requirement tuples must be non-empty")
        if len(set(value)) != len(value):
            raise ValueError("source proof requirement tuples must not contain duplicates")
        return value

    @field_validator("required_span_refs")
    @classmethod
    def _validate_required_span_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("requiredSpanRefs must be non-empty")
        if len(set(value)) != len(value):
            raise ValueError("requiredSpanRefs must not contain duplicates")
        return tuple(_public_ref(item, "requiredSpanRef") for item in value)

    @field_validator("allowed_redaction_statuses")
    @classmethod
    def _validate_allowed_redaction_statuses(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        if not value:
            raise ValueError("allowedRedactionStatuses must be non-empty")
        if len(set(value)) != len(value):
            raise ValueError("allowedRedactionStatuses must not contain duplicates")
        return value

    @field_validator("not_before", "not_after")
    @classmethod
    def _validate_optional_time(cls, value: str | None) -> str | None:
        if value is None:
            return None
        clean = value.strip()
        _parse_utcish_datetime(clean, "freshness bound")
        return clean

    @model_validator(mode="after")
    def _validate_freshness_window(self) -> Self:
        if (self.not_before is None) != (self.not_after is None):
            raise ValueError("freshness policy requires both notBefore and notAfter")
        if self.not_before is not None and self.not_after is not None:
            before = _parse_utcish_datetime(self.not_before, "notBefore")
            after = _parse_utcish_datetime(self.not_after, "notAfter")
            if before > after:
                raise ValueError("notBefore must be before or equal to notAfter")
        return self

    def public_projection(self) -> dict[str, object]:
        return {
            "sourceRefId": self.source_ref_id,
            "allowedSourceKinds": self.allowed_source_kinds,
            "requiredReceiptKinds": self.required_receipt_kinds,
            "requiredSpanRefs": self.required_span_refs,
            "allowedRedactionStatuses": self.allowed_redaction_statuses,
            "notBefore": self.not_before,
            "notAfter": self.not_after,
        }


class ResearchSourceProofVerdict(_ResearchSourceModel):
    _issued_by_source_verifier: bool = PrivateAttr(default=False)

    source_ref_id: str = Field(alias="sourceRefId")
    source_kind: ResearchSourceKind | None = Field(default=None, alias="sourceKind")
    verdict: ResearchSourceProofStatus
    reason_code: ResearchSourceProofReason = Field(alias="reasonCode")
    freshness_verdict: ResearchSourceFreshnessVerdict = Field(alias="freshnessVerdict")
    matched_source_refs: tuple[str, ...] = Field(default=(), alias="matchedSourceRefs")
    content_digest: str | None = Field(default=None, alias="contentDigest")
    span_refs: tuple[str, ...] = Field(default=(), alias="spanRefs")
    projected_text: str = Field(alias="projectedText")
    requirement: ResearchSourceProofRequirement
    execution_posture: ResearchSourceExecutionPosture = Field(
        default_factory=ResearchSourceExecutionPosture,
        alias="executionPosture",
    )
    adk_usage_notes: str = Field(default=_ADK_USAGE_NOTES, alias="adkUsageNotes")

    @property
    def is_source_verifier_issued(self) -> bool:
        return (
            bool(self.__pydantic_private__.get("_issued_by_source_verifier"))
            and id(self) in _VERIFIER_ISSUED_SOURCE_VERDICT_OBJECT_IDS
        )

    @field_validator("source_ref_id")
    @classmethod
    def _validate_source_ref_id(cls, value: str) -> str:
        return _source_ref(value, "sourceRefId")

    @field_validator("matched_source_refs")
    @classmethod
    def _validate_matched_source_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("matchedSourceRefs must not contain duplicates")
        return tuple(_source_ref(item, "matchedSourceRef") for item in value)

    @field_validator("content_digest")
    @classmethod
    def _validate_optional_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _DIGEST_RE.fullmatch(value):
            raise ValueError("contentDigest must be a sha256 hex digest")
        return value

    @field_validator("span_refs")
    @classmethod
    def _validate_span_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("spanRefs must not contain duplicates")
        return tuple(_public_ref(item, "spanRef") for item in value)

    @field_validator("projected_text", "adk_usage_notes")
    @classmethod
    def _validate_public_text(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("public text must be non-empty")
        _reject_unsafe_public_text(clean, "public text")
        if len(clean) > 240:
            raise ValueError("public text must be at most 240 characters")
        return clean

    @model_validator(mode="after")
    def _validate_verdict_shape(self) -> Self:
        if self.requirement.source_ref_id != self.source_ref_id:
            raise ValueError("requirement sourceRefId must match verdict sourceRefId")
        if self.verdict == "allowed":
            if self.reason_code != "source_match":
                raise ValueError("allowed source verdicts require reasonCode=source_match")
            if self.matched_source_refs != (self.source_ref_id,):
                raise ValueError("allowed source verdicts require matched source ref")
            if self.content_digest is None or not self.span_refs:
                raise ValueError("allowed source verdicts require digest and spans")
            expected_text = f"source verified: {self.source_ref_id}"
        else:
            if self.reason_code == "source_match":
                raise ValueError("denied source verdicts must not use source_match")
            if self.matched_source_refs or self.content_digest is not None or self.span_refs:
                raise ValueError("denied source verdicts must not expose source details")
            expected_text = f"source not verified: {self.source_ref_id}"
        if self.projected_text != expected_text:
            raise ValueError("projectedText must match deterministic source proof wording")
        return self

    def public_projection(self) -> dict[str, object]:
        return {
            "sourceRefId": self.source_ref_id,
            "sourceKind": self.source_kind,
            "verdict": self.verdict,
            "reasonCode": self.reason_code,
            "freshnessVerdict": self.freshness_verdict,
            "matchedSourceRefs": self.matched_source_refs,
            "contentDigest": self.content_digest,
            "spanRefs": self.span_refs,
            "projectedText": self.projected_text,
            "requirement": self.requirement.public_projection(),
            "executionPosture": self.execution_posture.model_dump(
                by_alias=True,
                mode="python",
                warnings=False,
            ),
            "adkUsageNotes": self.adk_usage_notes,
        }


def verify_research_source_proof(
    requirements: Iterable[ResearchSourceProofRequirement | Mapping[str, object]],
    source_refs: Iterable[ResearchSourceOpenReceiptRef],
) -> tuple[ResearchSourceProofVerdict, ...]:
    validated_requirements = tuple(_validate_requirement(item) for item in requirements)
    validated_source_refs = tuple(_validate_source_ref_object(item) for item in source_refs)
    requirement_ids = [item.source_ref_id for item in validated_requirements]
    if len(set(requirement_ids)) != len(requirement_ids):
        raise ValueError("source proof requirements must not contain duplicate sourceRefId")

    source_refs_by_id: dict[str, tuple[ResearchSourceOpenReceiptRef, ...]] = {}
    for source_ref in validated_source_refs:
        source_refs_by_id[source_ref.source_ref_id] = (
            *source_refs_by_id.get(source_ref.source_ref_id, ()),
            source_ref,
        )

    verdicts: list[ResearchSourceProofVerdict] = []
    for requirement in validated_requirements:
        candidates = source_refs_by_id.get(requirement.source_ref_id, ())
        if not candidates:
            verdicts.append(_denied_verdict(requirement, "missing_source"))
            continue
        if _has_source_ref_collision(candidates):
            verdicts.append(_denied_verdict(requirement, "source_ref_collision"))
            continue
        source_ref = candidates[0]
        reason, freshness = _source_ref_failure_reason(source_ref, requirement)
        if reason is not None:
            verdicts.append(_denied_verdict(requirement, reason, freshness, source_ref))
            continue
        verdicts.append(
            _issued_verdict(
                sourceRefId=requirement.source_ref_id,
                sourceKind=source_ref.source_kind,
                verdict="allowed",
                reasonCode="source_match",
                freshnessVerdict=freshness,
                matchedSourceRefs=(source_ref.source_ref_id,),
                contentDigest=source_ref.content_digest,
                spanRefs=source_ref.span_refs,
                projectedText=f"source verified: {requirement.source_ref_id}",
                requirement=requirement,
            )
        )
    return tuple(verdicts)


def project_research_source_proof_verdicts(
    verdicts: Iterable[ResearchSourceProofVerdict],
) -> tuple[dict[str, object], ...]:
    return tuple(_validate_verdict(item).public_projection() for item in verdicts)


def _source_ref_failure_reason(
    source_ref: ResearchSourceOpenReceiptRef,
    requirement: ResearchSourceProofRequirement,
) -> tuple[ResearchSourceProofReason | None, ResearchSourceFreshnessVerdict]:
    if not source_ref.opened or source_ref.receipt_kind == "discovered_source":
        return "unopened_source", "not_checked"
    if source_ref.receipt_kind not in requirement.required_receipt_kinds:
        return "unopened_source", "not_checked"
    if source_ref.source_kind not in requirement.allowed_source_kinds:
        return "source_mismatch", "not_checked"
    if source_ref.redaction_status not in requirement.allowed_redaction_statuses:
        return "redaction_missing", "not_checked"
    if not set(requirement.required_span_refs).issubset(set(source_ref.span_refs)):
        return "source_mismatch", "not_checked"
    freshness = _freshness_verdict(source_ref, requirement)
    if freshness == "stale":
        return "stale_source", freshness
    return None, freshness


def _freshness_verdict(
    source_ref: ResearchSourceOpenReceiptRef,
    requirement: ResearchSourceProofRequirement,
) -> ResearchSourceFreshnessVerdict:
    if requirement.not_before is None or requirement.not_after is None:
        return "not_checked"
    inspected = _parse_utcish_datetime(source_ref.inspected_at, "inspectedAt")
    not_before = _parse_utcish_datetime(requirement.not_before, "notBefore")
    not_after = _parse_utcish_datetime(requirement.not_after, "notAfter")
    if inspected < not_before or inspected > not_after:
        return "stale"
    return "current"


def _denied_verdict(
    requirement: ResearchSourceProofRequirement,
    reason_code: ResearchSourceProofReason,
    freshness_verdict: ResearchSourceFreshnessVerdict = "not_checked",
    source_ref: ResearchSourceOpenReceiptRef | None = None,
) -> ResearchSourceProofVerdict:
    return _issued_verdict(
        sourceRefId=requirement.source_ref_id,
        sourceKind=source_ref.source_kind if source_ref is not None else None,
        verdict="denied",
        reasonCode=reason_code,
        freshnessVerdict=freshness_verdict,
        matchedSourceRefs=(),
        contentDigest=None,
        spanRefs=(),
        projectedText=f"source not verified: {requirement.source_ref_id}",
        requirement=requirement,
    )


def _issued_verdict(**kwargs: object) -> ResearchSourceProofVerdict:
    verdict = ResearchSourceProofVerdict(**kwargs)
    _mark_source_verdict_issued(verdict)
    return verdict


def _mark_runtime_source_ref_issued(source_ref: ResearchSourceOpenReceiptRef) -> None:
    object_id = id(source_ref)
    source_ref.__pydantic_private__["_issued_by_runtime_boundary"] = True
    _RUNTIME_ISSUED_SOURCE_OBJECT_IDS.add(object_id)
    _RUNTIME_ISSUED_SOURCE_FINGERPRINTS[object_id] = _model_fingerprint(source_ref)
    _RUNTIME_ISSUED_SOURCE_FINALIZERS[object_id] = finalize(
        source_ref,
        _discard_runtime_source_ref_object_id,
        object_id,
    )


def _discard_runtime_source_ref_object_id(object_id: int) -> None:
    _RUNTIME_ISSUED_SOURCE_OBJECT_IDS.discard(object_id)
    _RUNTIME_ISSUED_SOURCE_FINGERPRINTS.pop(object_id, None)
    _RUNTIME_ISSUED_SOURCE_FINALIZERS.pop(object_id, None)


def _mark_source_verdict_issued(verdict: ResearchSourceProofVerdict) -> None:
    object_id = id(verdict)
    verdict.__pydantic_private__["_issued_by_source_verifier"] = True
    _VERIFIER_ISSUED_SOURCE_VERDICT_OBJECT_IDS.add(object_id)
    _VERIFIER_ISSUED_SOURCE_VERDICT_FINGERPRINTS[object_id] = _model_fingerprint(verdict)
    _VERIFIER_ISSUED_SOURCE_VERDICT_FINALIZERS[object_id] = finalize(
        verdict,
        _discard_source_verdict_object_id,
        object_id,
    )


def _discard_source_verdict_object_id(object_id: int) -> None:
    _VERIFIER_ISSUED_SOURCE_VERDICT_OBJECT_IDS.discard(object_id)
    _VERIFIER_ISSUED_SOURCE_VERDICT_FINGERPRINTS.pop(object_id, None)
    _VERIFIER_ISSUED_SOURCE_VERDICT_FINALIZERS.pop(object_id, None)


def _validate_requirement(
    value: ResearchSourceProofRequirement | Mapping[str, object],
) -> ResearchSourceProofRequirement:
    if isinstance(value, ResearchSourceProofRequirement):
        return ResearchSourceProofRequirement.model_validate(
            value.model_dump(by_alias=True, mode="python", warnings=False)
        )
    return ResearchSourceProofRequirement.model_validate(value)


def _validate_source_ref_object(
    value: ResearchSourceOpenReceiptRef,
) -> ResearchSourceOpenReceiptRef:
    if not isinstance(value, ResearchSourceOpenReceiptRef):
        raise TypeError("source proof inputs must be runtime-issued source ref objects")
    if not value.is_runtime_boundary_issued:
        raise ValueError("source proof refs must be issued by the runtime boundary")
    expected = _RUNTIME_ISSUED_SOURCE_FINGERPRINTS.get(id(value))
    if expected != _model_fingerprint(value):
        raise ValueError("source proof refs were modified after runtime issuance")
    ResearchSourceOpenReceiptRef.model_validate(
        value.model_dump(by_alias=True, mode="python", warnings=False)
    )
    return value


def _validate_verdict(value: ResearchSourceProofVerdict) -> ResearchSourceProofVerdict:
    if not isinstance(value, ResearchSourceProofVerdict):
        raise TypeError("source proof projections require verifier-issued source verdict objects")
    if not value.is_source_verifier_issued:
        raise ValueError("source proof verdicts must be issued by the verifier")
    expected = _VERIFIER_ISSUED_SOURCE_VERDICT_FINGERPRINTS.get(id(value))
    if expected != _model_fingerprint(value):
        raise ValueError("source proof verdict was modified after verifier issuance")
    ResearchSourceProofVerdict.model_validate(
        value.model_dump(by_alias=True, mode="python", warnings=False)
    )
    return value


def _has_source_ref_collision(source_refs: tuple[ResearchSourceOpenReceiptRef, ...]) -> bool:
    fingerprints = {
        (
            item.source_kind,
            item.receipt_kind,
            item.opened,
            item.content_digest,
            item.inspected_at,
            item.span_refs,
            item.redaction_status,
            item.digest,
        )
        for item in source_refs
    }
    return len(fingerprints) > 1


def _model_fingerprint(model: BaseModel) -> object:
    return _freeze_for_fingerprint(
        model.model_dump(by_alias=True, mode="python", warnings=False)
    )


def _freeze_for_fingerprint(value: object) -> object:
    if isinstance(value, Mapping):
        return tuple(
            (str(key), _freeze_for_fingerprint(item))
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        )
    if isinstance(value, Iterable) and not isinstance(value, str | bytes | bytearray):
        return tuple(_freeze_for_fingerprint(item) for item in value)
    return value


def _source_ref(value: str, field_name: str) -> str:
    clean = value.strip()
    if not clean:
        raise ValueError(f"{field_name} must be non-empty")
    _reject_unsafe_public_text(clean, field_name)
    if _SOURCE_REF_RE.fullmatch(clean) is None:
        raise ValueError(f"{field_name} must use stable src_N metadata refs")
    return clean


def _public_ref(value: str, field_name: str) -> str:
    clean = value.strip()
    if not clean:
        raise ValueError(f"{field_name} must be non-empty")
    _reject_unsafe_public_text(clean, field_name)
    if _PUBLIC_REF_RE.fullmatch(clean) is None:
        raise ValueError(f"{field_name} must be a digest-safe public id")
    return clean


def _source_receipt_digest(
    *,
    source_ref_id: str,
    source_kind: str,
    receipt_kind: str,
    opened: bool,
    content_digest: str,
    inspected_at: str,
    span_refs: tuple[str, ...],
    redaction_status: str,
) -> str:
    material = "\n".join(
        (
            "openmagi-research-source-proof-v1",
            source_ref_id.strip(),
            source_kind.strip(),
            receipt_kind.strip(),
            str(opened),
            content_digest.strip(),
            inspected_at.strip(),
            "\n".join(span_refs),
            redaction_status.strip(),
            "openmagi_runtime_boundary",
            "toolhost_mediated",
        )
    )
    return "sha256:" + sha256(material.encode("utf-8")).hexdigest()


def _parse_utcish_datetime(value: str, field_name: str) -> datetime:
    clean = value.strip()
    if not clean:
        raise ValueError(f"{field_name} must be non-empty")
    try:
        parsed = datetime.fromisoformat(clean.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone offset")
    return parsed


def _reject_unsafe_raw_value(value: Mapping[object, object]) -> None:
    for key, item in value.items():
        normalized_key = re.sub(r"[^a-z0-9]", "", str(key).casefold())
        if normalized_key in _FORBIDDEN_RAW_KEY_TOKENS:
            raise ValueError("research source proof inputs must be metadata-only")
        _reject_unsafe_nested_value(item)


def _reject_unsafe_nested_value(value: object) -> None:
    if isinstance(value, str):
        _reject_unsafe_public_text(value, "value")
        return
    if isinstance(value, Mapping):
        _reject_unsafe_raw_value(value)
        return
    if isinstance(value, Iterable) and not isinstance(value, bytes | bytearray):
        for item in value:
            _reject_unsafe_nested_value(item)


def _reject_unsafe_public_text(value: str, field_name: str) -> None:
    if _PRIVATE_PATH_RE.search(value):
        raise ValueError(f"{field_name} must not contain private paths")
    if _SECRET_TEXT_RE.search(value) or _UNSAFE_TEXT_RE.search(value):
        raise ValueError(
            f"{field_name} must not contain URL-only, raw, private, auth, token, or secret data"
        )


__all__ = [
    "ResearchSourceExecutionPosture",
    "ResearchSourceFreshnessVerdict",
    "ResearchSourceKind",
    "ResearchSourceOpenReceiptRef",
    "ResearchSourceProofReason",
    "ResearchSourceProofRequirement",
    "ResearchSourceProofStatus",
    "ResearchSourceProofVerdict",
    "ResearchSourceReceiptKind",
    "ResearchSourceRedactionStatus",
    "project_research_source_proof_verdicts",
    "verify_research_source_proof",
]
