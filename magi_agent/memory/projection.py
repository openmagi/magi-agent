from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from magi_agent.ops.authority import FalseOnlyAuthorityModel
from magi_agent.transport.tool_preview import sanitize_tool_preview
from magi_agent.runtime.events import NormalizedEvent

from .contracts import MemoryRecord, RecallResult
from .namespaces import MemoryNamespacePolicy, admit_recall_result_to_namespace
from .policy import MemoryPolicy


LongTermMemoryPolicy = Literal["normal", "background_only", "disabled"]
ClassifierMemoryPolicy = Literal["normal", "background_only", "disabled"]
MemoryContinuity = Literal["active", "related", "background"]
MemoryRecallSource = str
ChildScope = Literal["none", "sanitized_envelope"]

_MODEL_CONFIG = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

_CURRENT_AUTHORITY_SOURCE_RE = re.compile(
    r"<current-turn-source\b[^>]*\bkind\s*=\s*['\"]([^'\"]+)['\"][^>]*>",
    re.IGNORECASE,
)
_CONTINUATION_CUE_RE = re.compile(
    r"\b(?:continue|resume|again|earlier|previous|that issue|that topic|the one we discussed)\b"
    r"|(?:아까|전에|이어서|다시|그거|그 문제|그 선택|그 주제|저번|이전)",
    re.IGNORECASE,
)
_DECISION_REQUEST_RE = re.compile(
    r"[?？]\s*$|(?:어떻게\s*할까요|할까요|정할까요|고를까요|선택(?:할|해야|해)|"
    r"결정(?:할|해야|해)|확인(?:해|할)|choose|decide|confirm|which)",
    re.IGNORECASE,
)
_PRIVATE_PATH_RE = re.compile(
    r"(?:/Users(?:/[^ \n\t'\"<>]*)?|/home(?:/[^ \n\t'\"<>]*)?|"
    r"/private(?:/[^ \n\t'\"<>]*)?|/var/folders(?:/[^ \n\t'\"<>]*)?|"
    r"/workspace(?:/[^ \n\t'\"<>]*)?|/data/bots(?:/[^ \n\t'\"<>]*)?|"
    r"/var/lib/kubelet(?:/[^ \n\t'\"<>]*)?|"
    r"pvc-[A-Za-z0-9-]+)"
)
_PRIVATE_PATH_ALIAS_RE = re.compile(
    r"(?:users|home|private|var[_:/-]?folders|workspace|data[_:/-]?bots|"
    r"var[_:/-]?lib[_:/-]?kubelet)[_:/-].*"
    r"(?:private[_:/-]?path|leaked[_:/-]?path|session[_:/-]?key|credential|secret)",
    re.IGNORECASE,
)
_SENSITIVE_REF_RE = re.compile(
    r"(?:s3|gs|gcs|supabase|postgres|postgresql|mysql|redis|mongodb|file|vault|"
    r"secret|secrets)://[^\s\"'<>]+|"
    r"https?://(?:"
    r"(?:storage\.googleapis\.com|storage\.cloud\.google\.com|[^/\s\"'<>]*\.storage\.googleapis\.com)|"
    r"(?:[^/\s\"'<>]*s3[^/\s\"'<>]*\.amazonaws\.com|s3[.-][^/\s\"'<>]*\.amazonaws\.com)|"
    r"(?:[^/\s\"'<>]*\.supabase\.co/storage/)|"
    r"(?:[^/\s\"'<>]*\.r2\.cloudflarestorage\.com)|"
    r"(?:[^/\s\"'<>]*blob\.core\.windows\.net)"
    r")[^\s\"'<>]*|"
    r"https?://api\.telegram\.org/bot[0-9]+:[^/\s\"'<>]+[^\s\"'<>]*|"
    r"https?://[^\s\"'<>]*[?&](?:X-Amz-Signature|access[_-]?token|api[_-]?key|auth|"
    r"authorization|cookie|credential|key|password|private[_-]?key|secret|session|"
    r"sig|signature|token)=[^\s\"'<>]+",
    re.IGNORECASE,
)
_COOKIE_HEADER_RE = re.compile(
    r"\b(?:Cookie|Set-Cookie)\s*:\s*[^\n\r]+",
    re.IGNORECASE,
)
_SECRET_TEXT_RE = re.compile(
    r"(?:Bearer\s+[A-Za-z0-9._~+/=-]{8,}|gh[opusr]_[A-Za-z0-9_]{8,}|"
    r"github_pat_[A-Za-z0-9_]{8,}|xox[a-z]-[A-Za-z0-9._-]{8,}|"
    r"AKIA[0-9A-Z]{8,}|AIza[A-Za-z0-9_-]{8,}|"
    r"sk-(?:live|test)?[-_A-Za-z0-9]{8,}|"
    r"authorization[_-]?bearer[_-][A-Za-z0-9._-]{8,}|"
    r"session[_-]?key[_-][A-Za-z0-9._-]{8,}|"
    r"[A-Z0-9_]*(?:SECRET|TOKEN|KEY|PASSWORD)[A-Z0-9_]*\s*[:=]\s*[^,\s}{\n]{4,})",
    re.IGNORECASE,
)
_ROOT_MEMORY_REF_RE = re.compile(r"(?:^|/)memory/root\.md$|(?:^|/)root\.md$", re.IGNORECASE)
_PUBLIC_SAFE_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,180}$")
_RAW_CHILD_REF_RE = re.compile(
    r"(?:^|/)child/(?:transcripts|tool[_-]?logs?)(?:/|$)|"
    r"(?:^|/)(?:raw[_-]?child[_-]?transcript|child[_-]?memory[_-]?raw)(?:\.|/|$)",
    re.IGNORECASE,
)
_CHILD_PROMPT_RE = re.compile(
    r"<child_prompt\b[^>]*>.*?</child_prompt>",
    re.IGNORECASE | re.DOTALL,
)
_RAW_TOOL_LOG_RE = re.compile(
    r"<tool_log\b[^>]*>.*?</tool_log>",
    re.IGNORECASE | re.DOTALL,
)
_HIDDEN_REASONING_RE = re.compile(
    r"<hidden_reasoning\b[^>]*>.*?</hidden_reasoning>",
    re.IGNORECASE | re.DOTALL,
)
_RAW_PRIVATE_LINE_RE = re.compile(
    r"^\s*(?:raw[ _-]?(?:child|subagent|tool|prompt|transcript|output|result|log|args)"
    r"(?:[ _-]?(?:child|subagent|tool|prompt|transcript|output|result|log|args))*|"
    r"(?:child|subagent)[ _-]?(?:prompt|output|transcript)|"
    r"tool[ _-]?(?:log|args|result)|"
    r"hidden[ _-]?reasoning|chain[ _-]?of[ _-]?thought|private[ _-]?reasoning|"
    r"reasoning[ _-]?trace|model[ _-]?internal|private[ _-]?memory|"
    r"authorization|cookie|set-cookie|"
    r"[A-Za-z0-9_]*(?:api[_-]?key|secret|token|password|private[_-]?key)"
    r"[A-Za-z0-9_]*)\s*[:=].*$",
    re.IGNORECASE,
)
_PRIVATE_FRAGMENT_RE = re.compile(
    r"raw[ _-]?(?:child|subagent|tool|prompt|transcript|output|result|log|args)|"
    r"(?:child|subagent)[ _-]?(?:prompt|output|transcript)|"
    r"tool[ _-]?(?:log|args|result)|"
    r"hidden[ _-]?reasoning|chain[ _-]?of[ _-]?thought|private[ _-]?reasoning|"
    r"reasoning[ _-]?trace|model[ _-]?internal|private[ _-]?memory|"
    r"authorization|cookie|set-cookie|"
    r"[A-Za-z0-9_]*(?:api[_-]?key|secret|token|password|private[_-]?key)"
    r"[A-Za-z0-9_]*",
    re.IGNORECASE,
)
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)

