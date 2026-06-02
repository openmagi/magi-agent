from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from magi_agent.runtime.query_state import (
    QueryState,
    validate_digest,
    validate_safe_ref,
)


_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    arbitrary_types_allowed=True,
    hide_input_in_errors=True,
)


class ContextLifecycleAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    live_model_call_allowed: Literal[False] = Field(
        default=False,
        alias="liveModelCallAllowed",
    )
    tool_execution_allowed: Literal[False] = Field(
        default=False,
        alias="toolExecutionAllowed",
    )
    memory_provider_call_allowed: Literal[False] = Field(
        default=False,
        alias="memoryProviderCallAllowed",
    )
    memory_write_allowed: Literal[False] = Field(default=False, alias="memoryWriteAllowed")
    production_transcript_write_allowed: Literal[False] = Field(
        default=False,
        alias="productionTranscriptWriteAllowed",
    )
    user_visible_output_allowed: Literal[False] = Field(
        default=False,
        alias="userVisibleOutputAllowed",
    )
    live_attachment_allowed: Literal[False] = Field(
        default=False,
        alias="liveAttachmentAllowed",
    )
    side_effects_allowed: Literal[False] = Field(default=False, alias="sideEffectsAllowed")

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: object,
    ) -> Self:
        return cls(**_false_flag_payload(cls))

    def model_copy(
        self,
        *,
        update: Mapping[str, object] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = deep
        return self.__class__()

    @model_validator(mode="before")
    @classmethod
    def _force_false_inputs(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        return _false_flag_payload(cls)

    @field_serializer(
        "live_model_call_allowed",
        "tool_execution_allowed",
        "memory_provider_call_allowed",
        "memory_write_allowed",
        "production_transcript_write_allowed",
        "user_visible_output_allowed",
        "live_attachment_allowed",
        "side_effects_allowed",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class ContextLifecycleConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    local_fake_compaction_enabled: bool = Field(
        default=False,
        alias="localFakeCompactionEnabled",
    )
    token_estimate_threshold: int = Field(
        default=24_000,
        ge=1,
        alias="tokenEstimateThreshold",
    )
    event_count_threshold: int = Field(default=128, ge=1, alias="eventCountThreshold")
    recent_event_count: int = Field(default=16, ge=1, alias="recentEventCount")


class ContextLifecycleEvent(BaseModel):
    model_config = _MODEL_CONFIG

    event_ref: str = Field(alias="eventRef")
    token_estimate: int = Field(default=0, ge=0, alias="tokenEstimate")
    content_ref: str | None = Field(default=None, alias="contentRef")

    @field_validator("event_ref", "content_ref")
    @classmethod
    def _validate_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_safe_ref(value)


class ContextLifecycleDiagnostics(BaseModel):
    model_config = _MODEL_CONFIG

    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    token_estimate: int = Field(default=0, ge=0, alias="tokenEstimate")
    token_estimate_threshold: int = Field(default=0, ge=0, alias="tokenEstimateThreshold")
    event_count: int = Field(default=0, ge=0, alias="eventCount")
    event_count_threshold: int = Field(default=0, ge=0, alias="eventCountThreshold")


class ContextCompactionDecision(BaseModel):
    model_config = _MODEL_CONFIG

    status: Literal["skipped", "unchanged", "blocked", "compacted"]
    compaction_applied: bool = Field(default=False, alias="compactionApplied")
    threshold_breaches: tuple[str, ...] = Field(default=(), alias="thresholdBreaches")
    truncated_event_count: int = Field(default=0, ge=0, alias="truncatedEventCount")
    state: QueryState
    diagnostics: ContextLifecycleDiagnostics
    authority_flags: ContextLifecycleAuthorityFlags = Field(
        default_factory=ContextLifecycleAuthorityFlags,
        alias="authorityFlags",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_authority(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        data["authorityFlags"] = ContextLifecycleAuthorityFlags().model_dump(by_alias=True)
        return data

    def public_projection(self) -> dict[str, object]:
        return self.model_dump(by_alias=True, mode="python")

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: object,
    ) -> Self:
        _ = _fields_set
        values["authorityFlags"] = ContextLifecycleAuthorityFlags().model_dump(by_alias=True)
        values.pop("authority_flags", None)
        return cls.model_validate(values)

    def model_copy(
        self,
        *,
        update: Mapping[str, object] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            data.update(_alias_update(type(self), update))
        data["authorityFlags"] = ContextLifecycleAuthorityFlags().model_dump(by_alias=True)
        _ = deep
        return type(self).model_validate(data)


class RestoreContextRequest(BaseModel):
    model_config = _MODEL_CONFIG

    state: QueryState
    approved_summary_ref: str = Field(alias="approvedSummaryRef")
    approved_summary_digest: str = Field(alias="approvedSummaryDigest")
    recent_event_refs: tuple[str, ...] = Field(default=(), alias="recentEventRefs")

    @field_validator("approved_summary_ref")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        return validate_safe_ref(value)

    @field_validator("approved_summary_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return validate_digest(value)

    @field_validator("recent_event_refs")
    @classmethod
    def _validate_recent_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(validate_safe_ref(item) for item in value))


class ContextRestoreResult(BaseModel):
    model_config = _MODEL_CONFIG

    status: Literal["blocked", "restored"]
    state: QueryState
    context_refs: tuple[str, ...] = Field(default=(), alias="contextRefs")
    diagnostics: ContextLifecycleDiagnostics
    authority_flags: ContextLifecycleAuthorityFlags = Field(
        default_factory=ContextLifecycleAuthorityFlags,
        alias="authorityFlags",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_authority(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        data["authorityFlags"] = ContextLifecycleAuthorityFlags().model_dump(by_alias=True)
        return data

    def final_answer_context(self) -> dict[str, object]:
        projection = self.state.public_projection()
        projection["contextRefs"] = list(self.context_refs)
        return projection

    def public_projection(self) -> dict[str, object]:
        return self.model_dump(by_alias=True, mode="python")

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: object,
    ) -> Self:
        _ = _fields_set
        values["authorityFlags"] = ContextLifecycleAuthorityFlags().model_dump(by_alias=True)
        values.pop("authority_flags", None)
        return cls.model_validate(values)

    def model_copy(
        self,
        *,
        update: Mapping[str, object] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            data.update(_alias_update(type(self), update))
        data["authorityFlags"] = ContextLifecycleAuthorityFlags().model_dump(by_alias=True)
        _ = deep
        return type(self).model_validate(data)


class ContextLifecycleBoundary:
    async def compact_if_needed(
        self,
        *,
        session_service: Any,
        session: Any,
        state: QueryState,
        events: Sequence[ContextLifecycleEvent],
        approvedSummaryRef: str,
        approvedSummaryDigest: str,
        config: ContextLifecycleConfig | None = None,
    ) -> ContextCompactionDecision:
        _ = (session_service, session)
        active_config = config or ContextLifecycleConfig()
        diagnostics = _diagnostics(
            reason_codes=(),
            token_estimate=_token_estimate(events),
            token_threshold=active_config.token_estimate_threshold,
            event_count=len(events),
            event_threshold=active_config.event_count_threshold,
        )

        if not active_config.enabled or not active_config.local_fake_compaction_enabled:
            return ContextCompactionDecision(
                status="skipped",
                compactionApplied=False,
                state=state,
                diagnostics=diagnostics.model_copy(
                    update={"reason_codes": ("context_lifecycle_disabled",)}
                ),
            )
        if getattr(session_service, "openmagi_local_fake_provider", False) is not True:
            return ContextCompactionDecision(
                status="blocked",
                compactionApplied=False,
                state=state,
                diagnostics=diagnostics.model_copy(
                    update={"reason_codes": ("local_fake_session_service_required",)}
                ),
            )
        if getattr(session, "id", None) != state.session_id:
            return ContextCompactionDecision(
                status="blocked",
                compactionApplied=False,
                state=state,
                diagnostics=diagnostics.model_copy(
                    update={"reason_codes": ("session_id_mismatch",)}
                ),
            )

        summary_ref = validate_safe_ref(approvedSummaryRef)
        summary_digest = validate_digest(approvedSummaryDigest)
        breaches = _threshold_breaches(events, active_config)
        if not breaches:
            return ContextCompactionDecision(
                status="unchanged",
                compactionApplied=False,
                state=state,
                diagnostics=diagnostics.model_copy(update={"reason_codes": ("thresholds_not_breached",)}),
            )

        recent_refs = tuple(event.event_ref for event in events[-active_config.recent_event_count :])
        truncated_count = max(0, len(events) - len(recent_refs))
        reason_codes = [*breaches, "compaction_applied"]
        if truncated_count:
            reason_codes.append("pre_boundary_truncated")
        compacted_state = state.model_copy(
            update={
                "compacted_transcript_summary_ref": summary_ref,
                "compacted_transcript_digest": summary_digest,
                "recent_event_refs": recent_refs,
            }
        )
        restore_provenance_digest = _restore_provenance_digest(compacted_state)
        compacted_state = compacted_state.model_copy(
            update={"restore_provenance_digest": restore_provenance_digest}
        )
        await session_service.append_event(
            session,
            _compaction_provenance_event(compacted_state, restore_provenance_digest),
        )
        return ContextCompactionDecision(
            status="compacted",
            compactionApplied=True,
            thresholdBreaches=breaches,
            truncatedEventCount=truncated_count,
            state=compacted_state,
            diagnostics=diagnostics.model_copy(update={"reason_codes": tuple(reason_codes)}),
        )

    async def restore_context(
        self,
        *,
        session_service: Any,
        session: Any,
        request: RestoreContextRequest,
    ) -> ContextRestoreResult:
        diagnostics = _diagnostics(
            reason_codes=(),
            token_estimate=0,
            token_threshold=0,
            event_count=len(request.recent_event_refs),
            event_threshold=0,
        )
        if request.state.compacted_transcript_summary_ref != request.approved_summary_ref:
            return ContextRestoreResult(
                status="blocked",
                state=request.state,
                diagnostics=diagnostics.model_copy(
                    update={"reason_codes": ("approved_summary_ref_mismatch",)}
                ),
            )
        if request.state.compacted_transcript_digest != request.approved_summary_digest:
            return ContextRestoreResult(
                status="blocked",
                state=request.state,
                diagnostics=diagnostics.model_copy(
                    update={"reason_codes": ("approved_summary_digest_mismatch",)}
                ),
            )
        if tuple(request.recent_event_refs) != tuple(request.state.recent_event_refs):
            return ContextRestoreResult(
                status="blocked",
                state=request.state,
                diagnostics=diagnostics.model_copy(
                    update={"reason_codes": ("recent_event_refs_mismatch",)}
                ),
            )
        if getattr(session, "id", None) != request.state.session_id:
            return ContextRestoreResult(
                status="blocked",
                state=request.state,
                diagnostics=diagnostics.model_copy(
                    update={"reason_codes": ("session_id_mismatch",)}
                ),
            )
        if getattr(session_service, "openmagi_local_fake_provider", False) is not True:
            return ContextRestoreResult(
                status="blocked",
                state=request.state,
                diagnostics=diagnostics.model_copy(
                    update={"reason_codes": ("local_fake_session_service_required",)}
                ),
            )
        expected_provenance = _restore_provenance_digest(request.state)
        if request.state.restore_provenance_digest != expected_provenance:
            return ContextRestoreResult(
                status="blocked",
                state=request.state,
                diagnostics=diagnostics.model_copy(
                    update={"reason_codes": ("restore_provenance_missing",)}
                ),
            )
        if not _session_has_compaction_provenance(session, expected_provenance):
            return ContextRestoreResult(
                status="blocked",
                state=request.state,
                diagnostics=diagnostics.model_copy(
                    update={"reason_codes": ("restore_provenance_missing",)}
                ),
            )

        context_refs = _ordered_refs(
            (
                request.approved_summary_ref,
                *request.recent_event_refs,
                *request.state.outstanding_control_request_refs,
                *request.state.pending_tool_result_refs,
                *request.state.child_agent_summary_refs,
                *request.state.child_agent_evidence_refs,
                *request.state.verification_evidence_refs,
                *request.state.model_context_config_refs,
                *request.state.cache_safe_param_refs,
            )
        )
        result = ContextRestoreResult(
            status="restored",
            state=request.state,
            contextRefs=context_refs,
            diagnostics=diagnostics.model_copy(update={"reason_codes": ("context_restored",)}),
        )
        await session_service.append_event(session, _restore_metadata_event(result))
        return result


def _restore_metadata_event(result: ContextRestoreResult) -> object:
    from google.adk.events import Event

    return Event(
        author="system",
        invocation_id=result.state.current_turn_id,
        custom_metadata={
            "openmagi.contextLifecycle": {
                "kind": "compacted_restore_refs",
                "contextRefs": list(result.context_refs),
                "authorityFlags": result.authority_flags.model_dump(by_alias=True),
            }
        },
    )


def _compaction_provenance_event(state: QueryState, provenance_digest: str) -> object:
    from google.adk.events import Event

    return Event(
        author="system",
        invocation_id=state.current_turn_id,
        custom_metadata={
            "openmagi.contextLifecycle": {
                "kind": "compacted_state_provenance",
                "sessionId": state.session_id,
                "restoreProvenanceDigest": provenance_digest,
                "authorityFlags": ContextLifecycleAuthorityFlags().model_dump(by_alias=True),
            }
        },
    )


def _session_has_compaction_provenance(session: object, provenance_digest: str) -> bool:
    for event in getattr(session, "events", ()):
        metadata = getattr(event, "custom_metadata", None)
        if not isinstance(metadata, Mapping):
            continue
        lifecycle = metadata.get("openmagi.contextLifecycle")
        if not isinstance(lifecycle, Mapping):
            continue
        if (
            lifecycle.get("kind") == "compacted_state_provenance"
            and lifecycle.get("restoreProvenanceDigest") == provenance_digest
        ):
            return True
    return False


def _threshold_breaches(
    events: Sequence[ContextLifecycleEvent],
    config: ContextLifecycleConfig,
) -> tuple[str, ...]:
    breaches: list[str] = []
    if _token_estimate(events) > config.token_estimate_threshold:
        breaches.append("token_estimate_threshold_breached")
    if len(events) > config.event_count_threshold:
        breaches.append("event_count_threshold_breached")
    return tuple(breaches)


def _token_estimate(events: Sequence[ContextLifecycleEvent]) -> int:
    return sum(event.token_estimate for event in events)


def _diagnostics(
    *,
    reason_codes: tuple[str, ...],
    token_estimate: int,
    token_threshold: int,
    event_count: int,
    event_threshold: int,
) -> ContextLifecycleDiagnostics:
    return ContextLifecycleDiagnostics(
        reasonCodes=reason_codes,
        tokenEstimate=token_estimate,
        tokenEstimateThreshold=token_threshold,
        eventCount=event_count,
        eventCountThreshold=event_threshold,
    )


def _ordered_refs(refs: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(validate_safe_ref(ref) for ref in refs))


def _restore_provenance_digest(
    state: QueryState,
) -> str:
    projection = state.public_projection()
    projection.pop("restoreProvenanceDigest", None)
    projection.pop("authorityFlags", None)
    encoded = json.dumps(
        projection,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _false_flag_payload(cls: type[BaseModel]) -> dict[str, bool]:
    return {field.alias or name: False for name, field in cls.model_fields.items()}


def _alias_update(
    cls: type[BaseModel],
    update: Mapping[str, object],
) -> dict[str, object]:
    alias_by_name = {
        name: field.alias
        for name, field in cls.model_fields.items()
        if field.alias is not None
    }
    return {
        str(alias_by_name.get(str(key), str(key))): value
        for key, value in update.items()
    }


__all__ = [
    "ContextCompactionDecision",
    "ContextLifecycleAuthorityFlags",
    "ContextLifecycleBoundary",
    "ContextLifecycleConfig",
    "ContextLifecycleDiagnostics",
    "ContextLifecycleEvent",
    "ContextRestoreResult",
    "RestoreContextRequest",
]
