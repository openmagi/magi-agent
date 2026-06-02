from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_serializer, field_validator

from openmagi_core_agent.evidence.reports import public_evidence_metadata_report
from openmagi_core_agent.evidence.types import (
    EvidenceAgentRole,
    EvidenceRecord,
    EvidenceRunOn,
    EvidenceSource,
    _freeze_mapping,
    _reject_empty_optional_string,
    _serialize_mapping,
    _validate_strict_bool,
)


SourceLedgerKind = Literal[
    "web_search",
    "web_fetch",
    "browser",
    "kb",
    "file",
    "external_repo",
    "external_doc",
    "subagent_result",
    "clock",
]
SourceTrustTier = Literal["primary", "official", "secondary", "unknown"]
SourceEvidenceType = Literal["WebSearch", "KnowledgeSearch", "SourceInspection", "Clock"]
SourceExecutionBoundary = Literal["main", "child"]

_MODEL_CONFIG = ConfigDict(
    populate_by_name=True,
    validate_default=True,
    extra="forbid",
    arbitrary_types_allowed=True,
)
_FROZEN_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    validate_default=True,
    extra="forbid",
    arbitrary_types_allowed=True,
)
_SOURCE_ID_RE = re.compile(r"^src_[1-9][0-9]*$")
_REDACTED = "[redacted]"
_RECORDED_SOURCE_EVENT_TYPE = "source_inspected"
_RECORDED_SOURCE_KIND_DEFAULT_TOOL: Mapping[str, str] = {
    "web_search": "WebSearch",
    "web_fetch": "WebFetch",
    "browser": "Browser",
    "kb": "KnowledgeSearch",
    "file": "FileRead",
    "external_repo": "ExternalSourceRead",
    "external_doc": "ExternalSourceRead",
    "subagent_result": "SpawnAgent",
    "clock": "Clock",
}
_FORBIDDEN_RECORDED_ATTACHMENT_FLAG_KEYS = frozenset(
    {
        "adkrunnerinvoked",
        "browserexecuted",
        "browserattached",
        "browserworkerattached",
        "cdpsessionattached",
        "evidenceblockenabled",
        "executionattached",
        "fetchattached",
        "livetooldispatched",
        "livetoolattached",
        "livedispatched",
        "liveexecutionattached",
        "livetrafficattached",
        "memoryprovidercalled",
        "agentmemoryimported",
        "networkfetched",
        "parentcontextinjected",
        "parentcontextrawinjection",
        "productionauthority",
        "rawbrowsersnapshotinjected",
        "rawsnapshotinjected",
        "rawtoolloginjected",
        "rawtoollogsinjected",
        "routeorapiattached",
        "runnerinvoked",
        "sourcefetched",
        "trafficattached",
        "toolhostdispatched",
        "websearchexecuted",
        "websearchattached",
    }
)
_TRUE_FLAG_STRINGS = frozenset({"1", "on", "true", "yes"})
_PUBLIC_SOURCE_DETAIL_METADATA_KEYS = frozenset(
    {
        "body",
        "content",
        "excerpt",
        "html",
        "rawbody",
        "rawcontent",
        "rawexcerpt",
        "rawhtml",
        "rawresponse",
        "rawsnippet",
        "rawsnippets",
        "rawtext",
        "rawuri",
        "rawurl",
        "responsebody",
        "snippet",
        "snippets",
        "sourceuri",
        "sourceurl",
        "text",
        "uri",
        "url",
    }
)
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
_SECRET_TEXT_RE = re.compile(
    r"(?:Bearer\s+[A-Za-z0-9._~+/=-]{8,}|gh[opusr]_[A-Za-z0-9_]{8,}|"
    r"github_pat_[A-Za-z0-9_]{8,}|AKIA[0-9A-Z]{12,}|ASIA[0-9A-Z]{12,}|"
    r"AIza[0-9A-Za-z_-]{12,}|xox[baprs]-[A-Za-z0-9-]{8,}|"
    r"sk-(?:live|test)?[-_A-Za-z0-9]{8,}|\b\d{5,}:[A-Za-z0-9_-]{8,}\b|"
    r"[A-Z0-9_]*(?:SECRET|TOKEN|KEY|PASSWORD|COOKIE)[A-Z0-9_]*\s*[:=]\s*"
    r"[^,\s}{\n]{4,})",
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


class SourceLedgerAttachmentFlags(BaseModel):
    model_config = _FROZEN_MODEL_CONFIG

    adk_runner_invoked: Literal[False] = Field(default=False, alias="adkRunnerInvoked")
    live_tool_dispatched: Literal[False] = Field(default=False, alias="liveToolDispatched")
    web_search_executed: Literal[False] = Field(default=False, alias="webSearchExecuted")
    browser_executed: Literal[False] = Field(default=False, alias="browserExecuted")
    source_fetched: Literal[False] = Field(default=False, alias="sourceFetched")
    memory_provider_called: Literal[False] = Field(default=False, alias="memoryProviderCalled")
    production_authority: Literal[False] = Field(default=False, alias="productionAuthority")
    route_or_api_attached: Literal[False] = Field(default=False, alias="routeOrApiAttached")
    evidence_block_enabled: Literal[False] = Field(default=False, alias="evidenceBlockEnabled")

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        return cls(**{name: False for name in cls.model_fields})

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        return type(self)()

    @field_validator(
        "adk_runner_invoked",
        "live_tool_dispatched",
        "web_search_executed",
        "browser_executed",
        "source_fetched",
        "memory_provider_called",
        "production_authority",
        "route_or_api_attached",
        "evidence_block_enabled",
        mode="before",
    )
    @classmethod
    def _validate_false_flags(cls, value: object, info: Any) -> object:
        _validate_strict_bool(value, info.field_name)
        if value is not False:
            raise ValueError("source ledger attachment flags must remain false")
        return value

    @field_serializer(
        "adk_runner_invoked",
        "live_tool_dispatched",
        "web_search_executed",
        "browser_executed",
        "source_fetched",
        "memory_provider_called",
        "production_authority",
        "route_or_api_attached",
        "evidence_block_enabled",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class SourceLedgerScope(BaseModel):
    model_config = _FROZEN_MODEL_CONFIG

    run_on: EvidenceRunOn = Field(default="main", alias="runOn")
    agent_role: EvidenceAgentRole = Field(default="general", alias="agentRole")
    spawn_depth: int = Field(default=0, alias="spawnDepth")
    parent_turn_id: str | None = Field(default=None, alias="parentTurnId")
    child_turn_id: str | None = Field(default=None, alias="childTurnId")
    execution_boundary: SourceExecutionBoundary = Field(
        default="main",
        alias="executionBoundary",
    )
    child_execution_attached: Literal[False] = Field(
        default=False,
        alias="childExecutionAttached",
    )

    @field_validator("spawn_depth")
    @classmethod
    def _validate_spawn_depth(cls, value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("spawnDepth must be an integer")
        if value < 0:
            raise ValueError("spawnDepth must be non-negative")
        return value

    @field_validator("parent_turn_id", "child_turn_id")
    @classmethod
    def _reject_empty_optional_ids(cls, value: str | None) -> str | None:
        return _reject_empty_optional_string(value, "source scope identifiers")

    @field_validator("child_execution_attached", mode="before")
    @classmethod
    def _validate_child_execution_flag(cls, value: object) -> object:
        _validate_strict_bool(value, "childExecutionAttached")
        if value is not False:
            raise ValueError("child execution must not be attached in source ledger audit mode")
        return value


class SourceLedgerRecord(BaseModel):
    model_config = _FROZEN_MODEL_CONFIG

    source_id: str = Field(alias="sourceId")
    turn_id: str = Field(alias="turnId")
    tool_name: str = Field(alias="toolName")
    tool_use_id: str | None = Field(default=None, alias="toolUseId")
    evidence_type: SourceEvidenceType = Field(alias="evidenceType")
    kind: SourceLedgerKind
    uri: str
    inspected_at: int | float = Field(alias="inspectedAt")
    inspected: bool = False
    title: str | None = None
    content_hash: str | None = Field(default=None, alias="contentHash")
    content_type: str | None = Field(default=None, alias="contentType")
    trust_tier: SourceTrustTier | None = Field(default=None, alias="trustTier")
    snippets: tuple[str, ...] = ()
    metadata: Mapping[str, object] = Field(default_factory=dict)
    scope: SourceLedgerScope = Field(default_factory=SourceLedgerScope)
    attachment_flags: SourceLedgerAttachmentFlags = Field(
        default_factory=SourceLedgerAttachmentFlags,
        alias="attachmentFlags",
    )

    @field_validator("source_id")
    @classmethod
    def _validate_source_id(cls, value: str) -> str:
        if _SOURCE_ID_RE.fullmatch(value) is None:
            raise ValueError("sourceId must use stable src_N metadata refs")
        return value

    @field_validator("turn_id", "tool_name", "uri")
    @classmethod
    def _reject_empty_required_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("source ledger text fields must be non-empty")
        return value

    @field_validator("tool_use_id", "title", "content_hash", "content_type")
    @classmethod
    def _reject_empty_optional_text(cls, value: str | None) -> str | None:
        return _reject_empty_optional_string(value, "source ledger optional text")

    @field_validator("inspected", mode="before")
    @classmethod
    def _validate_inspected_bool(cls, value: object) -> object:
        return _validate_strict_bool(value, "inspected")

    @field_validator("inspected_at", mode="before")
    @classmethod
    def _validate_inspected_at(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError("inspectedAt must be a finite int or float")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("inspectedAt must be a finite int or float")
        return value

    @field_validator("snippets")
    @classmethod
    def _freeze_snippets(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not snippet.strip() for snippet in value):
            raise ValueError("source ledger snippets must be non-empty")
        return tuple(value)

    @field_validator("metadata")
    @classmethod
    def _freeze_metadata(cls, value: Mapping[str, object]) -> Mapping[str, object]:
        _reject_forbidden_source_metadata_alias_keys(value)
        return _freeze_mapping(value, "metadata")

    @field_serializer("metadata")
    def _serialize_metadata(self, value: Mapping[str, object]) -> dict[str, object]:
        return _serialize_mapping(value) or {}

    def to_evidence_record(self) -> EvidenceRecord:
        source_metadata: dict[str, object] = {
            "sourceId": self.source_id,
            "sourceKind": self.kind,
            "recordedLocalOnly": True,
        }
        evidence_id = self.metadata.get("evidenceId")
        if isinstance(evidence_id, str) and evidence_id.strip():
            source_metadata["evidenceId"] = evidence_id
        return EvidenceRecord(
            type=self.evidence_type,
            status="ok" if self.inspected else "unknown",
            observedAt=self.inspected_at,
            source=EvidenceSource(
                kind="tool_trace",
                toolName=self.tool_name,
                toolCallId=self.tool_use_id,
                metadata=source_metadata,
            ),
            fields={
                "sourceId": self.source_id,
                "sourceIds": [self.source_id],
                "sourceKind": self.kind,
                "inspected": self.inspected,
            },
            preview=f"{self.evidence_type} recorded {self.source_id}",
            metadata={"publicSafeFields": ["sourceId", "sourceIds", "sourceKind", "inspected"]},
        )


def source_ledger_record_from_source_inspected_event(
    event: Mapping[str, object],
) -> SourceLedgerRecord:
    if not isinstance(event, Mapping):
        raise TypeError("recorded source event must be a mapping")
    if event.get("type") != _RECORDED_SOURCE_EVENT_TYPE:
        raise ValueError("recorded source event type must be source_inspected")
    _reject_live_attachment_claims(event)
    source = event.get("source")
    if not isinstance(source, Mapping):
        raise ValueError("recorded source_inspected event must include source metadata")
    _reject_live_attachment_claims(source)

    raw_evidence_type = _mapping_first(source, "evidenceType", "evidence_type")
    if raw_evidence_type is not None and raw_evidence_type != "SourceInspection":
        raise ValueError("source_inspected events normalize only to SourceInspection")

    source_id = _required_text(
        _mapping_first(source, "sourceId", "source_id", "id"),
        "source.sourceId",
    )
    if _SOURCE_ID_RE.fullmatch(source_id) is None:
        raise ValueError("sourceId must use stable src_N metadata refs")

    kind = _required_text(
        _mapping_first(source, "kind", "sourceKind", "sourceType", "type"),
        "source.kind",
    )
    uri = _required_text(_mapping_first(source, "uri", "url"), "source.uri")
    turn_id = _required_text(
        _mapping_first(source, "turnId", "turn_id") or event.get("turnId"),
        "source.turnId",
    )
    tool_name = _optional_text(_mapping_first(source, "toolName", "tool_name"), "source.toolName")
    if tool_name is None:
        tool_name = _RECORDED_SOURCE_KIND_DEFAULT_TOOL.get(kind, "SourceInspection")

    raw_inspected = source.get("inspected")
    if raw_inspected is not None:
        _validate_strict_bool(raw_inspected, "source.inspected")
        if raw_inspected is not True:
            raise ValueError("source_inspected events must be inspected=true")

    raw_inspected_at = _mapping_first(source, "inspectedAt", "inspected_at", "observedAt")
    if raw_inspected_at is None:
        raw_inspected_at = event.get("observedAt")

    metadata = _recorded_source_metadata(source)
    payload: dict[str, object] = {
        "sourceId": source_id,
        "turnId": turn_id,
        "toolName": tool_name,
        "evidenceType": "SourceInspection",
        "kind": kind,
        "uri": uri,
        "inspectedAt": raw_inspected_at,
        "inspected": True,
        "metadata": metadata,
        "scope": _recorded_source_scope(source, turn_id),
    }

    for source_key, payload_key in (
        ("toolUseId", "toolUseId"),
        ("tool_use_id", "toolUseId"),
        ("title", "title"),
        ("contentHash", "contentHash"),
        ("content_hash", "contentHash"),
        ("contentType", "contentType"),
        ("content_type", "contentType"),
        ("trustTier", "trustTier"),
        ("trust_tier", "trustTier"),
        ("attachmentFlags", "attachmentFlags"),
        ("attachment_flags", "attachmentFlags"),
    ):
        if source_key in source:
            payload[payload_key] = source[source_key]

    snippets = _recorded_source_snippets(source)
    if snippets:
        payload["snippets"] = snippets

    return SourceLedgerRecord.model_validate(payload)


def evidence_record_from_source_inspected_event(event: Mapping[str, object]) -> EvidenceRecord:
    return source_ledger_record_from_source_inspected_event(event).to_evidence_record()


class LocalResearchSourceLedger(BaseModel):
    model_config = _MODEL_CONFIG

    ledger_id: str = Field(alias="ledgerId")
    session_id: str = Field(alias="sessionId")
    turn_id: str = Field(alias="turnId")
    run_on: EvidenceRunOn = Field(default="main", alias="runOn")
    agent_role: EvidenceAgentRole = Field(default="general", alias="agentRole")
    spawn_depth: int = Field(default=0, alias="spawnDepth")
    attachment_flags: SourceLedgerAttachmentFlags = Field(
        default_factory=SourceLedgerAttachmentFlags,
        alias="attachmentFlags",
    )
    _records: list[SourceLedgerRecord] = PrivateAttr(default_factory=list)

    @field_validator("ledger_id", "session_id", "turn_id")
    @classmethod
    def _reject_empty_ids(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("source ledger identifiers must be non-empty")
        return value

    @field_validator("spawn_depth")
    @classmethod
    def _validate_spawn_depth(cls, value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("spawnDepth must be an integer")
        if value < 0:
            raise ValueError("spawnDepth must be non-negative")
        return value

    def record_source(self, source: Mapping[str, object]) -> SourceLedgerRecord:
        next_id = self._next_source_id()
        payload = dict(source)
        payload["sourceId"] = next_id
        payload.setdefault("inspectedAt", len(self._records) + 1)
        payload["scope"] = self._scope_payload_for(payload)
        record = SourceLedgerRecord.model_validate(payload)
        self._records.append(record)
        return _copy_record(record)

    def record_source_inspected_event(
        self,
        event: Mapping[str, object],
    ) -> SourceLedgerRecord:
        record = source_ledger_record_from_source_inspected_event(event)
        payload = record.model_dump(by_alias=True, mode="python", warnings=False)
        payload["scope"] = self._scope_payload_for(payload)
        record = SourceLedgerRecord.model_validate(payload)
        self._records.append(record)
        return _copy_record(record)

    def snapshot(self) -> tuple[SourceLedgerRecord, ...]:
        return tuple(_copy_record(record) for record in self._records)

    def sources_for_turn(self, turn_id: str) -> tuple[SourceLedgerRecord, ...]:
        if not turn_id.strip():
            raise ValueError("turn_id must be non-empty")
        child_prefix = f"{turn_id}::spawn::"
        return tuple(
            _copy_record(record)
            for record in self._records
            if record.turn_id == turn_id or record.turn_id.startswith(child_prefix)
        )

    def source_by_id(self, source_id: str) -> SourceLedgerRecord | None:
        for record in self._records:
            if record.source_id == source_id:
                return _copy_record(record)
        return None

    def _scope_payload_for(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        raw_scope = payload.get("scope")
        scope = dict(raw_scope) if isinstance(raw_scope, Mapping) else {}
        source_turn_id = payload.get("turnId")
        if not isinstance(source_turn_id, str):
            source_turn_id = payload.get("turn_id")
        is_child_turn = (
            isinstance(source_turn_id, str)
            and source_turn_id.startswith(f"{self.turn_id}::spawn::")
        )
        if is_child_turn:
            scope["runOn"] = "child"
            scope.setdefault("agentRole", self.agent_role)
            scope["spawnDepth"] = _normalized_child_spawn_depth(scope.get("spawnDepth"))
            scope["parentTurnId"] = self.turn_id
            scope["childTurnId"] = source_turn_id.removeprefix(f"{self.turn_id}::spawn::")
            scope["executionBoundary"] = "child"
        else:
            scope.setdefault("runOn", self.run_on)
            scope.setdefault("agentRole", self.agent_role)
            scope.setdefault("spawnDepth", self.spawn_depth)
            scope.setdefault("executionBoundary", "main")
        scope["childExecutionAttached"] = False
        return scope

    def _next_source_id(self) -> str:
        max_source_number = 0
        for record in self._records:
            match = _SOURCE_ID_RE.fullmatch(record.source_id)
            if match is not None:
                max_source_number = max(
                    max_source_number,
                    int(record.source_id.removeprefix("src_")),
                )
        return f"src_{max_source_number + 1}"


class PublicSourceLedgerRecordReport(BaseModel):
    model_config = _FROZEN_MODEL_CONFIG

    source_id: str = Field(alias="sourceId")
    kind: SourceLedgerKind
    evidence_type: SourceEvidenceType = Field(alias="evidenceType")
    title: str | None = None
    uri: Literal["[redacted]"] = _REDACTED
    snippets: tuple[Literal["[redacted]"], ...] = ()
    inspected: bool
    inspected_at: int | float = Field(alias="inspectedAt")
    trust_tier: SourceTrustTier | None = Field(default=None, alias="trustTier")
    scope: SourceLedgerScope
    metadata: Mapping[str, object] = Field(default_factory=dict)
    attachment_flags: SourceLedgerAttachmentFlags = Field(alias="attachmentFlags")

    @field_serializer("metadata")
    def _serialize_metadata(self, value: Mapping[str, object]) -> dict[str, object]:
        return dict(value)


class PublicSourceLedgerReport(BaseModel):
    model_config = _FROZEN_MODEL_CONFIG

    ledger_id: str = Field(alias="ledgerId")
    session_id: str = Field(alias="sessionId")
    turn_id: str = Field(alias="turnId")
    audit_only: Literal[True] = Field(default=True, alias="auditOnly")
    block_mode: Literal[False] = Field(default=False, alias="blockMode")
    final_answer_mutated: Literal[False] = Field(default=False, alias="finalAnswerMutated")
    sources: tuple[PublicSourceLedgerRecordReport, ...]
    attachment_flags: SourceLedgerAttachmentFlags = Field(alias="attachmentFlags")


def public_source_ledger_report(ledger: LocalResearchSourceLedger) -> PublicSourceLedgerReport:
    return PublicSourceLedgerReport(
        ledgerId=_public_source_identifier(ledger.ledger_id),
        sessionId=_public_source_identifier(ledger.session_id),
        turnId=_public_source_identifier(ledger.turn_id),
        sources=tuple(_public_source_record(record) for record in ledger.snapshot()),
        attachmentFlags=ledger.attachment_flags,
    )


def _public_source_record(record: SourceLedgerRecord) -> PublicSourceLedgerRecordReport:
    return PublicSourceLedgerRecordReport(
        sourceId=record.source_id,
        kind=record.kind,
        evidenceType=record.evidence_type,
        title=_public_source_title(record.title),
        uri=_REDACTED,
        snippets=tuple(_REDACTED for _ in record.snippets),
        inspected=record.inspected,
        inspectedAt=record.inspected_at,
        trustTier=record.trust_tier,
        scope=_public_source_scope(record.scope),
        metadata=_public_source_metadata(record.metadata),
        attachmentFlags=record.attachment_flags,
    )


def _public_source_title(title: str | None) -> str | None:
    if title is None:
        return None
    sanitized = public_evidence_metadata_report({"title": title}).get("title")
    if not isinstance(sanitized, str) or _contains_private_public_text(sanitized):
        return _REDACTED
    return sanitized


def _public_source_identifier(value: str) -> str:
    sanitized = public_evidence_metadata_report({"value": value}).get("value")
    if (
        not isinstance(sanitized, str)
        or sanitized != value
        or _contains_private_public_text(sanitized)
    ):
        return _REDACTED
    return sanitized


def _public_source_scope(scope: SourceLedgerScope) -> SourceLedgerScope:
    payload = scope.model_dump(by_alias=True, mode="python", warnings=False)
    for key in ("parentTurnId", "childTurnId"):
        value = payload.get(key)
        if isinstance(value, str):
            payload[key] = _public_source_identifier(value)
    return SourceLedgerScope.model_validate(payload)


def _copy_record(record: SourceLedgerRecord) -> SourceLedgerRecord:
    return SourceLedgerRecord.model_validate(
        record.model_dump(by_alias=True, mode="python", warnings=False)
    )


def _mapping_first(source: Mapping[str, object], *keys: str) -> object:
    for key in keys:
        if key in source:
            return source[key]
    return None


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _optional_text(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string when provided")
    return value


def _recorded_source_metadata(source: Mapping[str, object]) -> Mapping[str, object]:
    raw_metadata = source.get("metadata")
    if raw_metadata is None:
        metadata: dict[str, object] = {}
    elif isinstance(raw_metadata, Mapping):
        metadata = dict(raw_metadata)
    else:
        raise ValueError("source.metadata must be a mapping when provided")

    raw_evidence_id = _mapping_first(source, "evidenceId", "evidence_id")
    if raw_evidence_id is None:
        raw_evidence_id = metadata.get("evidenceId") or metadata.get("evidence_id")
    evidence_id = _optional_text(raw_evidence_id, "source.evidenceId")
    if evidence_id is not None:
        metadata["evidenceId"] = evidence_id
    metadata.pop("evidence_id", None)
    return metadata


def _recorded_source_scope(
    source: Mapping[str, object],
    turn_id: str,
) -> Mapping[str, object]:
    raw_scope = source.get("scope")
    if raw_scope is None:
        scope: dict[str, object] = {}
    elif isinstance(raw_scope, Mapping):
        scope = dict(raw_scope)
    else:
        raise ValueError("source.scope must be a mapping when provided")

    if "::spawn::" in turn_id:
        parent_turn_id, child_turn_id = turn_id.split("::spawn::", 1)
        scope["runOn"] = "child"
        scope.setdefault("agentRole", "general")
        scope["spawnDepth"] = _normalized_child_spawn_depth(scope.get("spawnDepth"))
        scope["parentTurnId"] = parent_turn_id
        scope["childTurnId"] = child_turn_id
        scope["executionBoundary"] = "child"
    else:
        scope.setdefault("runOn", "main")
        scope.setdefault("agentRole", "general")
        scope.setdefault("spawnDepth", 0)
        scope.setdefault("executionBoundary", "main")
    scope["childExecutionAttached"] = False
    return scope


def _normalized_child_spawn_depth(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 1
    return max(1, value)


def _recorded_source_snippets(source: Mapping[str, object]) -> tuple[str, ...]:
    raw_snippets = source.get("snippets")
    raw_snippet = source.get("snippet")
    if raw_snippets is not None:
        if not isinstance(raw_snippets, tuple | list):
            raise ValueError("source.snippets must be a sequence of strings")
        snippets = tuple(_required_text(item, "source.snippets") for item in raw_snippets)
        return snippets
    if raw_snippet is None:
        return ()
    return (_required_text(raw_snippet, "source.snippet"),)


def _reject_live_attachment_claims(value: object) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise ValueError("recorded source metadata keys must be strings")
            if (
                _normalized_key(key) in _FORBIDDEN_RECORDED_ATTACHMENT_FLAG_KEYS
                and _contains_truthy_recorded_attachment_flag_value(nested)
            ):
                raise ValueError("recorded source metadata must not attach live execution flags")
            _reject_live_attachment_claims(nested)
    elif isinstance(value, tuple | list):
        for item in value:
            _reject_live_attachment_claims(item)


def _reject_forbidden_source_metadata_alias_keys(value: object) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise ValueError("source ledger metadata keys must be strings")
            if _normalized_key(key) in _FORBIDDEN_RECORDED_ATTACHMENT_FLAG_KEYS:
                raise ValueError(
                    "source ledger metadata must not include live/raw attachment aliases"
                )
            _reject_forbidden_source_metadata_alias_keys(nested)
    elif isinstance(value, tuple | list):
        for item in value:
            _reject_forbidden_source_metadata_alias_keys(item)


def _contains_truthy_recorded_attachment_flag_value(value: object) -> bool:
    if _is_truthy_recorded_attachment_flag_value(value):
        return True
    if isinstance(value, Mapping):
        return any(
            _contains_truthy_recorded_attachment_flag_value(nested)
            for nested in value.values()
        )
    if isinstance(value, tuple | list):
        return any(_contains_truthy_recorded_attachment_flag_value(item) for item in value)
    return False


def _is_truthy_recorded_attachment_flag_value(value: object) -> bool:
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().casefold() in _TRUE_FLAG_STRINGS
    if isinstance(value, int | float) and not isinstance(value, bool):
        return value != 0
    return False


def _public_source_metadata(metadata: Mapping[str, object]) -> dict[str, object]:
    public_metadata = public_evidence_metadata_report(metadata)
    return _project_public_source_metadata_mapping(public_metadata)


def _project_public_source_metadata_mapping(metadata: Mapping[str, object]) -> dict[str, object]:
    projected: dict[str, object] = {}
    for key, value in metadata.items():
        if _normalized_key(key) in _FORBIDDEN_RECORDED_ATTACHMENT_FLAG_KEYS:
            continue
        projected[key] = _redact_public_source_metadata_value(key, value)
    return projected


def _redact_public_source_metadata_value(key: str, value: object) -> object:
    if _normalized_key(key) in _PUBLIC_SOURCE_DETAIL_METADATA_KEYS:
        return _REDACTED
    if isinstance(value, Mapping):
        return {
            str(nested_key): _redact_public_source_metadata_value(str(nested_key), nested_value)
            for nested_key, nested_value in value.items()
            if _normalized_key(str(nested_key)) not in _FORBIDDEN_RECORDED_ATTACHMENT_FLAG_KEYS
        }
    if isinstance(value, tuple | list):
        return [
            _redact_public_source_metadata_value(key, nested_value)
            for nested_value in value
        ]
    if isinstance(value, str) and _contains_private_public_text(value):
        return _REDACTED
    return value


def _normalized_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.casefold())


def _contains_private_public_text(value: str) -> bool:
    return bool(
        _PRIVATE_PATH_TEXT_RE.search(value)
        or _SECRET_TEXT_RE.search(value)
        or _SOURCE_LOCATOR_TEXT_RE.search(value)
    )


__all__ = [
    "LocalResearchSourceLedger",
    "PublicSourceLedgerRecordReport",
    "PublicSourceLedgerReport",
    "SourceEvidenceType",
    "SourceExecutionBoundary",
    "SourceLedgerAttachmentFlags",
    "SourceLedgerKind",
    "SourceLedgerRecord",
    "SourceLedgerScope",
    "SourceTrustTier",
    "evidence_record_from_source_inspected_event",
    "public_source_ledger_report",
    "source_ledger_record_from_source_inspected_event",
]