_STOP_TOKENS = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
    "about",
    "current",
    "project",
    "memory",
    "context",
    "active",
    "summary",
    "used",
    "says",
    "이",
    "그",
    "저",
    "것",
    "수",
    "등",
    "및",
    "그리고",
    "하지만",
    "현재",
    "프로젝트",
    "맥락",
}


class SourceAuthorityEnvelope(BaseModel):
    model_config = _MODEL_CONFIG

    schema_version: Literal["sourceAuthorityEnvelope.v1"] = Field(
        default="sourceAuthorityEnvelope.v1",
        alias="schemaVersion",
    )
    current_source_kinds: tuple[str, ...] = Field(default=(), alias="currentSourceKinds")
    long_term_memory_policy: LongTermMemoryPolicy = Field(alias="longTermMemoryPolicy")
    classifier_policy: ClassifierMemoryPolicy = Field(alias="classifierPolicy")
    classifier_current_sources_authoritative: bool = Field(
        default=False,
        alias="classifierCurrentSourcesAuthoritative",
    )
    classifier_reason: str = Field(alias="classifierReason")
    authority_order: tuple[str, ...] = Field(alias="authorityOrder")
    rules: tuple[str, ...]
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")

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
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            data.update(dict(update))
        return type(self).model_validate(data)

    @field_validator("classifier_reason", mode="before")
    @classmethod
    def _sanitize_classifier_reason(cls, value: object) -> str:
        return _sanitize_public_metadata_string(str(value or ""))

    @field_validator("current_source_kinds", "authority_order", "rules", mode="before")
    @classmethod
    def _sanitize_public_string_tuple(cls, value: object) -> tuple[str, ...]:
        return _sanitize_reason_codes(value)

    @field_validator("reason_codes", mode="before")
    @classmethod
    def _sanitize_authority_reason_codes(cls, value: object) -> tuple[str, ...]:
        return _sanitize_reason_codes(value)


