from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from magi_agent.transport.sse import (
    InMemorySseWriter,
    _sanitize_agent_event,
)


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
_REQUIRED_GAPS = frozenset(
    {
        "tool",
        "control",
        "child",
        "source",
        "browser",
        "artifact",
        "intermediate",
        "final",
        "error",
        "provider_fallback",
        "temporal_progress",
        "channel_delivery_absent",
    }
)
_UNSAFE_TEXT_RE = re.compile(
    r"Bearer\s+|sk-[A-Za-z0-9]|ghp_[A-Za-z0-9]|supabase-service-role|"
    r"(?:^|[\\/])(?:data[\\/]bots|workspace|var[\\/]lib[\\/]kubelet)(?:[\\/]|$)|"
    r"infra[\\/]k8s|deploy\\.sh|pythonResponseAuthority|raw secret",
    re.IGNORECASE,
)
_UNSAFE_FIXTURE_PATH_RE = re.compile(
    r"(?:^|[\\/])(?:data[\\/]bots|workspace|var[\\/]lib[\\/]kubelet)(?:[\\/]|$)|"
    r"infra[\\/]k8s|deploy\\.sh|runtime-selector|runtime_selector",
    re.IGNORECASE,
)


class Gate5B4DLiveAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    adk_runner_invoked: Literal[False] = Field(default=False, alias="adkRunnerInvoked")
    live_user_visible_streaming: Literal[False] = Field(
        default=False,
        alias="liveUserVisibleStreaming",
    )
    runtime_selector_activated: Literal[False] = Field(
        default=False,
        alias="runtimeSelectorActivated",
    )
    production_transcript_written: Literal[False] = Field(
        default=False,
        alias="productionTranscriptWritten",
    )
    production_sse_written: Literal[False] = Field(default=False, alias="productionSseWritten")
    durable_store_written: Literal[False] = Field(default=False, alias="durableStoreWritten")
    frontend_switch_enabled: Literal[False] = Field(default=False, alias="frontendSwitchEnabled")
    live_tool_dispatched: Literal[False] = Field(default=False, alias="liveToolDispatched")
    memory_provider_called: Literal[False] = Field(default=False, alias="memoryProviderCalled")
    chat_proxy_route_attached: Literal[False] = Field(
        default=False,
        alias="chatProxyRouteAttached",
    )
    telegram_attached: Literal[False] = Field(default=False, alias="telegramAttached")
    k8s_or_provisioning_touched: Literal[False] = Field(
        default=False,
        alias="k8sOrProvisioningTouched",
    )

    @field_serializer(
        "adk_runner_invoked",
        "live_user_visible_streaming",
        "runtime_selector_activated",
        "production_transcript_written",
        "production_sse_written",
        "durable_store_written",
        "frontend_switch_enabled",
        "live_tool_dispatched",
        "memory_provider_called",
        "chat_proxy_route_attached",
        "telegram_attached",
        "k8s_or_provisioning_touched",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class Gate5B4DJsonRecord(BaseModel):
    model_config = _JSON_RECORD_CONFIG

    @model_validator(mode="before")
    @classmethod
    def _validate_payload(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            raise ValueError("Gate 5B-4d stream records must be JSON objects")
        _validate_json_like(value)
        return value

    def as_dict(self) -> dict[str, object]:
        return self.model_dump(by_alias=True, mode="json", warnings=False)


class Gate5B4DCoverageEntry(BaseModel):
    model_config = _MODEL_CONFIG

    gap_id: str = Field(alias="gapId")
    adk_source: str = Field(alias="adkSource")
    safe_agent_event_target: str = Field(alias="safeAgentEventTarget")
    first_canary_posture: Literal["shadow_internal_only", "fixture_only", "absent"] = Field(
        alias="firstCanaryPosture",
    )


class Gate5B4DUtf8Chunking(BaseModel):
    model_config = _MODEL_CONFIG

    split_byte_sequences: int = Field(alias="splitByteSequences")
    replacement_characters_observed: Literal[False] = Field(
        default=False,
        alias="replacementCharactersObserved",
    )
    reassembled_text: str = Field(alias="reassembledText")


class Gate5B4DDuplicateLegacyRenderingPrevention(BaseModel):
    model_config = _MODEL_CONFIG

    agent_channel_authoritative: Literal[True] = Field(alias="agentChannelAuthoritative")
    legacy_delta_mirrored: Literal[True] = Field(alias="legacyDeltaMirrored")
    duplicate_legacy_delta_suppressed: Literal[True] = Field(
        alias="duplicateLegacyDeltaSuppressed",
    )
    visible_text_source: Literal["agent_text_delta"] = Field(alias="visibleTextSource")


class Gate5B4DActiveSnapshotBoundary(BaseModel):
    model_config = _MODEL_CONFIG

    attached: Literal[False]
    isolated: Literal[True]
    snapshot_authority: Literal[False] = Field(alias="snapshotAuthority")
    source: Literal["fixture_metadata_only"]


class Gate5B4DDurableWriteBoundary(BaseModel):
    model_config = _MODEL_CONFIG

    production_transcript_written: Literal[False] = Field(alias="productionTranscriptWritten")
    production_sse_written: Literal[False] = Field(alias="productionSseWritten")
    durable_store_written: Literal[False] = Field(alias="durableStoreWritten")
    active_snapshot_written: Literal[False] = Field(alias="activeSnapshotWritten")


class Gate5B4DTransportContract(BaseModel):
    model_config = _MODEL_CONFIG

    emit_legacy_finish: Literal[True] = Field(alias="emitLegacyFinish")
    legacy_delta_text: str = Field(default="", alias="legacyDeltaText")


class Gate5B4DStreamFixture(BaseModel):
    model_config = _MODEL_CONFIG

    schema_version: Literal["gate5b4dStreamFixtureAudit.v1"] = Field(alias="schemaVersion")
    fixture_id: str = Field(alias="fixtureId")
    recording_mode: Literal["local_diagnostic_fixture"] = Field(alias="recordingMode")
    redaction_status: Literal["verified"] = Field(alias="redactionStatus")
    local_diagnostic: Literal[True] = Field(alias="localDiagnostic")
    live_authority_flags: Gate5B4DLiveAuthorityFlags = Field(alias="liveAuthorityFlags")
    coverage_matrix: tuple[Gate5B4DCoverageEntry, ...] = Field(alias="coverageMatrix")
    agent_events: tuple[Gate5B4DJsonRecord, ...] = Field(alias="agentEvents")
    transport: Gate5B4DTransportContract
    utf8_chunking: Gate5B4DUtf8Chunking = Field(alias="utf8Chunking")
    duplicate_legacy_rendering_prevention: Gate5B4DDuplicateLegacyRenderingPrevention = Field(
        alias="duplicateLegacyRenderingPrevention",
    )
    active_snapshot_boundary: Gate5B4DActiveSnapshotBoundary = Field(
        alias="activeSnapshotBoundary",
    )
    durable_write_boundary: Gate5B4DDurableWriteBoundary = Field(alias="durableWriteBoundary")

    @model_validator(mode="before")
    @classmethod
    def _validate_json_payload(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            raise ValueError("Gate 5B-4d stream fixture must be a JSON object")
        _validate_json_like(value)
        _reject_unsafe_text(value)
        return value

    @model_validator(mode="after")
    def _validate_required_coverage(self) -> Self:
        covered = {entry.gap_id for entry in self.coverage_matrix}
        if not _REQUIRED_GAPS.issubset(covered):
            missing = sorted(_REQUIRED_GAPS - covered)
            raise ValueError(f"Gate 5B-4d stream coverage missing: {missing}")
        return self


class Gate5B4DStreamAuditResult(BaseModel):
    model_config = _MODEL_CONFIG

    fixture_id: str = Field(alias="fixtureId")
    local_diagnostic: Literal[True] = Field(alias="localDiagnostic")
    covered_gap_ids: tuple[str, ...] = Field(alias="coveredGapIds")
    missing_gap_ids: tuple[str, ...] = Field(alias="missingGapIds")
    safe_agent_event_types: tuple[str, ...] = Field(alias="safeAgentEventTypes")
    false_only_live_authority_flags: dict[str, bool] = Field(alias="falseOnlyLiveAuthorityFlags")
    sse_body: str = Field(alias="sseBody")
    transport_markers: tuple[str, ...] = Field(alias="transportMarkers")
    utf8_chunking: dict[str, object] = Field(alias="utf8Chunking")
    duplicate_legacy_rendering_prevention: dict[str, object] = Field(
        alias="duplicateLegacyRenderingPrevention",
    )
    active_snapshot_boundary: dict[str, object] = Field(alias="activeSnapshotBoundary")
    durable_write_boundary: dict[str, object] = Field(alias="durableWriteBoundary")


def load_gate5b4d_stream_fixture(
    path: str | Path,
    *,
    fixture_root: str | Path | None = None,
) -> Gate5B4DStreamFixture:
    resolved_path = _resolve_fixture_path(path, fixture_root=fixture_root)
    with resolved_path.open("r", encoding="utf-8") as fixture_file:
        payload: object = json.load(fixture_file)
    return Gate5B4DStreamFixture.model_validate(payload)


def audit_gate5b4d_stream_fixture(
    fixture: Gate5B4DStreamFixture | Mapping[str, Any],
) -> Gate5B4DStreamAuditResult:
    safe_fixture = _validated_fixture(fixture)
    safe_events = [_sanitize_agent_event(event.as_dict()) for event in safe_fixture.agent_events]
    emitted_events = [event for event in safe_events if event is not None]
    _validate_emitted_event_sequence(emitted_events, safe_fixture)

    chunks = [":ok\n\n"]
    for event in emitted_events:
        chunks.append(f"event: agent\ndata: {_json(event)}\n\n")
        if event.get("type") == "text_delta" and safe_fixture.transport.legacy_delta_text:
            legacy_delta_writer = InMemorySseWriter()
            legacy_delta_writer.legacy_delta(safe_fixture.transport.legacy_delta_text)
            chunks.append(legacy_delta_writer.body)
    if safe_fixture.transport.emit_legacy_finish:
        legacy_finish_writer = InMemorySseWriter()
        legacy_finish_writer.legacy_finish()
        chunks.append(legacy_finish_writer.body)
    sse_body = "".join(chunks)
    _reject_unsafe_text(sse_body)

    covered = sorted({entry.gap_id for entry in safe_fixture.coverage_matrix})
    missing = tuple(sorted(_REQUIRED_GAPS - set(covered)))
    event_types = tuple(
        str(event["type"])
        for event in emitted_events
        if isinstance(event.get("type"), str)
    )
    markers: list[str] = []
    if "response_clear" in event_types:
        markers.append("response_clear")
    if safe_fixture.transport.emit_legacy_finish:
        markers.extend(("legacy_finish", "[DONE]"))

    return Gate5B4DStreamAuditResult(
        fixtureId=safe_fixture.fixture_id,
        localDiagnostic=safe_fixture.local_diagnostic,
        coveredGapIds=tuple(covered),
        missingGapIds=missing,
        safeAgentEventTypes=event_types,
        falseOnlyLiveAuthorityFlags=safe_fixture.live_authority_flags.model_dump(
            by_alias=True,
            mode="json",
            warnings=False,
        ),
        sseBody=sse_body,
        transportMarkers=tuple(markers),
        utf8Chunking=safe_fixture.utf8_chunking.model_dump(
            by_alias=True,
            mode="json",
            warnings=False,
        ),
        duplicateLegacyRenderingPrevention=(
            safe_fixture.duplicate_legacy_rendering_prevention.model_dump(
                by_alias=True,
                mode="json",
                warnings=False,
            )
        ),
        activeSnapshotBoundary=safe_fixture.active_snapshot_boundary.model_dump(
            by_alias=True,
            mode="json",
            warnings=False,
        ),
        durableWriteBoundary=safe_fixture.durable_write_boundary.model_dump(
            by_alias=True,
            mode="json",
            warnings=False,
        ),
    )


def _validate_emitted_event_sequence(
    emitted_events: list[dict[str, object]],
    fixture: Gate5B4DStreamFixture,
) -> None:
    event_types = tuple(
        str(event["type"])
        for event in emitted_events
        if isinstance(event.get("type"), str)
    )
    _require_event_type(event_types, "response_clear")
    _require_event_type(event_types, "text_delta")
    _require_event_type(event_types, "turn_end")
    if not (
        event_types.index("response_clear")
        < event_types.index("text_delta")
        < event_types.index("turn_end")
    ):
        raise ValueError("Gate 5B-4d response_clear must precede replacement text and turn_end")
    if not fixture.transport.emit_legacy_finish:
        raise ValueError("Gate 5B-4d fixtures must represent [DONE] transport termination")

    control_event_types = tuple(
        nested.get("type")
        for event in emitted_events
        if event.get("type") == "control_event" and isinstance(event.get("event"), Mapping)
        for nested in (event["event"],)
        if isinstance(nested.get("type"), str)
    )
    for required_control_event in (
        "control_request_created",
        "control_request_resolved",
        "control_request_cancelled",
        "control_request_timed_out",
    ):
        if required_control_event not in control_event_types:
            raise ValueError(
                f"Gate 5B-4d control lifecycle missing {required_control_event}"
            )

    for required_child_event in ("child_started", "child_progress", "child_completed"):
        _require_event_type(event_types, required_child_event)
    for required_child_terminal in ("child_failed", "child_cancelled"):
        _require_event_type(event_types, required_child_terminal)
    for required_progress_event in ("model_fallback", "llm_progress", "heartbeat"):
        _require_event_type(event_types, required_progress_event)
    if "channel_delivery" in event_types:
        raise ValueError("Gate 5B-4d fixtures must not represent channel delivery authority")


def _require_event_type(event_types: tuple[str, ...], event_type: str) -> None:
    if event_type not in event_types:
        raise ValueError(f"Gate 5B-4d stream fixture missing {event_type}")


def _validated_fixture(
    fixture: Gate5B4DStreamFixture | Mapping[str, Any],
) -> Gate5B4DStreamFixture:
    if isinstance(fixture, Gate5B4DStreamFixture):
        return Gate5B4DStreamFixture.model_validate(
            fixture.model_dump(by_alias=True, mode="json", warnings=False)
        )
    return Gate5B4DStreamFixture.model_validate(fixture)


def _resolve_fixture_path(path: str | Path, *, fixture_root: str | Path | None) -> Path:
    _reject_unsafe_fixture_path(str(path))
    candidate = Path(path)
    if fixture_root is None:
        resolved = candidate.resolve(strict=True)
        _reject_unsafe_fixture_path(str(resolved))
        return resolved
    _reject_unsafe_fixture_path(str(fixture_root))
    resolved_root = Path(fixture_root).resolve(strict=True)
    _reject_unsafe_fixture_path(str(resolved_root))
    if not candidate.is_absolute():
        candidate = resolved_root / candidate
    resolved_candidate = candidate.resolve(strict=True)
    _reject_unsafe_fixture_path(str(resolved_candidate))
    if not resolved_candidate.is_relative_to(resolved_root):
        raise ValueError("Gate 5B-4d stream fixture path must stay under fixture_root")
    return resolved_candidate


def _reject_unsafe_fixture_path(path_text: str) -> None:
    if _UNSAFE_FIXTURE_PATH_RE.search(path_text):
        raise ValueError("Gate 5B-4d stream fixtures must be local and non-production")


def _reject_unsafe_text(value: object) -> None:
    for text in _json_string_values(value):
        if _UNSAFE_TEXT_RE.search(text):
            raise ValueError("Gate 5B-4d stream fixture contains unsafe public text")


def _json_string_values(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list | tuple):
        return tuple(item for nested in value for item in _json_string_values(nested))
    if isinstance(value, Mapping):
        return tuple(item for nested in value.values() for item in _json_string_values(nested))
    return ()


def _validate_json_like(value: object) -> None:
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int) and not isinstance(value, bool):
        return
    if isinstance(value, float):
        if math.isfinite(value):
            return
        raise ValueError("Gate 5B-4d stream payloads must be JSON-compatible")
    if isinstance(value, list | tuple):
        for item in value:
            _validate_json_like(item)
        return
    if isinstance(value, Mapping):
        for key, nested_value in value.items():
            if not isinstance(key, str):
                raise ValueError("Gate 5B-4d stream mappings must use string keys")
            _validate_json_like(nested_value)
        return
    raise ValueError("Gate 5B-4d stream payloads must be JSON-compatible")


def _json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"))


__all__ = [
    "Gate5B4DStreamAuditResult",
    "Gate5B4DStreamFixture",
    "audit_gate5b4d_stream_fixture",
    "load_gate5b4d_stream_fixture",
]
