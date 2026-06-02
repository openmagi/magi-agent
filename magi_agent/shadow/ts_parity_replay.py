from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_serializer, model_validator

from magi_agent.runtime.control import (
    ControlRequestCancelledEvent,
    ControlRequestCreatedEvent,
    ControlRequestResolvedEvent,
    ControlRequestTimedOutEvent,
    PermissionDecisionControlEvent,
)
from magi_agent.runtime.transcript import (
    CompactionBoundaryEntry,
    ToolCallEntry,
    ToolResultEntry,
    TranscriptEntry,
)
from magi_agent.transport.sse import InMemorySseWriter


_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_JSON_RECORD_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="allow",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_TRANSCRIPT_ENTRY_ADAPTER = TypeAdapter(TranscriptEntry)
_PRODUCTION_PATH_RE = re.compile(
    r"(?:^|[\\/])(?:data[\\/]bots|workspace|var[\\/]lib[\\/]kubelet)(?:[\\/]|$)|"
    r"pvc|supabase://|s3://|gs://|postgres(?:ql)?://|"
    r"infra[\\/]k8s|infra[\\/]docker[\\/]provisioning-worker|deploy(?:ment)?[\\/]|"
    r"deploy\\.sh|runtime-selector|runtime_selector|telegram|canary",
    re.IGNORECASE,
)


ControlEvent = (
    PermissionDecisionControlEvent
    | ControlRequestCreatedEvent
    | ControlRequestResolvedEvent
    | ControlRequestCancelledEvent
    | ControlRequestTimedOutEvent
)


