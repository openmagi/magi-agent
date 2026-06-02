from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from openmagi_core_agent.transport.tool_preview import sanitize_tool_preview


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        data = self.model_dump(by_alias=True, warnings="none")
        if update:
            alias_by_input_key = {
                field_name: field.alias or field_name
                for field_name, field in self.__class__.model_fields.items()
            }
            alias_by_input_key.update(
                {
                    field.alias: field.alias
                    for field in self.__class__.model_fields.values()
                    if field.alias is not None
                }
            )
            data.update({alias_by_input_key.get(key, key): value for key, value in update.items()})
        return self.__class__.model_validate(data)


class ObservableProcessSignalKind(StrEnum):
    READ_BEFORE_WRITE = "read_before_write"
    DETERMINISTIC_EXACTNESS = "deterministic_exactness"
    SOURCE_GROUNDING = "source_grounding"
    VERIFICATION_DISCIPLINE = "verification_discipline"
    LOOP_CONTROL = "loop_control"
    PARALLELISM = "parallelism"
    DELIVERY_RELIABILITY = "delivery_reliability"
    SELF_DEBUGGING = "self_debugging"


ObservableProcessPolarity = Literal["positive", "negative", "neutral"]
ObservableProcessSourceSurface = Literal[
    "transcript",
    "adk_event",
    "tool_result",
    "artifact",
    "verifier",
    "harness_audit",
    "evidence_ledger",
    "channel",
    "workspace",
    "task",
]
AgentRole = Literal["general", "coding", "research"]
RunOn = Literal["main", "child"]
AggregationScope = Literal["turn", "task", "benchmark"]
ThresholdStatus = Literal["ok", "warn", "fail"]


class ObservableProcessSignal(_StrictFrozenModel):
    signal_kind: ObservableProcessSignalKind = Field(alias="signalKind")
    description: str
    positive_weight: float = Field(default=1.0, alias="positiveWeight")
    negative_weight: float = Field(default=1.0, alias="negativeWeight")
    warn_below: float = Field(default=0.7, alias="warnBelow")
    fail_below: float = Field(default=0.4, alias="failBelow")
    default_report_only: bool = Field(default=True, alias="defaultReportOnly")
    policy_attached: bool = Field(default=False, alias="policyAttached")

    @model_validator(mode="after")
    def _validate_signal(self) -> Self:
        if self.positive_weight <= 0 or self.negative_weight <= 0:
            raise ValueError("process signal weights must be positive")
        if not 0 <= self.fail_below <= self.warn_below <= 1:
            raise ValueError("process signal thresholds must satisfy 0 <= failBelow <= warnBelow <= 1")
        if not self.default_report_only or self.policy_attached:
            raise ValueError("process signals must remain report-only and policy-free")
        return self


def _signal(
    signal_kind: ObservableProcessSignalKind,
    description: str,
) -> ObservableProcessSignal:
    return ObservableProcessSignal(
        signalKind=signal_kind,
        description=description,
        positiveWeight=1.0,
        negativeWeight=1.0,
        warnBelow=0.7,
        failBelow=0.4,
        defaultReportOnly=True,
        policyAttached=False,
    )


_OBSERVABLE_PROCESS_SIGNAL_CATALOG: dict[str, ObservableProcessSignal] = {
    signal.value: _signal(signal, description)
    for signal, description in (
        (
            ObservableProcessSignalKind.READ_BEFORE_WRITE,
            "Observable reads precede writes when task context requires inspection.",
        ),
        (
            ObservableProcessSignalKind.DETERMINISTIC_EXACTNESS,
            "Exact commands, paths, identifiers, and outputs are preserved deterministically.",
        ),
        (
            ObservableProcessSignalKind.SOURCE_GROUNDING,
            "Claims are grounded in observable source, artifact, verifier, or ledger surfaces.",
        ),
        (
            ObservableProcessSignalKind.VERIFICATION_DISCIPLINE,
            "Verification commands or equivalent checks are run and reported when needed.",
        ),
        (
            ObservableProcessSignalKind.LOOP_CONTROL,
            "The agent bounds retries, debugging loops, and repeated attempts.",
        ),
        (
            ObservableProcessSignalKind.PARALLELISM,
            "Independent reads or checks are batched where useful without coupling state.",
        ),
        (
            ObservableProcessSignalKind.DELIVERY_RELIABILITY,
            "Final delivery reflects completed observable work and residual risks.",
        ),
        (
            ObservableProcessSignalKind.SELF_DEBUGGING,
            "Failures are investigated from observable evidence before fixes are claimed.",
        ),
    )
}
OBSERVABLE_PROCESS_SIGNAL_CATALOG: Mapping[str, ObservableProcessSignal] = MappingProxyType(
    _OBSERVABLE_PROCESS_SIGNAL_CATALOG
)


