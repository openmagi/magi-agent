from __future__ import annotations

from collections.abc import Mapping
import hashlib
import re
from typing import Literal

from pydantic import Field, field_validator

from magi_agent.ops.authority import FalseOnlyAuthorityModel

from .contracts import MemoryRecord, RecallResult
from .policy import MemoryMode, MemorySourceAuthority


MemoryNamespaceDecisionStatus = Literal["allowed", "blocked", "background_only"]
MemoryRedactionState = Literal["verified", "not_required", "unverified", "failed"]
MemoryRetentionState = Literal["active", "expired", "suspended"]
MemoryEraseState = Literal["active", "erased", "tombstoned"]

_SAFE_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,180}$")
_SAFE_REASON_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,120}$")
_PRIVATE_VISIBILITIES = frozenset({"private", "shared"})
_NAMESPACE_KEYS = (
    "namespaceRef",
    "namespace_ref",
    "memoryNamespaceRef",
    "memory_namespace_ref",
    "namespace",
)
_REDACTION_KEYS = ("redactionStatus", "redaction_status", "redactionState", "redaction_state")
_RETENTION_KEYS = ("retentionState", "retention_state")
_ERASE_KEYS = ("eraseState", "erase_state")
_STALE_KEYS = ("stale", "staleMemory", "stale_memory", "staleRef", "stale_ref")
_RECALL_BLOCKING_SOURCE_AUTHORITIES = frozenset(
    {
        "long_term_disabled",
        "memory_redact_authority",
        "child_isolated",
    }
)
_PUBLIC_BLOCKING_SOURCE_AUTHORITIES = frozenset(
    {
        "background_only",
        *_RECALL_BLOCKING_SOURCE_AUTHORITIES,
    }
)
_SENSITIVE_TEXT_RE = re.compile(
    r"authorization|bearer|cookie|set-cookie|session[_-]?key|connector[_-]?token|"
    r"api[_-]?key|private[_-]?key|password|secret|credential|token|"
    r"github_pat|gh[opusr]_|sk-(?:live|test)|akia[0-9a-z]{8,}|"
    r"/Users/|/home/|/workspace/|/data/bots/|/var/lib/kubelet/",
    re.IGNORECASE,
)


class MemoryNamespacePolicy(FalseOnlyAuthorityModel):
    namespace_ref: str = Field(alias="namespaceRef")
    memory_mode: MemoryMode = Field(default="normal", alias="memoryMode")
    source_authority: MemorySourceAuthority = Field(
        default="long_term_allowed",
        alias="sourceAuthority",
    )
    redaction_state: MemoryRedactionState = Field(default="verified", alias="redactionState")
    retention_state: MemoryRetentionState = Field(default="active", alias="retentionState")
    erase_state: MemoryEraseState = Field(default="active", alias="eraseState")
    prompt_projection_allowed: Literal[False] = Field(
        default=False,
        alias="promptProjectionAllowed",
    )
    memory_write_allowed: Literal[False] = Field(default=False, alias="memoryWriteAllowed")

    @field_validator("namespace_ref")
    @classmethod
    def _validate_namespace_ref(cls, value: str) -> str:
        return _safe_public_ref(value, prefix="memory-ns")


class MemoryNamespaceDecision(FalseOnlyAuthorityModel):
    record_id: str = Field(alias="recordId")
    namespace_ref: str | None = Field(default=None, alias="namespaceRef")
    status: MemoryNamespaceDecisionStatus
    public_projection_allowed: bool = Field(alias="publicProjectionAllowed")
    prompt_projection_allowed: Literal[False] = Field(
        default=False,
        alias="promptProjectionAllowed",
    )
    memory_write_allowed: Literal[False] = Field(default=False, alias="memoryWriteAllowed")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")

    @field_validator("record_id", mode="before")
    @classmethod
    def _sanitize_record_id(cls, value: object) -> str:
        return _safe_public_ref(str(value), prefix="memory")

    @field_validator("namespace_ref", mode="before")
    @classmethod
    def _sanitize_namespace_ref(cls, value: object) -> str | None:
        if value is None:
            return None
        return _safe_public_ref(str(value), prefix="memory-ns")

    @field_validator("reason_codes", mode="before")
    @classmethod
    def _sanitize_reason_codes(cls, value: object) -> tuple[str, ...]:
        return _sanitize_reason_codes(value)


