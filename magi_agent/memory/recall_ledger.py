from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from typing import Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator


MemoryRecallLedgerStatus: TypeAlias = Literal["disabled", "recorded"]
MemoryRecallSource: TypeAlias = Literal["provider", "adk_memory_service"]
MemoryRecallDecisionKind: TypeAlias = Literal["allowed", "suppressed"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_PUBLIC_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,180}$")
_PRIVATE_RE = re.compile(
    r"(?:"
    r"/Users(?:/[^\s,;}\"']*)?|"
    r"/home(?:/[^\s,;}\"']*)?|"
    r"/workspace(?:/[^\s,;}\"']*)?|"
    r"/data/bots(?:/[^\s,;}\"']*)?|"
    r"/var/lib/kubelet(?:/[^\s,;}\"']*)?|"
    r"s3://[^\s\"']+|"
    r"gs://[^\s\"']+|"
    r"file://[^\s\"']+|"
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
    r"sig|signature|token)=[^\s\"'<>]+|"
    r"authorization|cookie|set-cookie|bearer\s+[A-Za-z0-9._-]+|"
    r"[A-Za-z0-9_]*(?:api[_-]?key|secret|token|password|private[_-]?key)"
    r"[A-Za-z0-9_]*[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9._~+/=-]+|"
    r"sk-[A-Za-z0-9._-]+|"
    r"gh[opusr]_[A-Za-z0-9_]+|"
    r"github_pat_[A-Za-z0-9_]+|"
    r"xox[a-z]-[A-Za-z0-9._-]+|"
    r"AKIA[0-9A-Z]{8,}|"
    r"AIza[A-Za-z0-9_-]+|"
    r"raw[ _-]?(?:tool|child|subagent|prompt|transcript|output|result|log|args)|"
    r"(?:child|subagent)[ _-]?(?:prompt|output|transcript)|"
    r"tool[ _-]?(?:log|args|result)|"
    r"<(?:/?)(?:tool[_-]?log|child[_-]?prompt|hidden[_-]?reasoning)\b|"
    r"hidden[ _-]?reasoning|private[ _-]?reasoning|"
    r"chain[ _-]?of[ _-]?thought|private[ _-]?memory"
    r")",
    re.IGNORECASE,
)
_PRIVATE_BLOCK_RE = re.compile(
    r"<(?:tool[_-]?log|child[_-]?prompt|hidden[_-]?reasoning)\b[^>]*>.*?"
    r"</(?:tool[_-]?log|child[_-]?prompt|hidden[_-]?reasoning)>",
    re.IGNORECASE | re.DOTALL,
)