class SanitizedMemoryReference(BaseModel):
    model_config = _MODEL_CONFIG

    record_id: str = Field(alias="recordId")
    provider_id: str = Field(alias="providerId")
    source_ref: str = Field(alias="sourceRef")
    scope: str
    kind: str
    confidence: str
    visibility: str
    score: float | None = None
    snippet: str
    continuity: MemoryContinuity
    distinctive_phrases: tuple[str, ...] = Field(alias="distinctivePhrases")
    truncated: bool = False
    child_scope: ChildScope = Field(default="none", alias="childScope")
    evidence_ref: str | None = Field(default=None, alias="evidenceRef")

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
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            data.update(dict(update))
        return type(self).model_validate(data)

    @field_validator("source_ref", mode="before")
    @classmethod
    def _sanitize_reference_source_ref(cls, value: object) -> object:
        if isinstance(value, str):
            return _sanitize_source_ref(value)
        return value

    @field_validator("record_id", mode="before")
    @classmethod
    def _sanitize_reference_record_id(cls, value: object) -> object:
        if isinstance(value, str):
            return _safe_public_ref(value, prefix="memory")
        return value

    @field_validator("provider_id", mode="before")
    @classmethod
    def _sanitize_reference_provider_id(cls, value: object) -> object:
        if isinstance(value, str):
            return _safe_public_ref(value, prefix="provider")
        return value

    @field_validator("snippet", mode="before")
    @classmethod
    def _sanitize_reference_snippet(cls, value: object) -> object:
        if isinstance(value, str):
            return _sanitize_memory_snippet(value)
        return value

    @field_validator("distinctive_phrases", mode="before")
    @classmethod
    def _sanitize_reference_phrases(cls, value: object) -> object:
        if value is None:
            return ()
        if isinstance(value, str):
            value = (value,)
        if isinstance(value, Sequence) and not isinstance(value, bytes):
            phrases: list[str] = []
            for item in value:
                if not isinstance(item, str):
                    continue
                if _PRIVATE_FRAGMENT_RE.search(item) or _PRIVATE_PATH_RE.search(item):
                    continue
                sanitized = _sanitize_memory_snippet(item)
                if sanitized:
                    phrases.append(sanitized[:160])
            return tuple(phrases)
        return value

    @field_validator("evidence_ref", mode="before")
    @classmethod
    def _sanitize_reference_evidence_ref(cls, value: object) -> object:
        if isinstance(value, str):
            return _sanitize_optional_metadata_string(value)
        return value

    @model_validator(mode="after")
    def _private_visibility_is_ref_only(self) -> "SanitizedMemoryReference":
        if self.visibility in {"private", "shared"} and self.snippet:
            return self.model_copy(update={"snippet": "", "distinctivePhrases": ()})
        return self


class MemoryProjectionDiagnostics(FalseOnlyAuthorityModel):
    schema_version: Literal["memoryProjectionDiagnostics.v1"] = Field(
        default="memoryProjectionDiagnostics.v1",
        alias="schemaVersion",
    )
    prompt_projection_enabled: Literal[False] = Field(
        default=False,
        alias="promptProjectionEnabled",
    )
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")
    records_input: int = Field(alias="recordsInput")
    records_output: int = Field(alias="recordsOutput")
    bytes_budget: int = Field(alias="bytesBudget")
    bytes_used: int = Field(alias="bytesUsed")
    truncated: bool = False
    rejected_records: int = Field(default=0, alias="rejectedRecords")

    @field_validator("reason_codes", mode="before")
    @classmethod
    def _sanitize_diagnostic_reason_codes(cls, value: object) -> tuple[str, ...]:
        return _sanitize_reason_codes(value)


