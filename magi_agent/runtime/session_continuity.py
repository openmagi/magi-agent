from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, Self, TypeAlias

from google.adk.events import Event
from google.adk.sessions import BaseSessionService, Session
from google.genai import types
from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from magi_agent.runtime.session_continuity_projection import (
    ProjectedTranscriptEvent,
    project_transcript_entries,
    safe_memory_recall_ref,
    safe_projected_text,
)
from magi_agent.runtime.session_continuity_proof import (
    attach_session_continuity_batch_proof,
    attach_session_continuity_proof,
    has_valid_compaction_boundary,
    has_session_continuity_marker,
    latest_valid_compacted_batch,
    session_continuity_event_digest,
    session_continuity_kind,
    validate_session_continuity_proof,
)


SessionContinuityStatus: TypeAlias = Literal["skipped", "imported"]
SessionContinuityReason: TypeAlias = Literal["disabled", "committed_history_imported"]
MemoryMode: TypeAlias = Literal["normal", "read_only", "incognito"]
BudgetPolicy: TypeAlias = Literal["keep_latest"]


_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)


class _ContinuityModel(BaseModel):
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
        data = self.model_dump(by_alias=False, mode="python", warnings=False)
        if update:
            alias_to_name = {
                field.alias: name
                for name, field in self.__class__.model_fields.items()
                if field.alias is not None
            }
            data.update({alias_to_name.get(key, key): value for key, value in update.items()})
        return self.__class__.model_validate(data)


class SessionContinuityConfig(_ContinuityModel):
    enabled: bool = False
    max_imported_events: int = Field(default=128, ge=1, alias="maxImportedEvents")


class MemoryRecallProjection(_ContinuityModel):
    allowed: bool = False
    refs: tuple[str, ...] = ()
    private_payload: str | None = Field(default=None, alias="privatePayload")


class SessionContinuityPolicy(_ContinuityModel):
    memory_mode: MemoryMode = Field(default="normal", alias="memoryMode")
    recall_projection: MemoryRecallProjection | None = Field(
        default=None,
        alias="recallProjection",
    )


class SessionContinuityAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    transcript_write_allowed: Literal[False] = Field(
        default=False,
        alias="transcriptWriteAllowed",
    )
    sse_write_allowed: Literal[False] = Field(default=False, alias="sseWriteAllowed")
    db_write_allowed: Literal[False] = Field(default=False, alias="dbWriteAllowed")
    control_write_allowed: Literal[False] = Field(
        default=False,
        alias="controlWriteAllowed",
    )
    tool_host_active: Literal[False] = Field(default=False, alias="toolHostActive")
    memory_provider_active: Literal[False] = Field(
        default=False,
        alias="memoryProviderActive",
    )
    memory_write_allowed: Literal[False] = Field(
        default=False,
        alias="memoryWriteAllowed",
    )
    child_execution_allowed: Literal[False] = Field(
        default=False,
        alias="childExecutionAllowed",
    )
    workspace_mutation_allowed: Literal[False] = Field(
        default=False,
        alias="workspaceMutationAllowed",
    )
    mission_runtime_allowed: Literal[False] = Field(
        default=False,
        alias="missionRuntimeAllowed",
    )
    routing_activation_allowed: Literal[False] = Field(
        default=False,
        alias="routingActivationAllowed",
    )
    live_runner_activation_allowed: Literal[False] = Field(
        default=False,
        alias="liveRunnerActivationAllowed",
    )

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        return cls(**_false_flag_payload(cls))

    @model_validator(mode="before")
    @classmethod
    def _force_false_inputs(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        return _false_flag_payload(cls)

    @field_serializer(
        "transcript_write_allowed",
        "sse_write_allowed",
        "db_write_allowed",
        "control_write_allowed",
        "tool_host_active",
        "memory_provider_active",
        "memory_write_allowed",
        "child_execution_allowed",
        "workspace_mutation_allowed",
        "mission_runtime_allowed",
        "routing_activation_allowed",
        "live_runner_activation_allowed",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class SessionContinuityDiagnostics(_ContinuityModel):
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    budget_policy: BudgetPolicy = Field(default="keep_latest", alias="budgetPolicy")
    total_candidate_event_count: int = Field(
        default=0,
        ge=0,
        alias="totalCandidateEventCount",
    )
    dropped_for_budget_count: int = Field(
        default=0,
        ge=0,
        alias="droppedForBudgetCount",
    )
    deduplicated_import_count: int = Field(
        default=0,
        ge=0,
        alias="deduplicatedImportCount",
    )
    out_of_order_import_skipped_count: int = Field(
        default=0,
        ge=0,
        alias="outOfOrderImportSkippedCount",
    )
    replaced_import_count: int = Field(default=0, ge=0, alias="replacedImportCount")


class SessionContinuityMemoryDiagnostic(_ContinuityModel):
    mode: MemoryMode
    recall_imported: bool = Field(default=False, alias="recallImported")
    write_intent_produced: Literal[False] = Field(
        default=False,
        alias="writeIntentProduced",
    )
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")

    @field_serializer("write_intent_produced")
    def _serialize_false(self, _value: object) -> bool:
        return False


class SessionContinuityResult(_ContinuityModel):
    schema_version: Literal["priorityA.sessionContinuity.v1"] = Field(
        default="priorityA.sessionContinuity.v1",
        alias="schemaVersion",
    )
    status: SessionContinuityStatus
    reason: SessionContinuityReason
    enabled: bool
    local_only: Literal[True] = Field(default=True, alias="localOnly")
    diagnostic_only: Literal[True] = Field(default=True, alias="diagnosticOnly")
    response_authority: Literal["none"] = Field(default="none", alias="responseAuthority")
    imported_event_count: int = Field(default=0, ge=0, alias="importedEventCount")
    rejected_entry_count: int = Field(default=0, ge=0, alias="rejectedEntryCount")
    compaction_applied: bool = Field(default=False, alias="compactionApplied")
    dropped_pre_boundary_count: int = Field(
        default=0,
        ge=0,
        alias="droppedPreBoundaryCount",
    )
    budget_truncated: bool = Field(default=False, alias="budgetTruncated")
    diagnostics: SessionContinuityDiagnostics = Field(
        default_factory=SessionContinuityDiagnostics,
    )
    memory: SessionContinuityMemoryDiagnostic = Field(alias="memory")
    authority_flags: SessionContinuityAuthorityFlags = Field(
        default_factory=SessionContinuityAuthorityFlags,
        alias="authorityFlags",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_local_no_authority(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        data["schemaVersion"] = "priorityA.sessionContinuity.v1"
        data["localOnly"] = True
        data["diagnosticOnly"] = True
        data["responseAuthority"] = "none"
        data["authorityFlags"] = SessionContinuityAuthorityFlags().model_dump(
            by_alias=True,
            mode="python",
        )
        return data


class SessionContinuityBoundary:
    async def import_committed_transcript(
        self,
        session_service: BaseSessionService,
        session: Session,
        *,
        transcript_store: object,
        config: SessionContinuityConfig | None = None,
        policy: SessionContinuityPolicy | None = None,
    ) -> SessionContinuityResult:
        active_config = config or SessionContinuityConfig()
        active_policy = policy or SessionContinuityPolicy()

        if not active_config.enabled:
            return _result(
                status="skipped",
                reason="disabled",
                enabled=False,
                memory=_memory_diagnostic(active_policy, imported=False),
            )

        entries = list(transcript_store.read_committed())
        projected = project_transcript_entries(entries)
        candidate_events = [_adk_event_from_projected(event) for event in projected.events]

        memory_event, memory_diag = _project_memory_event(active_policy)
        if memory_event is not None:
            candidate_events.append(memory_event)

        total_candidate_count = len(candidate_events)
        kept_events = candidate_events[-active_config.max_imported_events :]
        kept_events = list(attach_session_continuity_batch_proof(kept_events))
        dropped_for_budget_count = total_candidate_count - len(kept_events)

        sync_result = _sync_session_continuity_events(session, kept_events)
        appendable_events = sync_result.appendable_events

        if sync_result.replacement_events is not None:
            _replace_session_continuity_events(
                session_service,
                session,
                sync_result.replacement_events,
                first_index=sync_result.replacement_index,
            )
            appendable_events = ()

        for event in appendable_events:
            await session_service.append_event(session, event)

        reason_codes = [
            *projected.reason_codes,
        ]
        if dropped_for_budget_count:
            reason_codes.append("history_budget_truncated")
        if sync_result.deduplicated_count:
            reason_codes.append("committed_history_deduplicated")
        if sync_result.replaced_count:
            reason_codes.append("committed_history_replaced")
        if sync_result.out_of_order_skipped_count:
            reason_codes.append("committed_history_out_of_order_skipped")
        if sync_result.invalid_pruned_count:
            reason_codes.append("invalid_session_continuity_marker_pruned")

        return _result(
            status="imported",
            reason="committed_history_imported",
            enabled=True,
            imported_event_count=sync_result.imported_event_count,
            rejected_entry_count=projected.rejected_count,
            compaction_applied=projected.compaction_applied,
            dropped_pre_boundary_count=projected.dropped_pre_boundary_count,
            budget_truncated=dropped_for_budget_count > 0,
            memory=memory_diag,
            diagnostics=SessionContinuityDiagnostics(
                reasonCodes=tuple(dict.fromkeys(reason_codes)),
                budgetPolicy="keep_latest",
                totalCandidateEventCount=total_candidate_count,
                droppedForBudgetCount=dropped_for_budget_count,
                deduplicatedImportCount=sync_result.deduplicated_count,
                outOfOrderImportSkippedCount=sync_result.out_of_order_skipped_count,
                replacedImportCount=sync_result.replaced_count,
            ),
        )


def _adk_event_from_projected(projected: ProjectedTranscriptEvent) -> Event:
    author = "user" if projected.role == "user" else "model"
    if projected.role == "system":
        author = "system"
    content_role = "user" if projected.role == "user" else "model"
    text = "" if projected.source == "metadata_ref" else projected.text
    return attach_session_continuity_proof(
        Event(
            author=author,
            invocation_id=projected.turn_id,
            content=types.Content(role=content_role, parts=[types.Part(text=text)]),
            custom_metadata=projected.metadata,
        )
    )


def _metadata_event(
    *,
    author: str,
    turn_id: str,
    metadata: dict[str, object],
) -> Event:
    return attach_session_continuity_proof(
        Event(
            author=author,
            invocation_id=turn_id,
            content=types.Content(role="model", parts=[types.Part(text="")]),
            custom_metadata=metadata,
        )
    )


def _event_text(event: Event) -> str:
    content = event.content
    parts = list(getattr(content, "parts", ()) or ())
    return "\n".join(
        part.text for part in parts if isinstance(getattr(part, "text", None), str)
    )


def _session_event_text_safe(event: Event) -> bool:
    kind = session_continuity_kind(event)
    text = _event_text(event)
    if kind in {
        "memory_recall_refs",
        "sanitized_control_ref",
        "sanitized_tool_ref",
    }:
        return text == ""
    return safe_projected_text(text) == text


def _valid_session_continuity_event(event: Event) -> bool:
    return (
        validate_session_continuity_proof(event)
        and _session_event_text_safe(event)
        and (
            session_continuity_kind(event) != "compaction_boundary"
            or isinstance(
                (event.custom_metadata or {}).get("openmagi.compaction"),
                Mapping,
            )
        )
    )


def _project_memory_event(
    policy: SessionContinuityPolicy,
) -> tuple[Event | None, SessionContinuityMemoryDiagnostic]:
    projection = policy.recall_projection
    reasons: list[str] = []
    if projection is None or not projection.allowed or not projection.refs:
        return None, _memory_diagnostic(policy, imported=False)

    if policy.memory_mode == "incognito":
        reasons.append("incognito_blocks_recall")
        return None, SessionContinuityMemoryDiagnostic(
            mode=policy.memory_mode,
            recallImported=False,
            writeIntentProduced=False,
            reasonCodes=tuple(reasons),
        )

    safe_refs = tuple(ref for ref in projection.refs if safe_memory_recall_ref(ref))
    if not safe_refs:
        reasons.append("unsafe_recall_refs_rejected")
        return None, SessionContinuityMemoryDiagnostic(
            mode=policy.memory_mode,
            recallImported=False,
            writeIntentProduced=False,
            reasonCodes=tuple(reasons),
        )

    event = _metadata_event(
        author="system",
        turn_id="memory-recall",
        metadata={
            "openmagi.sessionContinuity": {
                "source": "memory_recall_metadata",
                "kind": "memory_recall_refs",
            },
            "openmagi.memoryRecall": {
                "mode": policy.memory_mode,
                "refs": list(safe_refs),
            },
        },
    )
    return event, SessionContinuityMemoryDiagnostic(
        mode=policy.memory_mode,
        recallImported=True,
        writeIntentProduced=False,
        reasonCodes=(),
    )


class _SessionContinuitySyncResult(_ContinuityModel):
    appendable_events: tuple[Event, ...] = Field(default=(), alias="appendableEvents")
    replacement_events: tuple[Event, ...] | None = Field(
        default=None,
        alias="replacementEvents",
    )
    replacement_index: int = Field(default=0, ge=0, alias="replacementIndex")
    imported_event_count: int = Field(default=0, ge=0, alias="importedEventCount")
    deduplicated_count: int = Field(default=0, ge=0, alias="deduplicatedCount")
    out_of_order_skipped_count: int = Field(default=0, ge=0, alias="outOfOrderSkippedCount")
    replaced_count: int = Field(default=0, ge=0, alias="replacedCount")
    invalid_pruned_count: int = Field(default=0, ge=0, alias="invalidPrunedCount")


def _sync_session_continuity_events(
    session: Session,
    candidate_events: list[Event],
) -> _SessionContinuitySyncResult:
    marked = _marked_session_continuity_events(session)
    existing = [
        (index, event, _session_continuity_event_key(event))
        for index, event in marked
        if _valid_session_continuity_event(event)
    ]
    invalid_count = len(marked) - len(existing)
    existing_events = [event for _index, event, _key in existing]

    if not marked:
        return _SessionContinuitySyncResult(
            appendableEvents=tuple(candidate_events),
            importedEventCount=len(candidate_events),
        )

    existing_keys = [key for _index, _event, key in existing]
    candidate_keys = [_session_continuity_event_key(event) for event in candidate_events]
    existing_key_set = set(existing_keys)
    deduplicated_count = sum(1 for key in candidate_keys if key in existing_key_set)
    compacted_batch = latest_valid_compacted_batch(existing_events)
    valid_compaction_boundary_present = has_valid_compaction_boundary(existing_events)
    if valid_compaction_boundary_present and not (
        _has_compaction_event(candidate_events)
    ):
        active_key_set = {
            _session_continuity_event_key(event) for event in compacted_batch
        }
        active_deduplicated_count = sum(
            1 for key in candidate_keys if key in active_key_set
        )
        return _SessionContinuitySyncResult(
            replacementEvents=tuple(compacted_batch),
            replacementIndex=marked[0][0],
            importedEventCount=0,
            deduplicatedCount=active_deduplicated_count,
            outOfOrderSkippedCount=max(0, len(candidate_keys) - active_deduplicated_count),
            invalidPrunedCount=len(marked) - len(compacted_batch),
        )

    if not existing and invalid_count:
        return _SessionContinuitySyncResult(
            replacementEvents=tuple(candidate_events),
            replacementIndex=marked[0][0],
            importedEventCount=len(candidate_events),
            deduplicatedCount=0,
            replacedCount=invalid_count,
        )

    if existing_keys == candidate_keys and not invalid_count:
        return _SessionContinuitySyncResult(
            importedEventCount=0,
            deduplicatedCount=len(candidate_keys),
        )

    return _SessionContinuitySyncResult(
        replacementEvents=tuple(candidate_events),
        replacementIndex=marked[0][0],
        importedEventCount=len(candidate_events),
        deduplicatedCount=deduplicated_count,
        replacedCount=len(marked),
    )


def _has_compaction_event(events: list[Event]) -> bool:
    return any(_is_compaction_event(event) for event in events)


def _is_compaction_event(event: Event) -> bool:
    if not _valid_session_continuity_event(event):
        return False
    metadata = event.custom_metadata
    if not isinstance(metadata, Mapping):
        return False
    marker = metadata.get("openmagi.sessionContinuity")
    if not isinstance(marker, Mapping) or marker.get("kind") != "compaction_boundary":
        return False
    return isinstance(metadata.get("openmagi.compaction"), Mapping)


def _replace_session_continuity_events(
    session_service: BaseSessionService,
    session: Session,
    replacement_events: tuple[Event, ...],
    *,
    first_index: int,
) -> None:
    current_events = list(getattr(session, "events", ()) or ())
    rebuilt_events: list[Event] = []
    inserted = False
    for index, event in enumerate(current_events):
        if index == first_index:
            rebuilt_events.extend(replacement_events)
            inserted = True
        if has_session_continuity_marker(event):
            continue
        rebuilt_events.append(event)
    if not inserted:
        rebuilt_events.extend(replacement_events)
    session.events[:] = rebuilt_events
    _persist_replaced_session_events(session_service, session, rebuilt_events)


def _persist_replaced_session_events(
    session_service: BaseSessionService,
    session: Session,
    rebuilt_events: list[Event],
) -> None:
    stored = _stored_workspace_session(session_service, session)
    if stored is None:
        stored = _stored_adk_in_memory_session(session_service, session)
    if stored is not None and stored is not session:
        stored.events[:] = list(rebuilt_events)

    last_update_time = _latest_event_timestamp(rebuilt_events)
    if last_update_time is not None:
        session.last_update_time = last_update_time
        if stored is not None:
            stored.last_update_time = last_update_time


def _stored_workspace_session(
    session_service: BaseSessionService,
    session: Session,
) -> Session | None:
    sessions = getattr(session_service, "_sessions", None)
    if not isinstance(sessions, dict):
        return None
    stored = sessions.get((session.app_name, session.user_id, session.id))
    return stored if isinstance(stored, Session) else None


def _stored_adk_in_memory_session(
    session_service: BaseSessionService,
    session: Session,
) -> Session | None:
    sessions = getattr(session_service, "sessions", None)
    if not isinstance(sessions, dict):
        return None
    app_sessions = sessions.get(session.app_name)
    if not isinstance(app_sessions, dict):
        return None
    user_sessions = app_sessions.get(session.user_id)
    if not isinstance(user_sessions, dict):
        return None
    stored = user_sessions.get(session.id)
    return stored if isinstance(stored, Session) else None


def _latest_event_timestamp(events: list[Event]) -> float | None:
    timestamps = [event.timestamp for event in events if isinstance(event.timestamp, int | float)]
    if not timestamps:
        return None
    return max(timestamps)


def _marked_session_continuity_events(session: Session) -> list[tuple[int, Event]]:
    existing: list[tuple[int, Event]] = []
    for index, event in enumerate(list(getattr(session, "events", ()) or ())):
        if has_session_continuity_marker(event):
            existing.append((index, event))
    return existing


def _is_session_continuity_event(event: Event) -> bool:
    return _valid_session_continuity_event(event)


def _session_continuity_event_key(event: Event) -> str:
    return session_continuity_event_digest(event)


def _memory_diagnostic(
    policy: SessionContinuityPolicy,
    *,
    imported: bool,
) -> SessionContinuityMemoryDiagnostic:
    reasons: list[str] = []
    if policy.memory_mode == "incognito":
        reasons.append("incognito_blocks_recall")
    return SessionContinuityMemoryDiagnostic(
        mode=policy.memory_mode,
        recallImported=imported,
        writeIntentProduced=False,
        reasonCodes=tuple(reasons),
    )


def _false_flag_payload(cls: type[BaseModel]) -> dict[str, bool]:
    return {field.alias or name: False for name, field in cls.model_fields.items()}


def _result(
    *,
    status: SessionContinuityStatus,
    reason: SessionContinuityReason,
    enabled: bool,
    memory: SessionContinuityMemoryDiagnostic,
    imported_event_count: int = 0,
    rejected_entry_count: int = 0,
    compaction_applied: bool = False,
    dropped_pre_boundary_count: int = 0,
    budget_truncated: bool = False,
    diagnostics: SessionContinuityDiagnostics | None = None,
) -> SessionContinuityResult:
    return SessionContinuityResult(
        status=status,
        reason=reason,
        enabled=enabled,
        importedEventCount=imported_event_count,
        rejectedEntryCount=rejected_entry_count,
        compactionApplied=compaction_applied,
        droppedPreBoundaryCount=dropped_pre_boundary_count,
        budgetTruncated=budget_truncated,
        diagnostics=diagnostics or SessionContinuityDiagnostics(),
        memory=memory,
        authorityFlags=SessionContinuityAuthorityFlags(),
    )


__all__ = [
    "MemoryRecallProjection",
    "SessionContinuityAuthorityFlags",
    "SessionContinuityBoundary",
    "SessionContinuityConfig",
    "SessionContinuityDiagnostics",
    "SessionContinuityMemoryDiagnostic",
    "SessionContinuityPolicy",
    "SessionContinuityResult",
]