class MemoryNamespaceAdmission(FalseOnlyAuthorityModel):
    namespace_policy: MemoryNamespacePolicy = Field(alias="namespacePolicy")
    decisions: tuple[MemoryNamespaceDecision, ...]
    result: RecallResult
    prompt_projection_allowed: Literal[False] = Field(
        default=False,
        alias="promptProjectionAllowed",
    )
    memory_write_allowed: Literal[False] = Field(default=False, alias="memoryWriteAllowed")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")

    @field_validator("reason_codes", mode="before")
    @classmethod
    def _sanitize_reason_codes(cls, value: object) -> tuple[str, ...]:
        return _sanitize_reason_codes(value)

    def public_projection(self) -> dict[str, object]:
        return {
            "namespacePolicy": {
                "namespaceRef": self.namespace_policy.namespace_ref,
                "memoryMode": self.namespace_policy.memory_mode,
                "sourceAuthority": self.namespace_policy.source_authority,
                "redactionState": self.namespace_policy.redaction_state,
                "retentionState": self.namespace_policy.retention_state,
                "eraseState": self.namespace_policy.erase_state,
                "promptProjectionAllowed": False,
                "memoryWriteAllowed": False,
            },
            "promptProjectionAllowed": False,
            "memoryWriteAllowed": False,
            "reasonCodes": list(self.reason_codes),
            "decisions": [
                decision.model_dump(by_alias=True, mode="json") for decision in self.decisions
            ],
            "result": _safe_recall_public_projection(self.result),
        }


def admit_recall_result_to_namespace(
    recall_result: RecallResult,
    namespace_policy: MemoryNamespacePolicy,
) -> MemoryNamespaceAdmission:
    decisions = tuple(
        evaluate_memory_record_namespace(record, namespace_policy)
        for record in recall_result.records
    )
    accepted_records = tuple(
        record
        for record, decision in zip(recall_result.records, decisions, strict=True)
        if decision.status == "allowed"
    )
    public_projection_allowed = (
        recall_result.public_projection_allowed
        and namespace_policy.memory_mode != "incognito"
        and namespace_policy.source_authority not in _PUBLIC_BLOCKING_SOURCE_AUTHORITIES
        and namespace_policy.redaction_state in {"verified", "not_required"}
        and namespace_policy.retention_state == "active"
        and namespace_policy.erase_state == "active"
    )
    recall_allowed = (
        recall_result.recall_allowed
        and namespace_policy.memory_mode != "incognito"
        and namespace_policy.source_authority not in _RECALL_BLOCKING_SOURCE_AUTHORITIES
        and namespace_policy.redaction_state in {"verified", "not_required"}
        and namespace_policy.retention_state == "active"
        and namespace_policy.erase_state == "active"
    )
    if not public_projection_allowed:
        accepted_records = ()
    result = recall_result.model_copy(
        update={
            "records": accepted_records,
            "recallAllowed": recall_allowed,
            "writeAllowed": False,
            "promptProjectionAllowed": False,
            "publicProjectionAllowed": public_projection_allowed,
            "reasonCodes": tuple(
                dict.fromkeys(
                    (
                        *recall_result.reason_codes,
                        *_policy_reason_codes(namespace_policy),
                        *(
                            reason
                            for decision in decisions
                            for reason in decision.reason_codes
                        ),
                    )
                )
            ),
        }
    )
    return MemoryNamespaceAdmission(
        namespace_policy=namespace_policy,
        decisions=decisions,
        result=result,
        reason_codes=result.reason_codes,
    )


def evaluate_memory_record_namespace(
    record: MemoryRecord,
    namespace_policy: MemoryNamespacePolicy,
) -> MemoryNamespaceDecision:
    reasons = list(_policy_reason_codes(namespace_policy))
    record_namespace = _record_namespace_ref(record)
    status: MemoryNamespaceDecisionStatus = "allowed"
    public_projection_allowed = True

    if record_namespace != namespace_policy.namespace_ref:
        reasons.append("memory_namespace_mismatch")
        status = "blocked"
        public_projection_allowed = False
    if record.visibility in _PRIVATE_VISIBILITIES:
        reasons.append("private_memory_excluded")
        status = "blocked"
        public_projection_allowed = False
    if _record_is_stale(record):
        reasons.append("stale_memory_ref_denied")
        status = "blocked"
        public_projection_allowed = False
    if _record_redaction_state(record) not in {"verified", "not_required"}:
        reasons.append("memory_redaction_not_verified")
        status = "blocked"
        public_projection_allowed = False
    if _record_retention_state(record) != "active":
        reasons.append("memory_retention_not_active")
        status = "blocked"
        public_projection_allowed = False
    if _record_erase_state(record) != "active":
        reasons.append("memory_erase_state_blocks_projection")
        status = "blocked"
        public_projection_allowed = False
    if namespace_policy.source_authority == "memory_redact_authority":
        reasons.append("memory_redact_authority_supersedes_provider")
        status = "blocked"
        public_projection_allowed = False
    if status == "allowed" and namespace_policy.source_authority == "background_only":
        status = "background_only"
        public_projection_allowed = False

    return MemoryNamespaceDecision(
        record_id=_decision_record_ref(record.id, status=status),
        namespace_ref=record_namespace,
        status=status,
        public_projection_allowed=public_projection_allowed,
        prompt_projection_allowed=False,
        memory_write_allowed=False,
        reason_codes=tuple(dict.fromkeys(reasons)),
    )


def _decision_record_ref(record_id: str, *, status: MemoryNamespaceDecisionStatus) -> str:
    if status == "allowed":
        return record_id
    return f"memory:{hashlib.sha1(record_id.encode('utf-8')).hexdigest()[:16]}"