class TsParityReplayAttachmentFlags(BaseModel):
    model_config = _MODEL_CONFIG

    adk_runner_invoked: Literal[False] = Field(default=False, alias="adkRunnerInvoked")
    live_tool_dispatched: Literal[False] = Field(default=False, alias="liveToolDispatched")
    shell_or_code_executed: Literal[False] = Field(
        default=False,
        alias="shellOrCodeExecuted",
    )
    memory_provider_called: Literal[False] = Field(
        default=False,
        alias="memoryProviderCalled",
    )
    agent_memory_imported: Literal[False] = Field(default=False, alias="agentMemoryImported")
    hipocampus_qmd_live_called: Literal[False] = Field(
        default=False,
        alias="hipocampusQmdLiveCalled",
    )
    production_storage_written: Literal[False] = Field(
        default=False,
        alias="productionStorageWritten",
    )
    route_or_api_attached: Literal[False] = Field(default=False, alias="routeOrApiAttached")
    telegram_attached: Literal[False] = Field(default=False, alias="telegramAttached")
    canary_traffic_attached: Literal[False] = Field(
        default=False,
        alias="canaryTrafficAttached",
    )
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
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            data.update(dict(update))
        return type(self).model_validate(data)

    @field_serializer(
        "adk_runner_invoked",
        "live_tool_dispatched",
        "shell_or_code_executed",
        "memory_provider_called",
        "agent_memory_imported",
        "hipocampus_qmd_live_called",
        "production_storage_written",
        "route_or_api_attached",
        "telegram_attached",
        "canary_traffic_attached",
        "evidence_block_enabled",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class TsParityJsonRecord(BaseModel):
    model_config = _JSON_RECORD_CONFIG

    @model_validator(mode="before")
    @classmethod
    def _validate_payload(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            raise ValueError("TS parity replay records must be JSON objects")
        _validate_json_like(value)
        return value

    def as_dict(self) -> dict[str, object]:
        return self.model_dump(by_alias=True, mode="json", warnings=False)


class TsParityMemoryFence(BaseModel):
    model_config = _MODEL_CONFIG

    fence_id: str = Field(alias="fenceId")
    memory_mode: Literal["normal", "read_only", "incognito"] = Field(alias="memoryMode")
    source_authority: Literal[
        "current_turn_over_memory",
        "memory_disabled",
        "memory_background_only",
    ] = Field(alias="sourceAuthority")
    recall_claimed: Literal[False] = Field(default=False, alias="recallClaimed")
    write_claimed: Literal[False] = Field(default=False, alias="writeClaimed")
    provider_call_made: Literal[False] = Field(default=False, alias="providerCallMade")


class TsParityCompactionBoundary(BaseModel):
    model_config = _MODEL_CONFIG

    boundary_id: str = Field(alias="boundaryId")
    transcript_turn_id: str = Field(alias="transcriptTurnId")
    transcript_kind: Literal["compaction_boundary"] = Field(alias="transcriptKind")
    summary_hash: str | None = Field(default=None, alias="summaryHash")


class TsParitySourceAuthority(BaseModel):
    model_config = _MODEL_CONFIG

    policy: Literal["current_turn_over_memory", "memory_disabled", "memory_background_only"]
    source_ids: tuple[str, ...] = Field(default=(), alias="sourceIds")
    memory_may_override_current_turn: Literal[False] = Field(
        default=False,
        alias="memoryMayOverrideCurrentTurn",
    )


class TsParityReplayFixture(BaseModel):
    model_config = _MODEL_CONFIG

    schema_version: Literal["tsParityReplayFixture.v1"] = Field(alias="schemaVersion")
    fixture_id: str = Field(alias="fixtureId")
    source_runtime: Literal["typescript-core-agent"] = Field(alias="sourceRuntime")
    recording_mode: Literal["local_diagnostic_fixture"] = Field(alias="recordingMode")
    redaction_status: Literal["verified"] = Field(alias="redactionStatus")
    local_diagnostic: Literal[True] = Field(alias="localDiagnostic")
    attachment_flags: TsParityReplayAttachmentFlags = Field(alias="attachmentFlags")
    transcript_entries: tuple[TranscriptEntry, ...] = Field(alias="transcriptEntries")
    agent_events: tuple[TsParityJsonRecord, ...] = Field(default=(), alias="agentEvents")
    control_events: tuple[ControlEvent, ...] = Field(default=(), alias="controlEvents")
    memory_fences: tuple[TsParityMemoryFence, ...] = Field(alias="memoryFences")
    compaction_boundaries: tuple[TsParityCompactionBoundary, ...] = Field(
        alias="compactionBoundaries",
    )
    source_authority: TsParitySourceAuthority = Field(alias="sourceAuthority")

    @model_validator(mode="before")
    @classmethod
    def _coerce_nested_records(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        _validate_json_like(value)
        coerced = dict(value)
        if "transcriptEntries" in coerced:
            coerced["transcriptEntries"] = tuple(
                _TRANSCRIPT_ENTRY_ADAPTER.validate_python(entry)
                for entry in _as_sequence(coerced["transcriptEntries"])
            )
        if "controlEvents" in coerced:
            coerced["controlEvents"] = tuple(
                _validate_control_event(event) for event in _as_sequence(coerced["controlEvents"])
            )
        return coerced

    @model_validator(mode="after")
    def _validate_fixture_boundary(self) -> Self:
        _require_compaction_boundaries_referenced(
            transcript_entries=self.transcript_entries,
            compaction_boundaries=self.compaction_boundaries,
        )
        _require_tool_result_links(self.transcript_entries)
        _require_control_lifecycle(self.control_events)
        return self


class TsParityReplayResult(BaseModel):
    model_config = _MODEL_CONFIG

    fixture_id: str = Field(alias="fixtureId")
    local_diagnostic: Literal[True] = Field(default=True, alias="localDiagnostic")
    attachment_flags: TsParityReplayAttachmentFlags = Field(alias="attachmentFlags")
    transcript_event_ids: tuple[str, ...] = Field(alias="transcriptEventIds")
    transcript_kinds: tuple[str, ...] = Field(alias="transcriptKinds")
    tool_links: dict[str, tuple[str, str]] = Field(alias="toolLinks")
    control_lifecycle: dict[str, tuple[str, str]] = Field(alias="controlLifecycle")
    sse_body: str = Field(alias="sseBody")
    memory_modes: tuple[Literal["normal", "read_only", "incognito"], ...] = Field(
        alias="memoryModes",
    )
    source_authority_policy: str = Field(alias="sourceAuthorityPolicy")
    compaction_boundary_ids: tuple[str, ...] = Field(alias="compactionBoundaryIds")
    no_false_memory_claims: Literal[True] = Field(alias="noFalseMemoryClaims")


def load_ts_parity_replay_fixture(
    path: str | Path,
    *,
    fixture_root: str | Path | None = None,
) -> TsParityReplayFixture:
    resolved_path = _resolve_fixture_path(path, fixture_root=fixture_root)
    with resolved_path.open("r", encoding="utf-8") as fixture_file:
        payload: object = json.load(fixture_file)
    return TsParityReplayFixture.model_validate(payload)


def replay_ts_parity_fixture(
    fixture: TsParityReplayFixture | Mapping[str, Any],
) -> TsParityReplayResult:
    safe_fixture = _validated_fixture_snapshot(fixture)
    writer = InMemorySseWriter()
    writer.start()
    for event in safe_fixture.agent_events:
        writer.agent(event.as_dict())

    return TsParityReplayResult(
        fixtureId=safe_fixture.fixture_id,
        attachmentFlags=safe_fixture.attachment_flags,
        transcriptEventIds=_transcript_event_ids(safe_fixture.transcript_entries),
        transcriptKinds=tuple(entry.kind for entry in safe_fixture.transcript_entries),
        toolLinks=_tool_links(safe_fixture.transcript_entries),
        controlLifecycle=_control_lifecycle(safe_fixture.control_events),
        sseBody=writer.body,
        memoryModes=tuple(fence.memory_mode for fence in safe_fixture.memory_fences),
        sourceAuthorityPolicy=safe_fixture.source_authority.policy,
        compactionBoundaryIds=tuple(
            boundary.boundary_id for boundary in safe_fixture.compaction_boundaries
        ),
        noFalseMemoryClaims=_no_false_memory_claims(safe_fixture.memory_fences),
    )


def _validated_fixture_snapshot(
    fixture: TsParityReplayFixture | Mapping[str, Any],
) -> TsParityReplayFixture:
    if isinstance(fixture, TsParityReplayFixture):
        return TsParityReplayFixture.model_validate(
            fixture.model_dump(by_alias=True, mode="json", warnings=False)
        )
    return TsParityReplayFixture.model_validate(fixture)


def _validate_control_event(event: object) -> ControlEvent:
    if not isinstance(event, Mapping):
        raise ValueError("controlEvents must contain JSON objects")
    event_type = event.get("type")
    if event_type == "permission_decision":
        return PermissionDecisionControlEvent.model_validate(event)
    if event_type == "control_request_created":
        return ControlRequestCreatedEvent.model_validate(event)
    if event_type == "control_request_resolved":
        return ControlRequestResolvedEvent.model_validate(event)
    if event_type == "control_request_cancelled":
        return ControlRequestCancelledEvent.model_validate(event)
    if event_type == "control_request_timed_out":
        return ControlRequestTimedOutEvent.model_validate(event)
    raise ValueError("unsupported TS parity control event type")


def _transcript_event_ids(entries: tuple[TranscriptEntry, ...]) -> tuple[str, ...]:
    ids: list[str] = []
    for entry in entries:
        if isinstance(entry, ToolCallEntry | ToolResultEntry):
            ids.append(entry.tool_use_id)
        elif isinstance(entry, CompactionBoundaryEntry):
            ids.append(entry.boundary_id)
        else:
            turn_id = getattr(entry, "turn_id", None)
            if isinstance(turn_id, str) and turn_id.strip():
                ids.append(turn_id)
    return tuple(ids)


def _tool_links(entries: tuple[TranscriptEntry, ...]) -> dict[str, tuple[str, str]]:
    states: dict[str, list[str]] = {}
    for entry in entries:
        if isinstance(entry, ToolCallEntry):
            states.setdefault(entry.tool_use_id, []).append("tool_call")
        if isinstance(entry, ToolResultEntry):
            states.setdefault(entry.tool_use_id, []).append("tool_result")
    return {
        tool_use_id: (state[0], state[-1])
        for tool_use_id, state in states.items()
        if "tool_call" in state and "tool_result" in state
    }


def _control_lifecycle(events: tuple[ControlEvent, ...]) -> dict[str, tuple[str, str]]:
    lifecycle: dict[str, list[str]] = {}
    for event in events:
        if isinstance(event, ControlRequestCreatedEvent):
            lifecycle.setdefault(event.request.request_id, []).append("created")
        elif isinstance(event, ControlRequestResolvedEvent):
            lifecycle.setdefault(event.request_id, []).append(event.decision)
        elif isinstance(event, ControlRequestCancelledEvent):
            lifecycle.setdefault(event.request_id, []).append("cancelled")
        elif isinstance(event, ControlRequestTimedOutEvent):
            lifecycle.setdefault(event.request_id, []).append("timed_out")
    return {
        request_id: (states[0], states[-1])
        for request_id, states in lifecycle.items()
        if len(states) >= 2
    }


def _require_tool_result_links(entries: tuple[TranscriptEntry, ...]) -> None:
    states: dict[str, set[str]] = {}
    for entry in entries:
        if isinstance(entry, ToolCallEntry):
            states.setdefault(entry.tool_use_id, set()).add("tool_call")
        elif isinstance(entry, ToolResultEntry):
            states.setdefault(entry.tool_use_id, set()).add("tool_result")
    for tool_use_id, state in states.items():
        if state != {"tool_call", "tool_result"}:
            raise ValueError(f"tool call/result linkage missing for {tool_use_id}")


def _require_control_lifecycle(events: tuple[ControlEvent, ...]) -> None:
    if not events:
        return
    lifecycle = _control_lifecycle(events)
    if not lifecycle:
        raise ValueError("control request lifecycle metadata must include terminal state")


def _require_compaction_boundaries_referenced(
    *,
    transcript_entries: tuple[TranscriptEntry, ...],
    compaction_boundaries: tuple[TsParityCompactionBoundary, ...],
) -> None:
    transcript_boundary_ids = {
        entry.boundary_id
        for entry in transcript_entries
        if isinstance(entry, CompactionBoundaryEntry)
    }
    declared_boundary_ids = {boundary.boundary_id for boundary in compaction_boundaries}
    if declared_boundary_ids != transcript_boundary_ids:
        raise ValueError("compaction boundary metadata must match transcript entries")


def _no_false_memory_claims(fences: tuple[TsParityMemoryFence, ...]) -> Literal[True]:
    if all(
        fence.recall_claimed is False
        and fence.write_claimed is False
        and fence.provider_call_made is False
        for fence in fences
    ):
        return True
    raise ValueError("TS parity memory fences must not claim live recall or writes")


def _resolve_fixture_path(path: str | Path, *, fixture_root: str | Path | None) -> Path:
    _reject_unsafe_path_text(str(path))
    candidate = Path(path)
    if fixture_root is None:
        resolved = candidate.resolve(strict=True)
        _reject_unsafe_path_text(str(resolved))
        return resolved
    _reject_unsafe_path_text(str(fixture_root))
    resolved_root = Path(fixture_root).resolve(strict=True)
    _reject_unsafe_path_text(str(resolved_root))
    if not candidate.is_absolute():
        candidate = resolved_root / candidate
    resolved_candidate = candidate.resolve(strict=True)
    _reject_unsafe_path_text(str(resolved_candidate))
    if not resolved_candidate.is_relative_to(resolved_root):
        raise ValueError("TS parity replay fixture path must stay under fixture_root")
    return resolved_candidate


def _reject_unsafe_path_text(path_text: str) -> None:
    if _PRODUCTION_PATH_RE.search(path_text):
        raise ValueError("TS parity replay fixtures must be local and non-production")


def _as_sequence(value: object) -> tuple[object, ...]:
    if isinstance(value, list | tuple):
        return tuple(value)
    raise ValueError("TS parity replay fixture field must be an array")


def _validate_json_like(value: object) -> None:
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int) and not isinstance(value, bool):
        return
    if isinstance(value, float):
        if math.isfinite(value):
            return
        raise ValueError("TS parity replay payloads must be JSON-compatible")
    if isinstance(value, list | tuple):
        for item in value:
            _validate_json_like(item)
        return
    if isinstance(value, Mapping):
        for key, nested_value in value.items():
            if not isinstance(key, str):
                raise ValueError("TS parity replay mappings must use string keys")
            _validate_json_like(nested_value)
        return
    raise ValueError("TS parity replay payloads must be JSON-compatible")


__all__ = [
    "TsParityCompactionBoundary",
    "TsParityMemoryFence",
    "TsParityReplayAttachmentFlags",
    "TsParityReplayFixture",
    "TsParityReplayResult",
    "TsParitySourceAuthority",
    "load_ts_parity_replay_fixture",
    "replay_ts_parity_fixture",
]