class FrozenMetadataDict(Mapping[str, Any]):
    def __init__(self, value: Mapping[str, Any]) -> None:
        self._value = dict(value)

    def __getitem__(self, key: str) -> Any:
        return self._value[key]

    def __iter__(self) -> Iterable[str]:
        return iter(self._value)

    def __len__(self) -> int:
        return len(self._value)

    def __repr__(self) -> str:
        return repr(self._value)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Mapping):
            return dict(self.items()) == dict(other.items())
        return False


class FrozenMetadataList(Sequence[Any]):
    def __init__(self, value: Sequence[Any]) -> None:
        self._value = tuple(value)

    def __getitem__(self, index: int | slice) -> Any:
        return self._value[index]

    def __len__(self) -> int:
        return len(self._value)

    def __repr__(self) -> str:
        return repr(list(self._value))

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Sequence) and not isinstance(other, str | bytes | bytearray):
            return list(self._value) == list(other)
        return False

    def append(self, _: Any) -> None:
        raise TypeError("metadata lists are immutable")


class ObservableProcessEvent(_StrictFrozenModel):
    signal_kind: ObservableProcessSignalKind = Field(alias="signalKind")
    polarity: ObservableProcessPolarity
    session_id: str = Field(alias="sessionId")
    turn_id: str = Field(alias="turnId")
    run_on: RunOn = Field(alias="runOn")
    agent_role: AgentRole = Field(alias="agentRole")
    spawn_depth: int = Field(alias="spawnDepth")
    source_surface: ObservableProcessSourceSurface = Field(alias="sourceSurface")
    public_preview: str | None = Field(default=None, alias="publicPreview")
    metadata: Mapping[str, Any] = Field(default_factory=dict)
    traffic_attached: bool = Field(default=False, alias="trafficAttached")
    execution_attached: bool = Field(default=False, alias="executionAttached")
    runner_attached: bool = Field(default=False, alias="runnerAttached")
    route_attached: bool = Field(default=False, alias="routeAttached")
    policy_attached: bool = Field(default=False, alias="policyAttached")
    canary_attached: bool = Field(default=False, alias="canaryAttached")

    @field_validator("session_id", "turn_id")
    @classmethod
    def _reject_empty_ids(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("process event identifiers must be non-empty")
        return value

    @field_validator("public_preview")
    @classmethod
    def _sanitize_public_preview(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return sanitize_tool_preview(value)

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(cls, value: Any) -> Mapping[str, Any]:
        return _freeze_metadata(value, path="metadata")

    @field_serializer("metadata")
    def _serialize_metadata(self, value: Mapping[str, Any]) -> dict[str, Any]:
        return _thaw_json_like(value)

    @field_validator(
        "traffic_attached",
        "execution_attached",
        "runner_attached",
        "route_attached",
        "policy_attached",
        "canary_attached",
    )
    @classmethod
    def _reject_attachment_flags(cls, value: bool) -> bool:
        if value:
            raise ValueError("observable process reward metadata must stay report-only and detached")
        return value

    @model_validator(mode="after")
    def _validate_context(self) -> Self:
        if self.spawn_depth < 0:
            raise ValueError("spawnDepth must be non-negative")
        if self.run_on == "main" and self.spawn_depth != 0:
            raise ValueError("main runs must use spawnDepth=0")
        if self.run_on == "child" and self.spawn_depth <= 0:
            raise ValueError("child runs must use spawnDepth greater than 0")
        return self


class ProcessRewardThresholds(_StrictFrozenModel):
    warn_below: float = Field(default=0.7, alias="warnBelow")
    fail_below: float = Field(default=0.4, alias="failBelow")
    policy_use_enabled: bool = Field(default=False, alias="policyUseEnabled")
    blocking_enabled: bool = Field(default=False, alias="blockingEnabled")
    traffic_attached: bool = Field(default=False, alias="trafficAttached")
    execution_attached: bool = Field(default=False, alias="executionAttached")
    runner_attached: bool = Field(default=False, alias="runnerAttached")
    route_attached: bool = Field(default=False, alias="routeAttached")
    policy_attached: bool = Field(default=False, alias="policyAttached")
    canary_attached: bool = Field(default=False, alias="canaryAttached")

    @model_validator(mode="after")
    def _validate_thresholds(self) -> Self:
        if not 0 <= self.fail_below <= self.warn_below <= 1:
            raise ValueError("process thresholds must satisfy 0 <= failBelow <= warnBelow <= 1")
        if self.policy_use_enabled or self.blocking_enabled:
            raise ValueError("observable process reward thresholds cannot enable policy or blocking")
        _reject_any_attachment(self)
        return self


class ProcessRewardAggregation(_StrictFrozenModel):
    scope: AggregationScope = "turn"
    session_id: str | None = Field(default=None, alias="sessionId")
    turn_ids: tuple[str, ...] = Field(default=(), alias="turnIds")
    task_id: str | None = Field(default=None, alias="taskId")
    benchmark_id: str | None = Field(default=None, alias="benchmarkId")
    traffic_attached: bool = Field(default=False, alias="trafficAttached")
    execution_attached: bool = Field(default=False, alias="executionAttached")
    runner_attached: bool = Field(default=False, alias="runnerAttached")
    route_attached: bool = Field(default=False, alias="routeAttached")
    policy_attached: bool = Field(default=False, alias="policyAttached")
    canary_attached: bool = Field(default=False, alias="canaryAttached")

    @model_validator(mode="after")
    def _validate_aggregation(self) -> Self:
        _reject_any_attachment(self)
        if self.session_id is not None and not self.session_id.strip():
            raise ValueError("sessionId must be non-empty when present")
        if any(not turn_id.strip() for turn_id in self.turn_ids):
            raise ValueError("turnIds entries must be non-empty")
        if self.task_id is not None and not self.task_id.strip():
            raise ValueError("taskId must be non-empty when present")
        if self.benchmark_id is not None and not self.benchmark_id.strip():
            raise ValueError("benchmarkId must be non-empty when present")
        return self


class ObservableProcessScoredEvent(_StrictFrozenModel):
    signal_kind: ObservableProcessSignalKind = Field(alias="signalKind")
    polarity: ObservableProcessPolarity
    effective_polarity: ObservableProcessPolarity = Field(alias="effectivePolarity")
    weight: float
    session_id: str = Field(alias="sessionId")
    turn_id: str = Field(alias="turnId")
    run_on: RunOn = Field(alias="runOn")
    agent_role: AgentRole = Field(alias="agentRole")
    spawn_depth: int = Field(alias="spawnDepth")
    source_surface: ObservableProcessSourceSurface = Field(alias="sourceSurface")
    public_preview: str | None = Field(default=None, alias="publicPreview")
    metadata: Mapping[str, Any] = Field(default_factory=dict)
    shortcut_neutralized: bool = Field(default=False, alias="shortcutNeutralized")
    shortcut_reason: str | None = Field(default=None, alias="shortcutReason")
    traffic_attached: bool = Field(default=False, alias="trafficAttached")
    execution_attached: bool = Field(default=False, alias="executionAttached")
    runner_attached: bool = Field(default=False, alias="runnerAttached")
    route_attached: bool = Field(default=False, alias="routeAttached")
    policy_attached: bool = Field(default=False, alias="policyAttached")
    canary_attached: bool = Field(default=False, alias="canaryAttached")

    @field_validator("public_preview")
    @classmethod
    def _sanitize_public_preview(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return sanitize_tool_preview(value)

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(cls, value: Any) -> Mapping[str, Any]:
        return _freeze_metadata(value, path="metadata")

    @field_serializer("metadata")
    def _serialize_metadata(self, value: Mapping[str, Any]) -> dict[str, Any]:
        return _thaw_json_like(value)

    @model_validator(mode="after")
    def _validate_detached(self) -> Self:
        _reject_any_attachment(self)
        return self


class ObservableProcessSignalScore(_StrictFrozenModel):
    signal_kind: ObservableProcessSignalKind = Field(alias="signalKind")
    positive_weight: float = Field(default=0.0, alias="positiveWeight")
    negative_weight: float = Field(default=0.0, alias="negativeWeight")
    neutral_event_count: int = Field(default=0, alias="neutralEventCount")
    shortcut_neutralized_count: int = Field(default=0, alias="shortcutNeutralizedCount")
    score: float


class ObservableProcessRewardReport(_StrictFrozenModel):
    score: float
    positive_weight: float = Field(alias="positiveWeight")
    negative_weight: float = Field(alias="negativeWeight")
    neutral_event_count: int = Field(alias="neutralEventCount")
    shortcut_neutralized_count: int = Field(alias="shortcutNeutralizedCount")
    threshold_status: ThresholdStatus = Field(alias="thresholdStatus")
    report_only: bool = Field(default=True, alias="reportOnly")
    thresholds: ProcessRewardThresholds = Field(default_factory=ProcessRewardThresholds)
    aggregation: ProcessRewardAggregation = Field(default_factory=ProcessRewardAggregation)
    signal_scores: tuple[ObservableProcessSignalScore, ...] = Field(alias="signalScores")
    events: tuple[ObservableProcessScoredEvent, ...]
    traffic_attached: bool = Field(default=False, alias="trafficAttached")
    execution_attached: bool = Field(default=False, alias="executionAttached")
    runner_attached: bool = Field(default=False, alias="runnerAttached")
    route_attached: bool = Field(default=False, alias="routeAttached")
    policy_attached: bool = Field(default=False, alias="policyAttached")
    canary_attached: bool = Field(default=False, alias="canaryAttached")

    @model_validator(mode="after")
    def _validate_report(self) -> Self:
        if not self.report_only:
            raise ValueError("observable process reward reports must remain report-only")
        _reject_any_attachment(self)
        return self


def score_observable_process_events(
    events: Iterable[ObservableProcessEvent],
    *,
    aggregation: ProcessRewardAggregation | None = None,
    thresholds: ProcessRewardThresholds | None = None,
) -> ObservableProcessRewardReport:
    validated_events = tuple(
        ObservableProcessEvent.model_validate(event.model_dump(warnings="none"))
        for event in events
    )
    resolved_thresholds = thresholds or ProcessRewardThresholds()
    resolved_aggregation = _resolve_aggregation(validated_events, aggregation)
    scored_events = tuple(sorted((_score_event(event) for event in validated_events), key=_event_sort_key))

    positive_weight = sum(event.weight for event in scored_events if event.effective_polarity == "positive")
    negative_weight = sum(event.weight for event in scored_events if event.effective_polarity == "negative")
    neutral_event_count = sum(1 for event in scored_events if event.effective_polarity == "neutral")
    shortcut_neutralized_count = sum(1 for event in scored_events if event.shortcut_neutralized)
    score = _ratio_score(positive_weight=positive_weight, negative_weight=negative_weight)
    signal_scores = _signal_scores(scored_events)

    return ObservableProcessRewardReport(
        score=score,
        positiveWeight=positive_weight,
        negativeWeight=negative_weight,
        neutralEventCount=neutral_event_count,
        shortcutNeutralizedCount=shortcut_neutralized_count,
        thresholdStatus=_threshold_status(score, resolved_thresholds),
        reportOnly=True,
        thresholds=resolved_thresholds,
        aggregation=resolved_aggregation,
        signalScores=signal_scores,
        events=scored_events,
        trafficAttached=False,
        executionAttached=False,
        runnerAttached=False,
        routeAttached=False,
        policyAttached=False,
        canaryAttached=False,
    )


def _score_event(event: ObservableProcessEvent) -> ObservableProcessScoredEvent:
    signal = _OBSERVABLE_PROCESS_SIGNAL_CATALOG[event.signal_kind.value]
    shortcut_reason = _shortcut_reason(event.metadata)
    shortcut_neutralized = event.polarity == "negative" and shortcut_reason is not None
    effective_polarity: ObservableProcessPolarity = "neutral" if shortcut_neutralized else event.polarity
    weight = 0.0
    if effective_polarity == "positive":
        weight = signal.positive_weight
    elif effective_polarity == "negative":
        weight = signal.negative_weight

    return ObservableProcessScoredEvent(
        signalKind=event.signal_kind,
        polarity=event.polarity,
        effectivePolarity=effective_polarity,
        weight=weight,
        sessionId=event.session_id,
        turnId=event.turn_id,
        runOn=event.run_on,
        agentRole=event.agent_role,
        spawnDepth=event.spawn_depth,
        sourceSurface=event.source_surface,
        publicPreview=event.public_preview,
        metadata=event.metadata,
        shortcutNeutralized=shortcut_neutralized,
        shortcutReason=shortcut_reason,
        trafficAttached=False,
        executionAttached=False,
        runnerAttached=False,
        routeAttached=False,
        policyAttached=False,
        canaryAttached=False,
    )


def _resolve_aggregation(
    events: tuple[ObservableProcessEvent, ...],
    aggregation: ProcessRewardAggregation | None,
) -> ProcessRewardAggregation:
    base = aggregation or ProcessRewardAggregation()
    session_ids = tuple(sorted({event.session_id for event in events}))
    turn_ids = tuple(sorted({event.turn_id for event in events}))
    session_id = base.session_id or (session_ids[0] if len(session_ids) == 1 else None)
    return ProcessRewardAggregation(
        scope=base.scope,
        sessionId=session_id,
        turnIds=base.turn_ids or turn_ids,
        taskId=base.task_id,
        benchmarkId=base.benchmark_id,
        trafficAttached=False,
        executionAttached=False,
        runnerAttached=False,
        routeAttached=False,
        policyAttached=False,
        canaryAttached=False,
    )


def _signal_scores(
    events: tuple[ObservableProcessScoredEvent, ...],
) -> tuple[ObservableProcessSignalScore, ...]:
    scores: list[ObservableProcessSignalScore] = []
    for signal_kind in ObservableProcessSignalKind:
        matching = tuple(event for event in events if event.signal_kind == signal_kind)
        positive_weight = sum(
            event.weight for event in matching if event.effective_polarity == "positive"
        )
        negative_weight = sum(
            event.weight for event in matching if event.effective_polarity == "negative"
        )
        scores.append(
            ObservableProcessSignalScore(
                signalKind=signal_kind,
                positiveWeight=positive_weight,
                negativeWeight=negative_weight,
                neutralEventCount=sum(
                    1 for event in matching if event.effective_polarity == "neutral"
                ),
                shortcutNeutralizedCount=sum(1 for event in matching if event.shortcut_neutralized),
                score=_ratio_score(
                    positive_weight=positive_weight,
                    negative_weight=negative_weight,
                ),
            )
        )
    return tuple(scores)


def _ratio_score(*, positive_weight: float, negative_weight: float) -> float:
    denominator = positive_weight + negative_weight
    if denominator == 0:
        return 1.0
    return positive_weight / denominator


def _threshold_status(score: float, thresholds: ProcessRewardThresholds) -> ThresholdStatus:
    if score < thresholds.fail_below:
        return "fail"
    if score < thresholds.warn_below:
        return "warn"
    return "ok"


def _shortcut_reason(metadata: Mapping[str, Any]) -> str | None:
    marker = metadata.get("userApprovedShortcut")
    if marker is True:
        return "user-approved shortcut"
    if isinstance(marker, Mapping):
        reason = marker.get("reason")
        if isinstance(reason, str) and reason.strip():
            return reason
    return None


def _event_sort_key(event: ObservableProcessScoredEvent) -> tuple[str, str, str, str, str, int, str]:
    return (
        event.session_id,
        event.turn_id,
        event.signal_kind.value,
        event.polarity,
        event.run_on,
        event.spawn_depth,
        event.source_surface,
    )


def _reject_any_attachment(model: BaseModel) -> None:
    for field_name in (
        "traffic_attached",
        "execution_attached",
        "runner_attached",
        "route_attached",
        "policy_attached",
        "canary_attached",
    ):
        if bool(getattr(model, field_name, False)):
            raise ValueError("observable process reward metadata must stay detached")


def _freeze_metadata(value: Any, *, path: str) -> Mapping[str, Any]:
    frozen = _freeze_json_like(value, path=path)
    if not isinstance(frozen, Mapping):
        raise ValueError(f"{path} must be a JSON-like object")
    return frozen


def _freeze_json_like(value: Any, *, path: str) -> Any:
    if isinstance(value, FrozenMetadataDict | FrozenMetadataList):
        return value
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} numbers must be finite")
        return value
    if isinstance(value, list):
        return FrozenMetadataList(
            [_freeze_json_like(item, path=f"{path}[{index}]") for index, item in enumerate(value)]
        )
    if isinstance(value, dict):
        frozen: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} keys must be strings")
            frozen[key] = _freeze_json_like(item, path=f"{path}.{key}")
        return FrozenMetadataDict(frozen)
    raise ValueError(f"{path} must contain only JSON-like metadata")


def _thaw_json_like(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json_like(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_thaw_json_like(item) for item in value]
    return value


__all__ = [
    "AggregationScope",
    "AgentRole",
    "OBSERVABLE_PROCESS_SIGNAL_CATALOG",
    "ObservableProcessEvent",
    "ObservableProcessPolarity",
    "ObservableProcessRewardReport",
    "ObservableProcessScoredEvent",
    "ObservableProcessSignal",
    "ObservableProcessSignalKind",
    "ObservableProcessSignalScore",
    "ObservableProcessSourceSurface",
    "ProcessRewardAggregation",
    "ProcessRewardThresholds",
    "RunOn",
    "ThresholdStatus",
    "score_observable_process_events",
]
