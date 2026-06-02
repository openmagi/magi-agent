from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from magi_agent.transport.tool_preview import sanitize_tool_preview


PlanGateArtifactKind = Literal["plan", "interview", "consensus"]


class PlanGateSessionImpact(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    session_service_owner: Literal["adk-session-service"] = Field(
        default="adk-session-service", alias="sessionServiceOwner"
    )
    plan_state_owner: Literal["session-service"] = Field(
        default="session-service", alias="planStateOwner"
    )
    stores_plan_state_later: Literal[True] = Field(
        default=True, alias="storesPlanStateLater"
    )
    session_write_attached: Literal[False] = Field(
        default=False, alias="sessionWriteAttached"
    )


class PlanGateTranscriptImpact(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    transcript_owner: Literal["openmagi-transcript"] = Field(
        default="openmagi-transcript", alias="transcriptOwner"
    )
    entry_kind: Literal["plan_gate_decision"] = Field(
        default="plan_gate_decision", alias="entryKind"
    )
    lane: str
    decision: str
    records_lane: Literal[True] = Field(default=True, alias="recordsLane")
    records_decision: Literal[True] = Field(default=True, alias="recordsDecision")
    transcript_write_attached: Literal[False] = Field(
        default=False, alias="transcriptWriteAttached"
    )

    @field_validator("lane", "decision")
    @classmethod
    def _reject_empty_transcript_fields(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("transcript lane and decision must be non-empty")
        return value


class PlanGateArtifactImpact(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    artifact_service_owner: Literal["adk-artifact-service"] = Field(
        default="adk-artifact-service", alias="artifactServiceOwner"
    )
    openmagi_index_owner: Literal["openmagi-artifact-index"] = Field(
        default="openmagi-artifact-index", alias="openmagiIndexOwner"
    )
    artifact_ref: str | None = Field(default=None, alias="artifactRef")
    artifact_kind: PlanGateArtifactKind | None = Field(default=None, alias="artifactKind")
    openmagi_index_records_ref: bool = Field(
        default=False, alias="openmagiIndexRecordsRef"
    )
    artifact_write_attached: Literal[False] = Field(
        default=False, alias="artifactWriteAttached"
    )

    @field_validator("artifact_ref")
    @classmethod
    def _reject_empty_artifact_ref(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("artifactRef must be non-empty when provided")
        return value

    @model_validator(mode="after")
    def _validate_artifact_ref_pair(self) -> PlanGateArtifactImpact:
        if self.artifact_ref is None and self.artifact_kind is not None:
            raise ValueError("artifactKind requires artifactRef")
        if self.artifact_ref is not None and self.artifact_kind is None:
            raise ValueError("artifactRef requires artifactKind")
        if self.openmagi_index_records_ref != (self.artifact_ref is not None):
            raise ValueError("openmagiIndexRecordsRef must match artifactRef presence")
        return self


class PlanGateControlRequestRef(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    request_id: str = Field(alias="requestId")
    kind: str
    state: str
    turn_id: str | None = Field(default=None, alias="turnId")

    @field_validator("request_id", "kind", "state")
    @classmethod
    def _reject_empty_control_fields(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("control request reference fields must be non-empty")
        return value

    @field_validator("turn_id")
    @classmethod
    def _reject_empty_optional_turn_id(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("turnId must be non-empty when provided")
        return value


class PlanGateDecisionSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    decision_id: str = Field(alias="decisionId")
    session_key: str = Field(alias="sessionKey")
    turn_id: str = Field(alias="turnId")
    lane: str
    decision: str
    reason: str
    public_signal_preview: str | None = Field(
        default=None, alias="publicSignalPreview"
    )
    session_impact: PlanGateSessionImpact = Field(alias="sessionImpact")
    transcript_impact: PlanGateTranscriptImpact = Field(alias="transcriptImpact")
    artifact_impact: PlanGateArtifactImpact = Field(alias="artifactImpact")
    control_request_ref: PlanGateControlRequestRef | None = Field(
        default=None, alias="controlRequestRef"
    )
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")
    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    execution_attached: Literal[False] = Field(default=False, alias="executionAttached")

    @field_validator("decision_id", "session_key", "turn_id", "lane", "decision", "reason")
    @classmethod
    def _reject_empty_snapshot_fields(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("plan gate snapshot fields must be non-empty")
        return value

    @field_validator("public_signal_preview")
    @classmethod
    def _sanitize_public_signal_preview(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return sanitize_tool_preview(value)

    @model_validator(mode="after")
    def _validate_impact_alignment(self) -> PlanGateDecisionSnapshot:
        if self.transcript_impact.lane != self.lane:
            raise ValueError("transcriptImpact lane must match snapshot lane")
        if self.transcript_impact.decision != self.decision:
            raise ValueError("transcriptImpact decision must match snapshot decision")
        if (
            self.control_request_ref is not None
            and self.control_request_ref.turn_id is not None
            and self.control_request_ref.turn_id != self.turn_id
        ):
            raise ValueError("controlRequestRef turnId must match snapshot turnId")
        return self


def build_plan_gate_decision_snapshot(
    *,
    decision_id: str,
    session_key: str,
    turn_id: str,
    lane: str,
    decision: str,
    reason: str,
    public_signal_preview: str | None = None,
    artifact_ref: str | None = None,
    artifact_kind: PlanGateArtifactKind | None = None,
    control_request_ref: PlanGateControlRequestRef | dict[str, object] | None = None,
) -> PlanGateDecisionSnapshot:
    resolved_control_request_ref = (
        PlanGateControlRequestRef.model_validate(
            control_request_ref.model_dump(by_alias=True, warnings="none")
            if isinstance(control_request_ref, PlanGateControlRequestRef)
            else control_request_ref
        )
        if control_request_ref is not None
        else None
    )

    return PlanGateDecisionSnapshot(
        decision_id=decision_id,
        session_key=session_key,
        turn_id=turn_id,
        lane=lane,
        decision=decision,
        reason=reason,
        public_signal_preview=public_signal_preview,
        session_impact=PlanGateSessionImpact(),
        transcript_impact=PlanGateTranscriptImpact(lane=lane, decision=decision),
        artifact_impact=PlanGateArtifactImpact(
            artifact_ref=artifact_ref,
            artifact_kind=artifact_kind,
            openmagi_index_records_ref=artifact_ref is not None,
        ),
        control_request_ref=resolved_control_request_ref,
        route_attached=False,
        traffic_attached=False,
        execution_attached=False,
    )