class MemoryBoundaryProjection(FalseOnlyAuthorityModel):
    schema_version: Literal["memoryBoundaryProjection.v1"] = Field(
        default="memoryBoundaryProjection.v1",
        alias="schemaVersion",
    )
    prompt_projection_allowed: Literal[False] = Field(
        default=False,
        alias="promptProjectionAllowed",
    )
    # NOTE: ``prompt_text`` is a ``Literal[""]`` (string literal, not bool), so
    # the kernel's introspection-based ``_force_false`` doesn't see it. The
    # class contract has ALWAYS pinned it to ``""`` via the pre-C-4 inline
    # ``model_construct``/``model_copy`` rewrites which SILENTLY normalized any
    # attempted override back to ``""`` (NOT raised). We PRESERVE that
    # silent-rewrite invariant with the inline
    # ``_force_prompt_text_empty`` validator + ``_serialize_prompt_text_empty``
    # serializer below -- same precedent as ``MemoryPolicyDecision.write_allowed``
    # in ``policy.py``. Without these, pydantic's own ``Literal[""]`` validator
    # would raise ``ValidationError`` on a malicious
    # ``model_copy(update={"promptText": "..."})`` instead of silently
    # rewriting, breaking the original byte-identical contract.
    prompt_text: Literal[""] = Field(default="", alias="promptText")
    session_injection_allowed: Literal[False] = Field(
        default=False,
        alias="sessionInjectionAllowed",
    )
    write_intent_allowed: bool = Field(alias="writeIntentAllowed")
    references: tuple[SanitizedMemoryReference, ...]
    diagnostics: MemoryProjectionDiagnostics
    source_authority: SourceAuthorityEnvelope = Field(alias="sourceAuthority")

    @model_validator(mode="before")
    @classmethod
    def _force_prompt_text_empty(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        payload = dict(value)
        if "prompt_text" in payload or "promptText" in payload:
            payload.pop("prompt_text", None)
            payload["promptText"] = ""
        return payload

    @field_serializer("prompt_text")
    def _serialize_prompt_text_empty(self, _value: object) -> str:
        return ""


class MemoryRecallRecord(BaseModel):
    model_config = _MODEL_CONFIG

    turn_id: str = Field(alias="turnId")
    source: MemoryRecallSource
    path: str
    continuity: MemoryContinuity
    distinctive_phrases: tuple[str, ...] = Field(alias="distinctivePhrases")
    recorded_at: float | None = Field(default=None, alias="recordedAt")


class StaleMemoryPromotionDecision(BaseModel):
    model_config = _MODEL_CONFIG

    retry: bool
    phrase: str | None = None
    path: str | None = None
    reason: str | None = None


class TurnMemorySummaryProjection(FalseOnlyAuthorityModel):
    schema_version: Literal["turnMemorySummaryProjection.v1"] = Field(
        default="turnMemorySummaryProjection.v1",
        alias="schemaVersion",
    )
    memory_writes_enabled: Literal[False] = Field(
        default=False,
        alias="memoryWritesEnabled",
    )
    production_writes_enabled: Literal[False] = Field(
        default=False,
        alias="productionWritesEnabled",
    )
    turn_digest: str = Field(alias="turnDigest")
    event_digest: str = Field(alias="eventDigest")
    transcript_digest: str | None = Field(default=None, alias="transcriptDigest")
    event_types: tuple[str, ...] = Field(alias="eventTypes")
    tool_result_refs: tuple[str, ...] = Field(default=(), alias="toolResultRefs")
    source_refs: tuple[str, ...] = Field(default=(), alias="sourceRefs")
    transcript_refs: tuple[str, ...] = Field(default=(), alias="transcriptRefs")

    @field_validator(
        "tool_result_refs",
        "source_refs",
        "transcript_refs",
        mode="before",
    )
    @classmethod
    def _sanitize_summary_refs(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        candidates: Sequence[object]
        if isinstance(value, str):
            candidates = (value,)
        elif isinstance(value, Sequence) and not isinstance(value, bytes):
            candidates = value
        else:
            candidates = (value,)
        refs: list[str] = []
        for candidate in candidates:
            if isinstance(candidate, str) and candidate.strip():
                refs.append(_safe_public_ref(candidate, prefix="summary"))
        return tuple(dict.fromkeys(refs))

    @field_validator("turn_digest", "event_digest", "transcript_digest")
    @classmethod
    def _require_summary_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
            raise ValueError("summary digests must be sha256 digests")
        return value


def project_turn_summary_for_memory(
    *,
    turn_id: str,
    normalized_events: Iterable[NormalizedEvent | Mapping[str, object]],
    transcript_entries: Iterable[object] = (),
) -> TurnMemorySummaryProjection:
    """Project a local-only turn summary without raw prompt/output material."""

    events = [NormalizedEvent.model_validate(event) for event in normalized_events]
    entries = list(transcript_entries)
    event_material = [
        {
            "type": event.type,
            "eventId": event.event_id,
            "turnId": event.turn_id,
            "callId": event.call_id,
            "source": event.source,
            "metadata": event.metadata,
        }
        for event in events
    ]
    transcript_material = [
        {
            "kind": getattr(entry, "kind", None),
            "turnId": getattr(entry, "turn_id", None),
            "toolUseId": getattr(entry, "tool_use_id", None),
            "status": getattr(entry, "status", None),
        }
        for entry in entries
    ]
    return TurnMemorySummaryProjection(
        turnDigest=_digest_json({"turnId": turn_id}),
        eventDigest=_digest_json(event_material),
        transcriptDigest=_digest_json(transcript_material) if entries else None,
        eventTypes=tuple(event.type for event in events),
        toolResultRefs=_collect_metadata_refs(events, "toolResultRefs"),
        sourceRefs=_collect_metadata_refs(events, "sourceRefs"),
        transcriptRefs=tuple(
            _safe_public_ref(
                f"{getattr(entry, 'kind', 'entry')}:{index}",
                prefix="transcript",
            )
            for index, entry in enumerate(entries, start=1)
        ),
    )


def detect_current_turn_source_kinds(
    *,
    system: str = "",
    user_text: str = "",
    has_images: bool = False,
) -> tuple[str, ...]:
    kinds = [
        *_extract_source_kinds(system),
        *_extract_source_kinds(user_text),
    ]
    if has_images:
        kinds.append("image")
    return tuple(dict.fromkeys(kind for kind in kinds if kind))


def resolve_source_authority(
    *,
    classifier_policy: ClassifierMemoryPolicy,
    classifier_current_sources_authoritative: bool,
    current_source_kinds: Sequence[str],
    classifier_reason: str | None = None,
) -> SourceAuthorityEnvelope:
    normalized_sources = tuple(
        dict.fromkeys(
            kind
            for kind in (_normalize_source_kind(value) for value in current_source_kinds)
            if kind is not None
        )
    )
    reasons: list[str] = []

    if classifier_policy == "disabled":
        long_term_memory_policy: LongTermMemoryPolicy = "disabled"
        reasons.append("classifier_disabled_long_term_memory")
    elif classifier_policy == "background_only":
        long_term_memory_policy = "background_only"
        reasons.append("classifier_marked_long_term_memory_background_only")
    elif classifier_current_sources_authoritative:
        long_term_memory_policy = "background_only"
        reasons.append("classifier_current_sources_authoritative")
    elif normalized_sources:
        long_term_memory_policy = "background_only"
        reasons.append("current_turn_sources_make_long_term_memory_background_only")
    else:
        long_term_memory_policy = "normal"
        reasons.append("long_term_memory_reference_allowed")

    return SourceAuthorityEnvelope(
        current_source_kinds=normalized_sources,
        long_term_memory_policy=long_term_memory_policy,
        classifier_policy=classifier_policy,
        classifier_current_sources_authoritative=classifier_current_sources_authoritative,
        classifier_reason=_sanitize_public_metadata_string(classifier_reason or reasons[0]),
        authority_order=(
            "L0 latest_user_message",
            "L1 current_turn_sources",
            "L2 current_session_transcript",
            "L3 runtime_state",
            "L4 long_term_memory",
        ),
        rules=(
            "L0/L1 outrank L4 long-term memory",
            "disabled long-term memory cannot be used as evidence",
            "background_only long-term memory is passive context only",
        ),
        reason_codes=tuple(reasons),
    )


def project_memory_boundary(
    recall_result: RecallResult,
    *,
    latest_user_text: str,
    policy: MemoryPolicy | None = None,
    source_authority: SourceAuthorityEnvelope | None = None,
    max_bytes: int = 16_384,
    write_intent: bool = False,
) -> MemoryBoundaryProjection:
    effective_policy = policy or MemoryPolicy(
        memory_mode="normal",
        source_authority="long_term_disabled",
    )
    authority = _sanitize_source_authority_envelope(
        source_authority or _source_authority_from_policy(effective_policy)
    )
    records_input = len(recall_result.records)
    reasons: list[str] = [
        "prompt_projection_disabled",
        *recall_result.reason_codes,
        *authority.reason_codes,
    ]
    references: list[SanitizedMemoryReference] = []
    rejected = 0
    truncated = False

    recall_refs_allowed = (
        recall_result.recall_allowed
        and recall_result.public_projection_allowed
        and effective_policy.memory_mode != "incognito"
        and authority.long_term_memory_policy != "disabled"
    )

    if effective_policy.memory_mode == "incognito":
        reasons.append("incognito_blocks_prior_long_term_memory")
    if authority.long_term_memory_policy == "disabled":
        reasons.append("source_authority_disables_long_term_memory")
    if authority.long_term_memory_policy == "background_only":
        reasons.append("source_authority_background_only")
    if not recall_result.public_projection_allowed:
        reasons.append("public_projection_blocked")
    if not recall_result.recall_allowed:
        reasons.append("recall_blocked")

    if recall_refs_allowed:
        for record in recall_result.records:
            child_scope = _child_scope(record)
            if child_scope == "raw":
                rejected += 1
                reasons.append("child_raw_memory_rejected")
                continue
            snippet = (
                _sanitize_memory_snippet(record.body)
                if record.visibility == "public-safe"
                else ""
            )
            continuity = classify_memory_continuity(
                latest_user_text=latest_user_text,
                memory_text=record.body,
                source=_memory_recall_source(record),
            )
            if authority.long_term_memory_policy == "background_only":
                continuity = "background"
            references.append(
                SanitizedMemoryReference(
                    record_id=record.id,
                    provider_id=record.provider_id,
                    source_ref=_sanitize_source_ref(record.source_ref),
                    scope=record.scope,
                    kind=record.kind,
                    confidence=record.confidence,
                    visibility=record.visibility,
                    score=record.score,
                    snippet=snippet,
                    continuity=continuity,
                    distinctive_phrases=tuple(extract_distinctive_phrases(snippet)[:12]),
                    child_scope=(
                        "sanitized_envelope"
                        if child_scope == "sanitized_envelope"
                        else "none"
                    ),
                    evidence_ref=_sanitize_optional_metadata_string(
                        record.custom_metadata.get("evidenceRef")
                    ),
                )
            )

    budgeted_refs, bytes_used, budget_truncated = _apply_reference_budget(
        references,
        max_bytes=max_bytes,
    )
    if budget_truncated:
        truncated = True
        reasons.append("budget_truncated")

    if write_intent:
        reasons.append("memory_writes_disabled")
        if effective_policy.memory_mode == "read_only":
            reasons.append("read_only_blocks_writes")
        if effective_policy.memory_mode == "incognito":
            reasons.append("incognito_blocks_writes")

    return MemoryBoundaryProjection(
        write_intent_allowed=False,
        references=tuple(budgeted_refs),
        diagnostics=MemoryProjectionDiagnostics(
            reason_codes=tuple(dict.fromkeys(reasons)),
            records_input=records_input,
            records_output=len(budgeted_refs),
            bytes_budget=max_bytes,
            bytes_used=bytes_used,
            truncated=truncated,
            rejected_records=rejected,
        ),
        source_authority=authority,
    )


def project_namespaced_memory_boundary(
    recall_result: RecallResult,
    *,
    namespace_policy: MemoryNamespacePolicy,
    latest_user_text: str,
    max_bytes: int = 16_384,
    write_intent: bool = False,
) -> MemoryBoundaryProjection:
    admission = admit_recall_result_to_namespace(recall_result, namespace_policy)
    return project_memory_boundary(
        admission.result,
        latest_user_text=latest_user_text,
        policy=MemoryPolicy(
            memory_mode=namespace_policy.memory_mode,
            source_authority=namespace_policy.source_authority,
        ),
        max_bytes=max_bytes,
        write_intent=write_intent,
    )


def has_continuation_cue(text: str) -> bool:
    return _CONTINUATION_CUE_RE.search(text) is not None


def classify_memory_continuity(
    *,
    latest_user_text: str,
    memory_text: str,
    source: MemoryRecallSource,
) -> MemoryContinuity:
    latest_tokens = _significant_tokens(latest_user_text)
    memory_tokens = _significant_tokens(memory_text)
    overlap = _overlap_count(latest_tokens, memory_tokens)

    if source == "root" and not has_continuation_cue(latest_user_text):
        return "background"
    if has_continuation_cue(latest_user_text) and overlap > 0:
        return "active"
    if overlap > 0:
        return "related"
    return "background"


def extract_distinctive_phrases(text: str) -> tuple[str, ...]:
    tokens = [
        token
        for token in (_normalize_token(raw) for raw in _tokenize(text))
        if len(token) >= 2 and token not in _STOP_TOKENS
    ]
    phrases: list[str] = []
    seen: set[str] = set()

    for size in range(min(5, len(tokens)), 1, -1):
        for index in range(0, len(tokens) - size + 1):
            phrase = " ".join(tokens[index : index + size])
            normalized = _normalize_text(phrase)
            if len(normalized) < 6 or normalized in seen:
                continue
            seen.add(normalized)
            phrases.append(phrase)
            if len(phrases) >= 12:
                return tuple(phrases)

    return tuple(phrases)


def should_retry_stale_memory_promotion(
    *,
    latest_user_text: str,
    assistant_text: str,
    records: Sequence[MemoryRecallRecord],
) -> StaleMemoryPromotionDecision:
    if has_continuation_cue(latest_user_text):
        return StaleMemoryPromotionDecision(retry=False)
    if _DECISION_REQUEST_RE.search(assistant_text.strip()) is None:
        return StaleMemoryPromotionDecision(retry=False)

    latest = _normalize_text(latest_user_text)
    assistant = _normalize_text(assistant_text)
    for record in records:
        if record.continuity != "background":
            continue
        for phrase in record.distinctive_phrases:
            normalized_phrase = _normalize_text(phrase)
            if len(normalized_phrase) < 6:
                continue
            if normalized_phrase not in assistant:
                continue
            if normalized_phrase in latest:
                continue
            return StaleMemoryPromotionDecision(
                retry=True,
                phrase=phrase,
                path=record.path,
                reason="background memory phrase promoted into decision request",
            )

    return StaleMemoryPromotionDecision(retry=False)


def _source_authority_from_policy(policy: MemoryPolicy) -> SourceAuthorityEnvelope:
    if policy.source_authority == "long_term_disabled":
        classifier_policy: ClassifierMemoryPolicy = "disabled"
    elif policy.source_authority == "child_isolated":
        classifier_policy = "disabled"
    elif policy.source_authority == "memory_redact_authority":
        classifier_policy = "disabled"
    elif policy.source_authority == "background_only":
        classifier_policy = "background_only"
    else:
        classifier_policy = "normal"
    authority = resolve_source_authority(
        classifier_policy=classifier_policy,
        classifier_current_sources_authoritative=False,
        current_source_kinds=(),
        classifier_reason=f"policy:{policy.source_authority}",
    )
    if policy.source_authority not in {"child_isolated", "memory_redact_authority"}:
        return authority
    if policy.source_authority == "memory_redact_authority":
        return authority.model_copy(
            update={
                "classifierReason": "policy:memory_redact_authority",
                "reasonCodes": tuple(
                    dict.fromkeys(
                        (
                            *authority.reason_codes,
                            "memory_redact_authority_supersedes_provider",
                        )
                    )
                ),
            }
        )
    return authority.model_copy(
        update={
            "classifierReason": "policy:child_isolated",
            "reasonCodes": tuple(
                dict.fromkeys(
                    (
                        *authority.reason_codes,
                        "child_memory_scope_isolated",
                    )
                )
            ),
        }
    )


def _apply_reference_budget(
    references: Sequence[SanitizedMemoryReference],
    *,
    max_bytes: int,
) -> tuple[list[SanitizedMemoryReference], int, bool]:
    if max_bytes < 1:
        max_bytes = 1

    output: list[SanitizedMemoryReference] = []
    used = 0
    truncated = False

    for index, ref in enumerate(references):
        candidate = ref
        candidate_bytes = _reference_bytes(candidate)
        if used + candidate_bytes > max_bytes:
            remaining = max_bytes - used
            truncated = True
            if remaining <= 0:
                break
            candidate = _truncate_reference_to_budget(ref, remaining)
            candidate_bytes = _reference_bytes(candidate)
            if used + candidate_bytes > max_bytes:
                break
        output.append(candidate)
        used += candidate_bytes
        if candidate.truncated or index < len(references) - 1 and used >= max_bytes:
            truncated = True
            if used >= max_bytes:
                break

    if len(output) < len(references):
        truncated = True
    return output, used, truncated


def _truncate_reference_to_budget(
    ref: SanitizedMemoryReference,
    budget: int,
) -> SanitizedMemoryReference:
    base = ref.model_copy(update={"snippet": "", "truncated": True})
    base_bytes = _reference_bytes(base)
    if base_bytes >= budget:
        return base

    suffix = "..."
    room = max(0, budget - base_bytes - len(suffix.encode("utf-8")))
    snippet = _slice_utf8(ref.snippet, room)
    if snippet:
        snippet = f"{snippet}{suffix}"
    return ref.model_copy(update={"snippet": snippet, "truncated": True})


def _reference_bytes(ref: SanitizedMemoryReference) -> int:
    return len(ref.model_dump_json(by_alias=True).encode("utf-8"))


def _child_scope(record: MemoryRecord) -> Literal["none", "raw", "sanitized_envelope"]:
    metadata = record.custom_metadata
    if metadata.get("childMemoryRaw") is True or metadata.get("rawChildTranscript") is True:
        return "raw"
    if any(_is_raw_child_ref(candidate) for candidate in _record_identity_candidates(record)):
        return "raw"
    if metadata.get("childEnvelopeSanitized") is True or metadata.get("evidenceRef") is not None:
        return "sanitized_envelope"
    return "none"


def _sanitize_memory_snippet(body: str) -> str:
    sanitized = _SENSITIVE_REF_RE.sub("[redacted-ref]", body)
    sanitized = _COOKIE_HEADER_RE.sub("[redacted-cookie]", sanitized)
    sanitized = _SECRET_TEXT_RE.sub("[redacted]", sanitized)
    sanitized = _CHILD_PROMPT_RE.sub("[redacted child prompt]", sanitized)
    sanitized = _RAW_TOOL_LOG_RE.sub("[redacted tool log]", sanitized)
    sanitized = _HIDDEN_REASONING_RE.sub("[redacted hidden reasoning]", sanitized)
    sanitized = "\n".join(_drop_private_projection_lines(sanitized.splitlines()))
    sanitized = _PRIVATE_PATH_RE.sub(_redact_private_path, sanitized)
    sanitized = _PRIVATE_PATH_ALIAS_RE.sub("[private_path]", sanitized)
    return sanitize_tool_preview(sanitized)


def _drop_private_projection_lines(lines: list[str]) -> list[str]:
    public_lines: list[str] = []
    for line in lines:
        line_has_marker = (
            _RAW_PRIVATE_LINE_RE.search(line) is not None
            or _PRIVATE_FRAGMENT_RE.search(line) is not None
            or _PRIVATE_PATH_RE.search(line) is not None
            or _PRIVATE_PATH_ALIAS_RE.search(line) is not None
            or "[redacted-ref]" in line
        )
        if line_has_marker:
            break
        public_lines.append(line)
    return public_lines


def _sanitize_source_ref(source_ref: str) -> str:
    normalized = source_ref.replace("\\", "/")
    if _source_ref_is_sensitive(normalized):
        return _hashed_memory_ref(normalized)
    marker = "/memory/"
    if marker in normalized:
        return f"memory/{normalized.split(marker, 1)[1]}"
    parts = [part for part in normalized.split("/") if part]
    if len(parts) >= 2:
        return "/".join(parts[-2:])
    return parts[-1] if parts else "(unknown)"


def _safe_public_ref(value: str, *, prefix: str) -> str:
    if _PUBLIC_SAFE_REF_RE.fullmatch(value) and not _source_ref_is_sensitive(value):
        return value
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}:{digest}"


def _redact_private_path(match: re.Match[str]) -> str:
    raw = match.group(0).replace("\\", "/")
    basename = raw.rsplit("/", 1)[-1]
    return f"[private_path]/{basename}" if basename else "[private_path]"


def _sanitize_optional_metadata_string(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return _sanitize_public_metadata_string(value)
    return None


def _sanitize_public_metadata_string(value: str) -> str:
    sanitized = _SENSITIVE_REF_RE.sub("[redacted-ref]", value.strip())
    sanitized = _COOKIE_HEADER_RE.sub("[redacted-cookie]", sanitized)
    sanitized = _SECRET_TEXT_RE.sub("[redacted]", sanitized)
    sanitized = "\n".join(_drop_private_projection_lines(sanitized.splitlines()))
    sanitized = _PRIVATE_PATH_RE.sub(_redact_private_path, sanitized)
    sanitized = _PRIVATE_PATH_ALIAS_RE.sub("[private_path]", sanitized)
    preview = sanitize_tool_preview(sanitized).strip()[:160]
    return preview or "[redacted-metadata]"


def _sanitize_reason_codes(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        candidates: Sequence[object] = (value,)
    elif isinstance(value, Sequence) and not isinstance(value, bytes):
        candidates = value
    else:
        candidates = (value,)
    return tuple(
        dict.fromkeys(_sanitize_public_metadata_string(str(candidate)) for candidate in candidates)
    )


def _sanitize_source_authority_envelope(
    authority: SourceAuthorityEnvelope,
) -> SourceAuthorityEnvelope:
    return SourceAuthorityEnvelope.model_validate(
        authority.model_dump(by_alias=True, mode="python", warnings=False)
    )


def _memory_recall_source(record: MemoryRecord) -> MemoryRecallSource:
    if any(_is_root_memory_ref(candidate) for candidate in _record_identity_candidates(record)):
        return "root"
    explicit = record.custom_metadata.get("recallSource")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip().lower()
    return ""


def _record_identity_candidates(record: MemoryRecord) -> tuple[str, ...]:
    candidates: list[str] = [record.source_ref, record.id]
    for key in ("sourceRef", "source_ref", "path", "recordPath", "filePath"):
        value = record.custom_metadata.get(key)
        if isinstance(value, str) and value.strip():
            candidates.append(value)
    return tuple(dict.fromkeys(candidates))


def _is_root_memory_ref(value: str) -> bool:
    normalized = value.replace("\\", "/").lower()
    return _ROOT_MEMORY_REF_RE.search(normalized) is not None


def _is_raw_child_ref(value: str) -> bool:
    normalized = value.replace("\\", "/").lower()
    return _RAW_CHILD_REF_RE.search(normalized) is not None


def _source_ref_is_sensitive(value: str) -> bool:
    lowered = value.casefold()
    return (
        _PRIVATE_PATH_RE.search(value) is not None
        or _PRIVATE_PATH_ALIAS_RE.search(value) is not None
        or _RAW_PRIVATE_LINE_RE.search(value) is not None
        or _PRIVATE_FRAGMENT_RE.search(value) is not None
        or _SENSITIVE_REF_RE.search(value) is not None
        or _COOKIE_HEADER_RE.search(value) is not None
        or _SECRET_TEXT_RE.search(value) is not None
        or ".." in value.split("/")
        or "telegram" in lowered
        or "authorization" in lowered
        or "cookie" in lowered
    )


def _hashed_memory_ref(value: str) -> str:
    return f"memory:{hashlib.sha1(value.encode('utf-8')).hexdigest()[:16]}"


def _extract_source_kinds(text: str) -> tuple[str, ...]:
    kinds: list[str] = []
    for match in _CURRENT_AUTHORITY_SOURCE_RE.finditer(text):
        kind = _normalize_source_kind(match.group(1))
        if kind is not None:
            kinds.append(kind)
    return tuple(dict.fromkeys(kinds))


def _normalize_source_kind(value: str) -> str | None:
    normalized = re.sub(r"[^a-z0-9_-]+", "_", value.strip().lower())
    normalized = normalized.strip("_")
    return normalized[:80] if normalized else None


def _overlap_count(left: Iterable[str], right: Iterable[str]) -> int:
    right_set = set(right)
    return sum(1 for token in set(left) if token in right_set)


def _significant_tokens(text: str) -> tuple[str, ...]:
    return tuple(
        token
        for token in (_normalize_token(raw) for raw in _tokenize(text))
        if len(token) >= 2 and token not in _STOP_TOKENS
    )


def _tokenize(text: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFC", text)
    return tuple(match.group(0) for match in _TOKEN_RE.finditer(normalized))


def _normalize_token(token: str) -> str:
    lowered = token.lower()
    return re.sub(
        r"(으로|에서|에게|께|을|를|은|는|이|가|과|와|도|만|로|에|의)$",
        "",
        lowered,
    )


def _normalize_text(text: str) -> str:
    return " ".join(_normalize_token(token) for token in _tokenize(text))


def _collect_metadata_refs(
    events: Iterable[NormalizedEvent],
    key: str,
) -> tuple[str, ...]:
    refs: list[str] = []
    for event in events:
        value = event.metadata.get(key)
        candidates: Sequence[object]
        if isinstance(value, str):
            candidates = (value,)
        elif isinstance(value, Sequence) and not isinstance(value, bytes):
            candidates = value
        else:
            candidates = ()
        for candidate in candidates:
            if isinstance(candidate, str) and candidate.strip():
                refs.append(_safe_public_ref(candidate, prefix="summary"))
    return tuple(dict.fromkeys(refs))


def _digest_json(value: object) -> str:
    payload = json.dumps(
        _json_digest_safe_value(value),
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _json_digest_safe_value(value: object) -> object:
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            return None
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_digest_safe_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return [_json_digest_safe_value(item) for item in value]
    return value


def _slice_utf8(value: str, max_bytes: int) -> str:
    if max_bytes <= 0:
        return ""
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    end = max_bytes
    while end > 0 and (encoded[end] & 0xC0) == 0x80:
        end -= 1
    return encoded[:end].decode("utf-8", errors="ignore")


__all__ = [
    "MemoryBoundaryProjection",
    "MemoryContinuity",
    "MemoryProjectionDiagnostics",
    "MemoryRecallRecord",
    "SanitizedMemoryReference",
    "SourceAuthorityEnvelope",
    "StaleMemoryPromotionDecision",
    "TurnMemorySummaryProjection",
    "classify_memory_continuity",
    "detect_current_turn_source_kinds",
    "extract_distinctive_phrases",
    "has_continuation_cue",
    "project_memory_boundary",
    "project_namespaced_memory_boundary",
    "project_turn_summary_for_memory",
    "resolve_source_authority",
    "should_retry_stale_memory_promotion",
]
