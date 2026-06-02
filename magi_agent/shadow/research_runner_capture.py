from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


RESEARCH_CAPTURE_DEFAULT_ENABLED = False

ResearchRouteIntent = Literal[
    "research",
    "new_research",
    "direct",
    "parallel_research",
    "followup_control",
]
SourceKind = Literal[
    "web_fetch",
    "web_search",
    "browser",
    "kb",
    "file",
    "external_repo",
    "external_doc",
    "subagent_result",
    "current_user_source",
    "input_fixture",
]
TrustTier = Literal["primary", "official", "secondary", "unknown"]
RequiredFieldStatus = Literal[
    "covered",
    "present",
    "satisfied",
    "partial",
    "partially_covered",
    "missing",
    "not_applicable",
]
ClaimSupportStatus = Literal["supported", "partial", "partially_supported", "unsupported", "uncertain"]
ClaimLinkSupport = Literal[
    "supports",
    "supported",
    "partial",
    "partially_supported",
    "unsupported",
    "uncertain",
]
ClaimType = Literal["fact", "synthesis", "uncertainty", "limitation"]
ContradictionStatus = Literal["handled", "resolved", "explained", "unhandled", "open"]
VerifierStatus = Literal["pass", "fail", "partial", "not_run"]
VerifierSynthesis = Literal[
    "deep",
    "rich",
    "integrated",
    "adequate",
    "shallow",
    "fallback",
    "none",
]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    validate_default=True,
    extra="forbid",
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_PUBLIC_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,180}$")
_SOURCE_ID_RE = re.compile(r"^src_[1-9][0-9]*$")
_CLAIM_ID_RE = re.compile(r"^claim_[1-9][0-9]*$")
_SHA256_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_CONTENT_HASH_RE = re.compile(r"^sha256:[A-Za-z0-9_.:-]{1,180}$")
_PRIVATE_RE = re.compile(
    r"(?:"
    r"/Users/|/home/|/workspace/|/data/bots/|/var/lib/kubelet/|"
    r"Bearer\s+|authorization|cookie|session[_-]?key|sessionid|secret|token|"
    r"password|credential|api[_-]?key|private[_-]?key|"
    r"github_pat_|gh[opusr]_|xox[a-z]-|AKIA|AIza|sk-[A-Za-z0-9_-]+|"
    r"hidden[_-]?reasoning|chain[_-]?of[_-]?thought|raw[_-]?(?:prompt|output|"
    r"source|browser|tool|transcript|log|logs|html|body|content)"
    r")",
    re.IGNORECASE,
)
_RAW_METADATA_KEYS = frozenset(
    {
        "body",
        "browserLog",
        "browserLogs",
        "browserSnapshot",
        "content",
        "cookie",
        "cookies",
        "html",
        "output",
        "prompt",
        "raw",
        "rawBody",
        "rawBrowserLog",
        "rawContent",
        "rawHtml",
        "rawOutput",
        "rawPrompt",
        "rawSource",
        "rawToolLog",
        "sessionKey",
        "sourceBody",
        "text",
        "toolLog",
        "toolLogs",
    }
)


def _ensure_safe_public_id(value: str, label: str) -> str:
    if _PUBLIC_ID_RE.fullmatch(value) is None or _PRIVATE_RE.search(value):
        raise ValueError(f"{label} must be a safe public reference")
    return value


def _ensure_safe_public_text(value: str, label: str) -> str:
    if not value.strip():
        raise ValueError(f"{label} must be non-empty")
    if _PRIVATE_RE.search(value):
        raise ValueError(f"{label} must not contain raw private data")
    return value


def _redact_snippet(value: str) -> str:
    if not value.strip():
        raise ValueError("source snippets must be non-empty")
    if _PRIVATE_RE.search(value):
        return "[redacted]"
    return value[:500]


def _safe_digest(value: str) -> str:
    if _SHA256_RE.fullmatch(value) is None:
        raise ValueError("digest values must be sha256:<64 lowercase hex>")
    return value


def _safe_content_hash(value: str) -> str:
    if _CONTENT_HASH_RE.fullmatch(value) is None or _PRIVATE_RE.search(value):
        raise ValueError("contentHash must be a safe sha256-prefixed public reference")
    return value