class _RecallLedgerModel(BaseModel):
    model_config = _MODEL_CONFIG

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: object) -> Self:
        _ = _fields_set
        return cls.model_validate(values)

    def model_copy(
        self,
        *,
        update: Mapping[str, object] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            data.update(dict(update))
        return type(self).model_validate(data)


class MemoryRecallLedgerConfig(_RecallLedgerModel):
    enabled: bool = False
    current_source_authoritative: bool = Field(
        default=False,
        alias="currentSourceAuthoritative",
    )


class MemoryRecallLedgerAuthorityFlags(_RecallLedgerModel):
    memory_provider_called: Literal[False] = Field(default=False, alias="memoryProviderCalled")
    adk_memory_service_called: Literal[False] = Field(
        default=False,
        alias="adkMemoryServiceCalled",
    )
    prompt_injection_allowed: Literal[False] = Field(
        default=False,
        alias="promptInjectionAllowed",
    )
    memory_write_allowed: Literal[False] = Field(default=False, alias="memoryWriteAllowed")
    production_write_allowed: Literal[False] = Field(
        default=False,
        alias="productionWriteAllowed",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_false_inputs(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        return {field.alias or name: False for name, field in cls.model_fields.items()}

    @field_serializer(
        "memory_provider_called",
        "adk_memory_service_called",
        "prompt_injection_allowed",
        "memory_write_allowed",
        "production_write_allowed",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class MemoryRecallRecordInput(_RecallLedgerModel):
    record_id: str = Field(alias="recordId")
    provider_id: str = Field(alias="providerId")
    source: MemoryRecallSource
    source_ref: str = Field(alias="sourceRef")
    evidence_ref: str | None = Field(default=None, alias="evidenceRef")
    snippet: str = ""
    visibility: str = "public-safe"


class MemoryRecallDecision(_RecallLedgerModel):
    record_ref: str = Field(alias="recordRef")
    evidence_ref: str | None = Field(default=None, alias="evidenceRef")
    source: MemoryRecallSource
    decision: MemoryRecallDecisionKind
    reason_code: str = Field(alias="reasonCode")
    snippet_preview: str = Field(default="", alias="snippetPreview")


class MemoryRecallLedger(_RecallLedgerModel):
    status: MemoryRecallLedgerStatus
    decisions: tuple[MemoryRecallDecision, ...] = ()
    public_refs: tuple[str, ...] = Field(default=(), alias="publicRefs")
    diagnostic_metadata: Mapping[str, object] = Field(
        default_factory=dict,
        alias="diagnosticMetadata",
    )
    authority_flags: MemoryRecallLedgerAuthorityFlags = Field(
        default_factory=MemoryRecallLedgerAuthorityFlags,
        alias="authorityFlags",
    )


def build_memory_recall_ledger(
    records: Sequence[MemoryRecallRecordInput | Mapping[str, object]],
    *,
    config: MemoryRecallLedgerConfig | Mapping[str, object] | None = None,
) -> MemoryRecallLedger:
    safe_config = MemoryRecallLedgerConfig.model_validate(config or {})
    if not safe_config.enabled:
        return MemoryRecallLedger(
            status="disabled",
            diagnosticMetadata={"enabled": False, "recordCount": 0},
            authorityFlags=MemoryRecallLedgerAuthorityFlags(),
        )

    seen: set[tuple[str, str]] = set()
    decisions: list[MemoryRecallDecision] = []
    public_refs: list[str] = []
    for raw_record in records:
        record = MemoryRecallRecordInput.model_validate(raw_record)
        key = (record.provider_id, record.record_id)
        record_ref = _memory_ref(record.record_id)
        evidence_ref = _safe_optional_ref(record.evidence_ref, prefix="evidence")
        snippet = _sanitize_text(record.snippet)

        if key in seen:
            decisions.append(
                _decision(record, record_ref, evidence_ref, "suppressed", "duplicate_recall_record", "")
            )
            continue
        seen.add(key)

        if record.visibility in {"private", "shared"} or _PRIVATE_RE.search(record.source_ref):
            decisions.append(
                _decision(record, record_ref, None, "suppressed", "private_memory_ref_only", "")
            )
            continue
        if safe_config.current_source_authoritative:
            decisions.append(
                _decision(
                    record,
                    record_ref,
                    evidence_ref,
                    "suppressed",
                    "current_source_outranks_memory",
                    "",
                )
            )
            continue
        if not snippet:
            decisions.append(
                _decision(record, record_ref, evidence_ref, "suppressed", "empty_public_snippet", "")
            )
            continue

        decisions.append(
            _decision(record, record_ref, evidence_ref, "allowed", "recall_ref_allowed", snippet)
        )
        public_refs.append(record_ref)
        if evidence_ref is not None:
            public_refs.append(evidence_ref)

    return MemoryRecallLedger(
        status="recorded",
        decisions=tuple(decisions),
        publicRefs=tuple(dict.fromkeys(public_refs)),
        diagnosticMetadata={
            "enabled": True,
            "recordCount": len(records),
            "currentSourceAuthoritative": safe_config.current_source_authoritative,
        },
        authorityFlags=MemoryRecallLedgerAuthorityFlags(),
    )


def _decision(
    record: MemoryRecallRecordInput,
    record_ref: str,
    evidence_ref: str | None,
    decision: MemoryRecallDecisionKind,
    reason_code: str,
    snippet_preview: str,
) -> MemoryRecallDecision:
    return MemoryRecallDecision(
        recordRef=record_ref,
        evidenceRef=evidence_ref,
        source=record.source,
        decision=decision,
        reasonCode=reason_code,
        snippetPreview=snippet_preview,
    )


def _memory_ref(record_id: str) -> str:
    if _PUBLIC_REF_RE.fullmatch(record_id) and _PRIVATE_RE.search(record_id) is None:
        return f"memory-ref:{record_id}"
    return f"memory-ref:{hashlib.sha1(record_id.encode('utf-8')).hexdigest()[:16]}"


def _safe_optional_ref(value: str | None, *, prefix: str) -> str | None:
    if not value:
        return None
    clean = _sanitize_text(value)
    if clean == value and _PUBLIC_REF_RE.fullmatch(clean):
        return clean
    return f"{prefix}:{hashlib.sha1(value.encode('utf-8')).hexdigest()[:16]}"


def _sanitize_text(value: str) -> str:
    value = _PRIVATE_BLOCK_RE.sub("", value)
    lines = _drop_private_marker_lines(value.splitlines())
    return "\n".join(lines)[:300]


def _drop_private_marker_lines(lines: list[str]) -> list[str]:
    public_lines: list[str] = []
    for line in lines:
        line_has_marker = bool(_PRIVATE_RE.search(line))
        if not line.strip():
            continue
        if line_has_marker:
            break
        public_lines.append(line)
    return public_lines


__all__ = [
    "MemoryRecallDecision",
    "MemoryRecallLedger",
    "MemoryRecallLedgerAuthorityFlags",
    "MemoryRecallLedgerConfig",
    "MemoryRecallRecordInput",
    "build_memory_recall_ledger",
]