def _policy_reason_codes(policy: MemoryNamespacePolicy) -> tuple[str, ...]:
    reasons: list[str] = []
    if policy.memory_mode == "incognito":
        reasons.append("incognito_blocks_recall")
    if policy.source_authority == "long_term_disabled":
        reasons.append("source_authority_disables_long_term_memory")
    if policy.source_authority == "child_isolated":
        reasons.append("child_memory_scope_isolated")
    if policy.source_authority == "background_only":
        reasons.append("source_authority_background_only")
    if policy.source_authority == "memory_redact_authority":
        reasons.append("memory_redact_authority_supersedes_provider")
    if policy.redaction_state not in {"verified", "not_required"}:
        reasons.append("memory_redaction_not_verified")
    if policy.retention_state != "active":
        reasons.append("memory_retention_not_active")
    if policy.erase_state != "active":
        reasons.append("memory_erase_state_blocks_projection")
    return tuple(dict.fromkeys(reasons))


def _record_namespace_ref(record: MemoryRecord) -> str | None:
    for key in _NAMESPACE_KEYS:
        value = record.custom_metadata.get(key)
        if isinstance(value, str) and value.strip():
            return _safe_public_ref(value, prefix="memory-ns")
    return None


def _record_redaction_state(record: MemoryRecord) -> MemoryRedactionState:
    return _metadata_state(
        record.custom_metadata,
        _REDACTION_KEYS,
        allowed={"verified", "not_required", "unverified", "failed"},
        default="verified",
        unknown="unverified",
    )  # type: ignore[return-value]


def _record_retention_state(record: MemoryRecord) -> MemoryRetentionState:
    return _metadata_state(
        record.custom_metadata,
        _RETENTION_KEYS,
        allowed={"active", "expired", "suspended"},
        default="active",
        unknown="suspended",
    )  # type: ignore[return-value]


def _record_erase_state(record: MemoryRecord) -> MemoryEraseState:
    return _metadata_state(
        record.custom_metadata,
        _ERASE_KEYS,
        allowed={"active", "erased", "tombstoned"},
        default="active",
        unknown="tombstoned",
    )  # type: ignore[return-value]


def _record_is_stale(record: MemoryRecord) -> bool:
    for key in _STALE_KEYS:
        value = record.custom_metadata.get(key)
        if key in {"staleRef", "stale_ref"}:
            if value is None or value is False:
                continue
            if isinstance(value, str):
                if value.strip():
                    return True
                continue
            if isinstance(value, Mapping | tuple | list | set):
                if len(value) > 0:
                    return True
                continue
            if bool(value):
                return True
            continue
        if value is True:
            return True
        if isinstance(value, str) and value.lower() in {"true", "yes", "1", "stale"}:
            return True
    return False


def _metadata_state(
    metadata: Mapping[str, object],
    keys: tuple[str, ...],
    *,
    allowed: set[str],
    default: str,
    unknown: str,
) -> str:
    for key in keys:
        if key not in metadata:
            continue
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            normalized = value.strip().lower()
            if normalized in allowed:
                return normalized
        return unknown
    return default


def _sanitize_reason_codes(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        candidates = (value,)
    elif isinstance(value, tuple | list):
        candidates = tuple(value)
    else:
        candidates = (str(value),)
    return tuple(dict.fromkeys(_safe_reason_code(str(candidate)) for candidate in candidates))


def _safe_reason_code(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9_.:-]+", "_", value.strip().lower()).strip("_")
    if _SAFE_REASON_RE.fullmatch(normalized):
        return normalized
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]
    return f"reason:{digest}"


def _safe_public_ref(value: str, *, prefix: str) -> str:
    normalized = value.strip()
    if _SAFE_REF_RE.fullmatch(normalized) and _SENSITIVE_TEXT_RE.search(normalized) is None:
        return normalized
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}:{digest}"


def _safe_recall_public_projection(result: RecallResult) -> dict[str, object]:
    projection = result.public_projection()
    records = projection.get("records")
    if isinstance(records, list):
        safe_records = []
        for item in records:
            if not isinstance(item, Mapping):
                continue
            safe = dict(item)
            raw_id = safe.get("id")
            if isinstance(raw_id, str):
                safe["id"] = _safe_public_ref(raw_id, prefix="memory")
            source_ref = safe.get("sourceRef")
            if isinstance(source_ref, str):
                safe["sourceRef"] = _safe_public_ref(source_ref, prefix="memory")
            safe_records.append(safe)
        projection["records"] = safe_records
    return projection


__all__ = [
    "MemoryEraseState",
    "MemoryNamespaceAdmission",
    "MemoryNamespaceDecision",
    "MemoryNamespaceDecisionStatus",
    "MemoryNamespacePolicy",
    "MemoryRedactionState",
    "MemoryRetentionState",
    "admit_recall_result_to_namespace",
    "evaluate_memory_record_namespace",
]