def _digest_text(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _validate_safe_metadata(value: object, path: str = "metadata") -> object:
    if isinstance(value, str):
        return _ensure_safe_public_text(value, path)
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int | float):
        return value
    if isinstance(value, tuple | list):
        return tuple(_validate_safe_metadata(item, f"{path}[]") for item in value)
    if isinstance(value, dict):
        result: dict[str, object] = {}
        for key, nested in value.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError(f"{path} keys must be non-empty strings")
            normalized_key = key.replace("_", "").replace("-", "").lower()
            if key in _RAW_METADATA_KEYS or normalized_key in {
                raw_key.replace("_", "").replace("-", "").lower()
                for raw_key in _RAW_METADATA_KEYS
            }:
                raise ValueError(f"{path} must not contain raw/private fields")
            _ensure_safe_public_text(key, f"{path} key")
            result[key] = _validate_safe_metadata(nested, f"{path}.{key}")
        return result
    raise ValueError(f"{path} contains unsupported metadata type")


class ResearchArtifactAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    production_authority: Literal[False] = Field(default=False, alias="productionAuthority")
    model_call: Literal[False] = Field(default=False, alias="modelCall")
    web_search_executed: Literal[False] = Field(default=False, alias="webSearchExecuted")
    browser_executed: Literal[False] = Field(default=False, alias="browserExecuted")
    live_tool_dispatched: Literal[False] = Field(default=False, alias="liveToolDispatched")
    production_storage_written: Literal[False] = Field(
        default=False,
        alias="productionStorageWritten",
    )
    evidence_block_enabled: Literal[False] = Field(default=False, alias="evidenceBlockEnabled")
    user_visible_activation: Literal[False] = Field(
        default=False,
        alias="userVisibleActivation",
    )

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
        update: dict[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = update, deep
        return type(self)()


class ClaimReasoningRecord(BaseModel):
    model_config = _MODEL_CONFIG

    status: str
    premise_source_ids: tuple[str, ...] = Field(default=(), alias="premiseSourceIds")
    inference: str
    assumptions: tuple[str, ...] = ()

    @field_validator("status", "inference")
    @classmethod
    def _safe_text(cls, value: str) -> str:
        return _ensure_safe_public_text(value, "claim reasoning")

    @field_validator("assumptions")
    @classmethod
    def _assumptions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_ensure_safe_public_text(item, "claim reasoning assumption") for item in value)

    @field_validator("premise_source_ids")
    @classmethod
    def _source_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("premiseSourceIds must not contain duplicates")
        if any(_SOURCE_ID_RE.fullmatch(source_id) is None for source_id in value):
            raise ValueError("premiseSourceIds must use src_N ids")
        return value


class SourceRecord(BaseModel):
    model_config = _MODEL_CONFIG

    source_id: str = Field(alias="sourceId")
    kind: SourceKind
    uri: str
    inspected_at: str = Field(alias="inspectedAt")
    title: str | None = None
    tool_name: str | None = Field(default=None, alias="toolName")
    trust_tier: TrustTier = Field(default="unknown", alias="trustTier")
    is_primary: bool = Field(default=False, alias="isPrimary")
    is_stale: bool = Field(default=False, alias="isStale")
    published_at: str | None = Field(default=None, alias="publishedAt")
    updated_at: str | None = Field(default=None, alias="updatedAt")
    content_hash: str = Field(alias="contentHash")
    inspection_turn_id: str | None = Field(default=None, alias="inspectionTurnId")
    snippets: tuple[str, ...] = ()
    refs: tuple[str, ...] = ()

    @field_validator("source_id")
    @classmethod
    def _source_id(cls, value: str) -> str:
        if _SOURCE_ID_RE.fullmatch(value) is None:
            raise ValueError("sourceId must use src_N ids")
        return value

    @field_validator("uri", "inspected_at")
    @classmethod
    def _required_safe_text(cls, value: str) -> str:
        return _ensure_safe_public_text(value, "source text")

    @field_validator("title", "tool_name", "published_at", "updated_at", "inspection_turn_id")
    @classmethod
    def _optional_safe_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _ensure_safe_public_text(value, "source optional text")

    @field_validator("content_hash")
    @classmethod
    def _content_hash(cls, value: str) -> str:
        return _safe_content_hash(value)

    @field_validator("snippets")
    @classmethod
    def _snippets(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) > 5:
            raise ValueError("source snippets are capped at five safe snippets")
        return tuple(_redact_snippet(snippet) for snippet in value)

    @field_validator("refs")
    @classmethod
    def _refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("source refs must not contain duplicates")
        return tuple(_ensure_safe_public_id(ref, "source ref") for ref in value)


class InspectedUrlRecord(BaseModel):
    model_config = _MODEL_CONFIG

    source_id: str = Field(alias="sourceId")
    url: str | None = None
    url_ref: str | None = Field(default=None, alias="urlRef")
    url_hash: str | None = Field(default=None, alias="urlHash")
    status: str | None = None
    status_code: int | None = Field(default=None, alias="statusCode")
    inspected_at: str | None = Field(default=None, alias="inspectedAt")

    @field_validator("source_id")
    @classmethod
    def _source_id(cls, value: str) -> str:
        if _SOURCE_ID_RE.fullmatch(value) is None:
            raise ValueError("sourceId must use src_N ids")
        return value

    @field_validator("url", "status")
    @classmethod
    def _url_status(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _ensure_safe_public_text(value, "inspected URL")

    @field_validator("url_ref")
    @classmethod
    def _url_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _ensure_safe_public_id(value, "urlRef")

    @field_validator("url_hash")
    @classmethod
    def _url_hash(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _safe_digest(value)

    @field_validator("inspected_at")
    @classmethod
    def _inspected_at(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _ensure_safe_public_text(value, "inspectedAt")

    @field_validator("status_code")
    @classmethod
    def _status_code(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool) or value < 100 or value > 599:
            raise ValueError("statusCode must be an HTTP status code")
        return value


class RequiredFieldCoverageRecord(BaseModel):
    model_config = _MODEL_CONFIG

    field_id: str = Field(alias="fieldId", validation_alias=AliasChoices("fieldId", "field"))
    label: str | None = None
    status: RequiredFieldStatus
    evidence_source_ids: tuple[str, ...] = Field(default=(), alias="evidenceSourceIds")
    notes: str | None = Field(default=None, validation_alias=AliasChoices("notes", "note"))

    @field_validator("field_id")
    @classmethod
    def _field_id(cls, value: str) -> str:
        return _ensure_safe_public_id(value, "required field")

    @field_validator("label", "notes")
    @classmethod
    def _safe_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _ensure_safe_public_text(value, "required field text")

    @field_validator("evidence_source_ids")
    @classmethod
    def _source_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("evidenceSourceIds must not contain duplicates")
        if any(_SOURCE_ID_RE.fullmatch(source_id) is None for source_id in value):
            raise ValueError("evidenceSourceIds must use src_N ids")
        return value


class ClaimRecord(BaseModel):
    model_config = _MODEL_CONFIG

    claim_id: str = Field(alias="claimId")
    text: str = Field(validation_alias=AliasChoices("text", "claim"))
    support_status: ClaimSupportStatus = Field(alias="supportStatus")
    claim_type: ClaimType = Field(default="fact", alias="claimType")
    source_ids: tuple[str, ...] = Field(default=(), alias="sourceIds")
    freshness_required: bool = Field(default=False, alias="freshnessRequired")
    confidence: float | None = None
    reasoning: ClaimReasoningRecord | None = None

    @field_validator("claim_id")
    @classmethod
    def _claim_id(cls, value: str) -> str:
        if _CLAIM_ID_RE.fullmatch(value) is None:
            raise ValueError("claimId must use claim_N ids")
        return value

    @field_validator("text")
    @classmethod
    def _claim(cls, value: str) -> str:
        return _ensure_safe_public_text(value, "claim")

    @field_validator("source_ids")
    @classmethod
    def _source_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("sourceIds must not contain duplicates")
        if any(_SOURCE_ID_RE.fullmatch(source_id) is None for source_id in value):
            raise ValueError("sourceIds must use src_N ids")
        return value

    @field_validator("confidence")
    @classmethod
    def _confidence(cls, value: float | None) -> float | None:
        if value is None:
            return None
        if value < 0 or value > 1:
            raise ValueError("claim confidence must be between 0 and 1")
        return value


class ClaimSourceLinkRecord(BaseModel):
    model_config = _MODEL_CONFIG

    claim_id: str = Field(alias="claimId")
    source_id: str = Field(alias="sourceId")
    support: ClaimLinkSupport = Field(
        default="supports",
        validation_alias=AliasChoices("support", "status"),
    )

    @field_validator("claim_id")
    @classmethod
    def _claim_id(cls, value: str) -> str:
        if _CLAIM_ID_RE.fullmatch(value) is None:
            raise ValueError("claimId must use claim_N ids")
        return value

    @field_validator("source_id")
    @classmethod
    def _source_id(cls, value: str) -> str:
        if _SOURCE_ID_RE.fullmatch(value) is None:
            raise ValueError("sourceId must use src_N ids")
        return value


class ContradictionRecord(BaseModel):
    model_config = _MODEL_CONFIG

    contradiction_id: str = Field(alias="contradictionId")
    source_ids: tuple[str, ...] = Field(default=(), alias="sourceIds")
    status: ContradictionStatus
    resolution: str | None = None

    @field_validator("contradiction_id")
    @classmethod
    def _id(cls, value: str) -> str:
        return _ensure_safe_public_id(value, "contradictionId")

    @field_validator("source_ids")
    @classmethod
    def _source_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(_SOURCE_ID_RE.fullmatch(source_id) is None for source_id in value):
            raise ValueError("sourceIds must use src_N ids")
        return value

    @field_validator("resolution")
    @classmethod
    def _resolution(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _ensure_safe_public_text(value, "contradiction resolution")


class GapRecord(BaseModel):
    model_config = _MODEL_CONFIG

    gap_id: str = Field(alias="gapId")
    category: str
    note: str

    @field_validator("gap_id", "category")
    @classmethod
    def _id_text(cls, value: str) -> str:
        return _ensure_safe_public_id(value, "gap")

    @field_validator("note")
    @classmethod
    def _note(cls, value: str) -> str:
        return _ensure_safe_public_text(value, "gap note")


class ConfidenceRecord(BaseModel):
    model_config = _MODEL_CONFIG

    level: Literal["high", "medium", "low", "unknown"] = "unknown"
    score: float | None = None
    note: str | None = None
    rationale: str | None = None

    @field_validator("score")
    @classmethod
    def _score(cls, value: float | None) -> float | None:
        if value is None:
            return None
        if value < 0 or value > 1:
            raise ValueError("confidence score must be between 0 and 1")
        return value

    @field_validator("note", "rationale")
    @classmethod
    def _note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _ensure_safe_public_text(value, "confidence note")


class EventRecord(BaseModel):
    model_config = _MODEL_CONFIG

    type: str
    tool_name: str | None = Field(default=None, alias="toolName")
    source_id: str | None = Field(default=None, alias="sourceId")
    rule_id: str | None = Field(default=None, alias="ruleId")
    verdict: str | None = None
    status: str | None = None
    detail: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)

    @field_validator("type")
    @classmethod
    def _event_type(cls, value: str) -> str:
        return _ensure_safe_public_id(value, "event type")

    @field_validator("tool_name", "rule_id", "verdict", "status", "detail")
    @classmethod
    def _optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _ensure_safe_public_text(value, "event text")

    @field_validator("source_id")
    @classmethod
    def _source_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if _SOURCE_ID_RE.fullmatch(value) is None:
            raise ValueError("sourceId must use src_N ids")
        return value

    @field_validator("metadata")
    @classmethod
    def _metadata(cls, value: dict[str, object]) -> dict[str, object]:
        return dict(_validate_safe_metadata(value, "event metadata"))


class VerifierRecord(BaseModel):
    model_config = _MODEL_CONFIG

    status: VerifierStatus = "not_run"
    synthesis: VerifierSynthesis = "none"
    blocked: bool = False
    deterministic_fallback: bool = Field(default=False, alias="deterministicFallback")
    gate_digest: str | None = Field(default=None, alias="gateDigest")
    receipt_digest: str | None = Field(default=None, alias="receiptDigest")
    eval_digest: str | None = Field(default=None, alias="evalDigest")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")

    @field_validator("gate_digest", "receipt_digest", "eval_digest")
    @classmethod
    def _digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _safe_digest(value)

    @field_validator("reason_codes")
    @classmethod
    def _reason_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("reasonCodes must not contain duplicates")
        return tuple(_ensure_safe_public_id(reason, "reason code") for reason in value)


class OutlineRecord(BaseModel):
    model_config = _MODEL_CONFIG

    id: str
    title: str
    status: str

    @field_validator("id", "title", "status")
    @classmethod
    def _safe_text(cls, value: str) -> str:
        return _ensure_safe_public_text(value, "outline text")


class ResearchArtifactRow(BaseModel):
    model_config = _MODEL_CONFIG

    schema_version: Literal["research-artifact-v1"] = Field(
        default="research-artifact-v1",
        alias="schemaVersion",
    )
    task_id: str = Field(alias="taskId")
    turn_id: str = Field(alias="turnId")
    run_id: str | None = Field(default=None, alias="runId")
    agent: str | None = None
    prompt: str | None = None
    route_intent: ResearchRouteIntent = Field(alias="routeIntent")
    final_answer: str = Field(alias="finalAnswer")
    created_at: str = Field(default="2026-05-24T12:00:00Z", alias="createdAt")
    outline: tuple[OutlineRecord, ...] = ()
    sources: tuple[SourceRecord, ...] = ()
    inspected_urls: tuple[InspectedUrlRecord, ...] = Field(default=(), alias="inspectedUrls")
    required_fields: tuple[RequiredFieldCoverageRecord, ...] = Field(
        default=(),
        alias="requiredFields",
    )
    claims: tuple[ClaimRecord, ...] = ()
    claim_source_links: tuple[ClaimSourceLinkRecord, ...] = Field(
        default=(),
        alias="claimSourceLinks",
    )
    contradictions: tuple[ContradictionRecord, ...] = ()
    gaps: tuple[GapRecord, ...] = ()
    confidence: ConfidenceRecord = Field(default_factory=ConfidenceRecord)
    limitations: tuple[str, ...] = ()
    events: tuple[EventRecord, ...] = ()
    verifier: VerifierRecord = Field(default_factory=VerifierRecord)
    authority_flags: ResearchArtifactAuthorityFlags = Field(
        default_factory=ResearchArtifactAuthorityFlags,
        alias="authorityFlags",
    )
    final_answer_digest: str | None = Field(default=None, alias="finalAnswerDigest")

    @field_validator(
        "task_id",
        "turn_id",
        "run_id",
        "agent",
        "prompt",
        "route_intent",
        "created_at",
        "final_answer",
    )
    @classmethod
    def _safe_ids(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _ensure_safe_public_text(value, "artifact id")

    @field_validator("limitations")
    @classmethod
    def _limitations(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_ensure_safe_public_text(item, "limitation") for item in value)

    @field_validator("final_answer_digest")
    @classmethod
    def _final_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _safe_digest(value)

    @model_validator(mode="after")
    def _links_resolve(self) -> "ResearchArtifactRow":
        source_ids = {source.source_id for source in self.sources}
        claim_ids = {claim.claim_id for claim in self.claims}
        for link in self.claim_source_links:
            if link.claim_id not in claim_ids or link.source_id not in source_ids:
                raise ValueError("claimSourceLinks must resolve to local claims and sources")
        if self.final_answer_digest is not None and self.final_answer_digest != _digest_text(
            self.final_answer
        ):
            raise ValueError("finalAnswerDigest must match finalAnswer")
        return self

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        values["authorityFlags"] = ResearchArtifactAuthorityFlags()
        return cls.model_validate(values)

    def model_copy(
        self,
        *,
        update: dict[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = deep
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            for key, value in update.items():
                alias = type(self).model_fields[key].alias if key in type(self).model_fields else key
                data[alias or key] = _plain_model_data(value)
        data["authorityFlags"] = ResearchArtifactAuthorityFlags().model_dump(
            by_alias=True,
            mode="python",
        )
        return type(self).model_validate(data)


class ResearchRunResultRow(BaseModel):
    model_config = _MODEL_CONFIG

    task_id: str = Field(alias="taskId")
    status: int = 200
    final_answer: str = Field(alias="finalAnswer")
    inspected_sources: tuple[SourceRecord, ...] = Field(default=(), alias="inspectedSources")
    tool_calls: tuple[dict[str, object], ...] = Field(default=(), alias="toolCalls")
    scores: dict[str, float] = Field(default_factory=dict)
    failure_categories: tuple[str, ...] = Field(default=(), alias="failureCategories")
    notes: str = "local fake PR22 capture row"

    @field_validator("task_id", "final_answer", "notes")
    @classmethod
    def _safe_text(cls, value: str) -> str:
        return _ensure_safe_public_text(value, "run result text")

    @field_validator("status")
    @classmethod
    def _status(cls, value: int) -> int:
        if isinstance(value, bool) or value < 100 or value > 599:
            raise ValueError("status must be an HTTP-like status code")
        return value

    @field_validator("tool_calls")
    @classmethod
    def _tool_calls(cls, value: tuple[dict[str, object], ...]) -> tuple[dict[str, object], ...]:
        normalized: list[dict[str, object]] = []
        for call in value:
            normalized_call = dict(_validate_safe_metadata(call, "toolCalls"))
            if "name" not in normalized_call or "count" not in normalized_call:
                raise ValueError("toolCalls entries must contain name and count")
            normalized.append(normalized_call)
        return tuple(normalized)

    @field_validator("failure_categories")
    @classmethod
    def _failure_categories(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("failureCategories must not contain duplicates")
        return tuple(_ensure_safe_public_id(category, "failure category") for category in value)

    @field_validator("scores")
    @classmethod
    def _scores(cls, value: dict[str, float]) -> dict[str, float]:
        result: dict[str, float] = {}
        for key, score in value.items():
            _ensure_safe_public_id(key, "score key")
            if score < 0 or score > 5:
                raise ValueError("scores must be between 0 and 5")
            result[key] = score
        return result


class ResearchRunDocument(BaseModel):
    model_config = _MODEL_CONFIG

    benchmark_version: int = Field(alias="benchmarkVersion")
    agent: str
    run_id: str = Field(alias="runId")
    created_at: str = Field(alias="createdAt")
    results: tuple[ResearchRunResultRow, ...]

    @field_validator("benchmark_version")
    @classmethod
    def _benchmark_version(cls, value: int) -> int:
        if isinstance(value, bool) or value < 1:
            raise ValueError("benchmarkVersion must be a positive integer")
        return value

    @field_validator("agent", "run_id", "created_at")
    @classmethod
    def _safe_text(cls, value: str) -> str:
        return _ensure_safe_public_text(value, "run document text")

    @field_validator("results")
    @classmethod
    def _unique_results(cls, value: tuple[ResearchRunResultRow, ...]) -> tuple[ResearchRunResultRow, ...]:
        task_ids = [result.task_id for result in value]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("run results must not contain duplicate taskId values")
        return value


@dataclass(frozen=True)
class LocalSampleCapture:
    run_path: Path
    artifacts_path: Path


def _plain_model_data(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(by_alias=True, mode="python", warnings=False)
    if isinstance(value, tuple | list):
        return [_plain_model_data(item) for item in value]
    if isinstance(value, dict):
        return {key: _plain_model_data(nested) for key, nested in value.items()}
    return value


def _revalidated_artifact_row(row: ResearchArtifactRow) -> ResearchArtifactRow:
    return ResearchArtifactRow.model_validate(_plain_model_data(row))


def _revalidated_run_document(run: ResearchRunDocument) -> ResearchRunDocument:
    return ResearchRunDocument.model_validate(_plain_model_data(run))


def build_research_artifact_row(
    *,
    task_id: str,
    turn_id: str,
    route_intent: ResearchRouteIntent,
    sources: tuple[SourceRecord, ...] = (),
    claims: tuple[ClaimRecord, ...] = (),
    claim_source_links: tuple[ClaimSourceLinkRecord, ...] = (),
    required_fields: tuple[RequiredFieldCoverageRecord, ...] = (),
    events: tuple[EventRecord, ...] = (),
    verifier: VerifierRecord | None = None,
    final_answer: str | None = None,
) -> ResearchArtifactRow:
    final_answer_digest = None
    if final_answer is not None:
        _ensure_safe_public_text(final_answer, "final answer")
        final_answer_digest = _digest_text(final_answer)
    return ResearchArtifactRow(
        taskId=task_id,
        turnId=turn_id,
        routeIntent=route_intent,
        finalAnswer=final_answer or "Local fake research artifact row.",
        sources=sources,
        claims=claims,
        claimSourceLinks=claim_source_links,
        requiredFields=required_fields,
        events=events,
        verifier=verifier or VerifierRecord(),
        finalAnswerDigest=final_answer_digest,
    )


def write_research_artifacts_jsonl(path: Path, rows: tuple[ResearchArtifactRow, ...]) -> Path:
    safe_rows = tuple(_revalidated_artifact_row(row) for row in rows)
    task_ids = [row.task_id for row in rows]
    if len(task_ids) != len(set(task_ids)):
        raise ValueError("research artifact JSONL must not contain duplicate taskId values")
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps(row.model_dump(by_alias=True, mode="json", exclude_none=True), sort_keys=True)
        for row in safe_rows
    ]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return path


def write_research_run_json(path: Path, run: ResearchRunDocument) -> Path:
    safe_run = _revalidated_run_document(run)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = safe_run.model_dump(by_alias=True, mode="json", exclude_none=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def build_local_sample_capture(output_dir: Path) -> LocalSampleCapture:
    source_child = SourceRecord(
        sourceId="src_1",
        kind="subagent_result",
        uri="child-agent:local-fake-pr22-scout",
        title="Local fake child research summary",
        toolName="SpawnAgent",
        inspectedAt="2026-05-24T12:00:00Z",
        contentHash=_digest_text("local fake child evidence"),
        snippets=("Child summary ref only; raw child transcript withheld.",),
        refs=("child:local-fake-pr22-scout", "evidence:local-fake-pr22-scout"),
    )
    missing_claim = ClaimRecord(
        claimId="claim_1",
        claim="Unsupported sample claim retained as an unsupported claim for evaluation.",
        supportStatus="unsupported",
        sourceIds=(),
    )
    child_claim = ClaimRecord(
        claimId="claim_1",
        claim="Parallel child evidence exists but is intentionally unused by final claims.",
        supportStatus="unsupported",
        sourceIds=(),
    )
    followup_claim = ClaimRecord(
        claimId="claim_1",
        claim="Follow-up control should not require a fresh research proof gate.",
        supportStatus="uncertain",
        claimType="limitation",
        sourceIds=(),
    )
    rows = (
        build_research_artifact_row(
            task_id="current-public-facts",
            turn_id="turn_pr22_missing",
            route_intent="research",
            claims=(missing_claim,),
            required_fields=(
                RequiredFieldCoverageRecord(field="currentFact", status="missing"),
            ),
            events=(
                EventRecord(
                    type="missing_source_evidence",
                    detail="No inspected sources are available for this local fake row.",
                ),
            ),
            verifier=VerifierRecord(
                status="fail",
                synthesis="none",
                deterministicFallback=True,
                evalDigest=_digest_text("missing source evidence"),
                reasonCodes=("no_sources_inspected",),
            ),
            final_answer="Local fake sample leaves the current fact unsupported.",
        ),
        build_research_artifact_row(
            task_id="parallel-long-running-research",
            turn_id="turn_pr22_child_unused",
            route_intent="parallel_research",
            sources=(source_child,),
            claims=(child_claim,),
            events=(
                EventRecord(
                    type="child_evidence_captured",
                    toolName="SpawnAgent",
                    sourceId="src_1",
                    detail="Child evidence is present without a valid claim-source link.",
                ),
            ),
            verifier=VerifierRecord(
                status="partial",
                synthesis="shallow",
                evalDigest=_digest_text("unused child evidence"),
                reasonCodes=("child_evidence_unused",),
            ),
            final_answer="Local fake sample exposes unused child evidence.",
        ),
        build_research_artifact_row(
            task_id="research-followup-option-selection",
            turn_id="turn_pr22_gate_scope",
            route_intent="followup_control",
            claims=(followup_claim,),
            events=(
                EventRecord(
                    type="gate_scope_overreach",
                    detail="CLAIM_CITATION gate retried a follow-up control turn.",
                    metadata={"gateCode": "CLAIM_CITATION"},
                ),
            ),
            verifier=VerifierRecord(
                status="fail",
                synthesis="none",
                blocked=True,
                gateDigest=_digest_text("CLAIM_CITATION followup overreach"),
                reasonCodes=("gate_scope_overreach",),
            ),
            final_answer="Local fake sample records follow-up gate overreach.",
        ),
    )
    run = ResearchRunDocument(
        benchmarkVersion=1,
        agent="openmagi-python-adk-local-fake",
        runId="python-adk-pr22-local-sample",
        createdAt="2026-05-24T12:00:00Z",
        results=tuple(
            ResearchRunResultRow(
                taskId=row.task_id,
                finalAnswer=f"Local fake PR22 capture sample for {row.task_id}.",
                inspectedSources=row.sources,
                toolCalls=({"name": "SpawnAgent", "count": 1},)
                if row.task_id == "parallel-long-running-research"
                else (),
            )
            for row in rows
        ),
    )

    run_path = write_research_run_json(output_dir / "python-adk-research-run.json", run)
    artifacts_path = write_research_artifacts_jsonl(
        output_dir / "python-adk-research-artifacts.jsonl",
        rows,
    )
    return LocalSampleCapture(run_path=run_path, artifacts_path=artifacts_path)
