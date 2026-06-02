from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator


MissionOperatorGoalJudgeCategory = Literal["operator_surface", "goaljudge_eval"]
MissionOperatorSurfaceKind = Literal["comment", "watch", "stats", "tail"]
MissionOperatorGoalJudgeSurface = Literal[
    "comment",
    "watch",
    "stats",
    "tail",
    "goaljudge",
]
GoalJudgeVerdict = Literal["done", "continue", "blocked", "needs_user"]
GoalJudgeInputShape = Literal[
    "valid_structured_output",
    "invalid_structured_output",
    "judge_error",
]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_PRODUCTION_PATH_RE = re.compile(
    r"(?:^|[\\/])(?:data[\\/]bots|workspace|var[\\/]lib[\\/]kubelet)(?:[\\/]|$)|"
    r"(?:^|[\\/])(?:missions?|schedulers?)(?:[\\/]|$)|"
    r"(?:^|[\\/])(?:mission|scheduler)-store(?:[\\/]|$)|"
    r"pvc|supabase://|s3://|gs://|postgres(?:ql)?://|telegram|canary",
    re.IGNORECASE,
)
_FORBIDDEN_PUBLIC_TOKENS = (
    "Bearer unsafe",
    "ghp_missionsecret",
    "sk-mission-secret",
    "SUPABASE_SERVICE_ROLE_KEY",
    "gateway token",
    "hidden reasoning",
    "rawStructuredOutput",
    "Runner.run",
    "ToolHost.execute",
    "google.adk",
)
_FORBIDDEN_PUBLIC_TOKENS_NORMALIZED = tuple(
    token.casefold() for token in _FORBIDDEN_PUBLIC_TOKENS
)
_SECRET_SHAPED_VALUE_RE = re.compile(
    r"\b(?:Bearer\s+[A-Za-z0-9._~+/=-]+|gh[opusr]_[A-Za-z0-9_]+|"
    r"sk-[A-Za-z0-9._-]+|[rs]k_(?:live|test)_[A-Za-z0-9_]+)\b",
    re.IGNORECASE,
)
_FORBIDDEN_TRUE_KEYS = frozenset(
    {
        "adk_runner_invocation_authority",
        "adk_runner_invoked",
        "background_resume_attached",
        "background_resume_authority",
        "browser_artifact_channel_delivery_attached",
        "channel_delivery_authority",
        "default_on",
        "llm_model_called",
        "mission_uses_long_running_function_tool",
        "mission_writes_attached",
        "mission_writes_authority",
        "model_call_authority",
        "model_called",
        "operator_polling_attached",
        "operator_polling_authority",
        "operator_subscription_attached",
        "production_authority",
        "route_api_dashboard_attached",
        "route_api_dashboard_proxy_deploy_authority",
        "scheduler_tick_attached",
        "scheduler_ticks_authority",
        "subscription_attached",
        "tool_host_dispatched",
        "tool_host_live_dispatch_authority",
        "workspace_mutated",
    }
)
_FORBIDDEN_PUBLIC_KEYS = frozenset({"raw_structured_output"})
_REQUIRED_SURFACES = frozenset({"comment", "watch", "stats", "tail", "goaljudge"})
_REQUIRED_GOAL_INPUT_SHAPES = frozenset(
    {"valid_structured_output", "invalid_structured_output", "judge_error"}
)


class MissionOperatorGoalJudgeAttachmentFlags(BaseModel):
    model_config = _MODEL_CONFIG

    scheduler_tick_attached: Literal[False] = Field(
        default=False,
        alias="schedulerTickAttached",
    )
    background_resume_attached: Literal[False] = Field(
        default=False,
        alias="backgroundResumeAttached",
    )
    operator_polling_attached: Literal[False] = Field(
        default=False,
        alias="operatorPollingAttached",
    )
    operator_subscription_attached: Literal[False] = Field(
        default=False,
        alias="operatorSubscriptionAttached",
    )
    mission_writes_attached: Literal[False] = Field(
        default=False,
        alias="missionWritesAttached",
    )
    route_api_dashboard_attached: Literal[False] = Field(
        default=False,
        alias="routeApiDashboardAttached",
    )
    adk_runner_invoked: Literal[False] = Field(default=False, alias="adkRunnerInvoked")
    model_called: Literal[False] = Field(default=False, alias="modelCalled")
    llm_model_called: Literal[False] = Field(default=False, alias="llmModelCalled")
    toolhost_dispatched: Literal[False] = Field(default=False, alias="toolHostDispatched")
    workspace_mutated: Literal[False] = Field(default=False, alias="workspaceMutated")
    browser_artifact_channel_delivery_attached: Literal[False] = Field(
        default=False,
        alias="browserArtifactChannelDeliveryAttached",
    )
    production_authority: Literal[False] = Field(default=False, alias="productionAuthority")

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
        "scheduler_tick_attached",
        "background_resume_attached",
        "operator_polling_attached",
        "operator_subscription_attached",
        "mission_writes_attached",
        "route_api_dashboard_attached",
        "adk_runner_invoked",
        "model_called",
        "llm_model_called",
        "toolhost_dispatched",
        "workspace_mutated",
        "browser_artifact_channel_delivery_attached",
        "production_authority",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class MissionOperatorGoalJudgeRuntimeAuthority(BaseModel):
    model_config = _MODEL_CONFIG

    scheduler_ticks_authority: Literal[False] = Field(
        default=False,
        alias="schedulerTicksAuthority",
    )
    background_resume_authority: Literal[False] = Field(
        default=False,
        alias="backgroundResumeAuthority",
    )
    operator_polling_authority: Literal[False] = Field(
        default=False,
        alias="operatorPollingAuthority",
    )
    mission_writes_authority: Literal[False] = Field(
        default=False,
        alias="missionWritesAuthority",
    )
    route_api_dashboard_proxy_deploy_authority: Literal[False] = Field(
        default=False,
        alias="routeApiDashboardProxyDeployAuthority",
    )
    adk_runner_invocation_authority: Literal[False] = Field(
        default=False,
        alias="adkRunnerInvocationAuthority",
    )
    model_call_authority: Literal[False] = Field(
        default=False,
        alias="modelCallAuthority",
    )
    tool_host_live_dispatch_authority: Literal[False] = Field(
        default=False,
        alias="toolHostLiveDispatchAuthority",
    )
    channel_delivery_authority: Literal[False] = Field(
        default=False,
        alias="channelDeliveryAuthority",
    )
    production_authority: Literal[False] = Field(default=False, alias="productionAuthority")

    @field_serializer(
        "scheduler_ticks_authority",
        "background_resume_authority",
        "operator_polling_authority",
        "mission_writes_authority",
        "route_api_dashboard_proxy_deploy_authority",
        "adk_runner_invocation_authority",
        "model_call_authority",
        "tool_host_live_dispatch_authority",
        "channel_delivery_authority",
        "production_authority",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class MissionOperatorSurfaceMetadata(BaseModel):
    model_config = _MODEL_CONFIG

    surface: MissionOperatorSurfaceKind
    mission_id: str = Field(alias="missionId")
    run_id: str = Field(alias="runId")
    event_id: str = Field(alias="eventId")
    public_projection: dict[str, object] = Field(alias="publicProjection")
    operator_polling_attached: Literal[False] = Field(
        default=False,
        alias="operatorPollingAttached",
    )
    subscription_attached: Literal[False] = Field(
        default=False,
        alias="subscriptionAttached",
    )
    mission_writes_attached: Literal[False] = Field(
        default=False,
        alias="missionWritesAttached",
    )
    route_api_dashboard_attached: Literal[False] = Field(
        default=False,
        alias="routeApiDashboardAttached",
    )

    @model_validator(mode="before")
    @classmethod
    def _validate_raw_surface(cls, value: object) -> object:
        if isinstance(value, Mapping):
            _reject_unsafe_raw_value(value)
        return value

    @model_validator(mode="after")
    def _validate_surface(self) -> Self:
        for value in (self.mission_id, self.run_id, self.event_id):
            if not value.strip():
                raise ValueError("operator surface metadata requires identifiers")
            _validate_public_value(value)
        _validate_public_value(self.public_projection)
        if self.public_projection.get("surface") != self.surface:
            raise ValueError("operator surface publicProjection surface mismatch")
        return self


class GoalJudgeMetadata(BaseModel):
    model_config = _MODEL_CONFIG

    evaluator_id: str = Field(alias="evaluatorId")
    mission_id: str = Field(alias="missionId")
    run_id: str = Field(alias="runId")
    input_shape: GoalJudgeInputShape = Field(alias="inputShape")
    verdict: GoalJudgeVerdict
    continuation_allowed: bool = Field(alias="continuationAllowed")
    validator_contract: Literal["structured_output_validator"] = Field(
        alias="validatorContract",
    )
    eval_contract: Literal["goaljudge_eval_metadata"] = Field(alias="evalContract")
    adk_attachment_boundary: Literal["future_eval_callback_only"] = Field(
        alias="adkAttachmentBoundary",
    )
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")
    public_projection: dict[str, object] = Field(alias="publicProjection")
    adk_runner_invoked: Literal[False] = Field(default=False, alias="adkRunnerInvoked")
    model_called: Literal[False] = Field(default=False, alias="modelCalled")
    toolhost_dispatched: Literal[False] = Field(default=False, alias="toolHostDispatched")
    mission_uses_long_running_function_tool: Literal[False] = Field(
        default=False,
        alias="missionUsesLongRunningFunctionTool",
    )

    @model_validator(mode="before")
    @classmethod
    def _validate_raw_goal_judge(cls, value: object) -> object:
        if isinstance(value, Mapping):
            _reject_unsafe_raw_value(value)
        return value

    @model_validator(mode="after")
    def _validate_goal_judge(self) -> Self:
        for value in (self.evaluator_id, self.mission_id, self.run_id):
            if not value.strip():
                raise ValueError("GoalJudge metadata requires identifiers")
            _validate_public_value(value)
        if not self.reason_codes or any(not code.strip() for code in self.reason_codes):
            raise ValueError("GoalJudge metadata requires reasonCodes")
        if self.input_shape == "invalid_structured_output":
            if self.verdict != "blocked" or self.continuation_allowed:
                raise ValueError("invalid structured output must block")
            if self.reason_codes != ("invalid_structured_output",):
                raise ValueError("invalid structured output requires matching reason code")
        if self.input_shape == "judge_error":
            if self.verdict != "blocked" or self.continuation_allowed:
                raise ValueError("judge errors must block")
            if self.reason_codes != ("judge_error",):
                raise ValueError("judge errors require matching reason code")
        if self.verdict in {"blocked", "needs_user", "done"} and self.continuation_allowed:
            raise ValueError("terminal GoalJudge verdicts cannot continue")
        if self.verdict == "continue" and not self.continuation_allowed:
            raise ValueError("continue GoalJudge verdict requires continuationAllowed=true")
        _validate_public_value(self.public_projection)
        if self.public_projection.get("verdict") != self.verdict:
            raise ValueError("GoalJudge publicProjection verdict mismatch")
        return self


class MissionOperatorGoalJudgeCase(BaseModel):
    model_config = _MODEL_CONFIG

    case_id: str = Field(alias="caseId")
    category: MissionOperatorGoalJudgeCategory
    surface: MissionOperatorGoalJudgeSurface
    local_diagnostic: Literal[True] = Field(alias="localDiagnostic")
    metadata_only: Literal[True] = Field(alias="metadataOnly")
    default_off: Literal[True] = Field(alias="defaultOff")
    operator_surface: MissionOperatorSurfaceMetadata | None = Field(
        default=None,
        alias="operatorSurface",
    )
    goal_judge: GoalJudgeMetadata | None = Field(default=None, alias="goalJudge")
    attachment_flags: MissionOperatorGoalJudgeAttachmentFlags = Field(
        alias="attachmentFlags",
    )

    @model_validator(mode="before")
    @classmethod
    def _validate_raw_case(cls, value: object) -> object:
        if isinstance(value, Mapping):
            _reject_unsafe_raw_value(value)
        return value

    @model_validator(mode="after")
    def _validate_case(self) -> Self:
        if not self.case_id.strip():
            raise ValueError("mission operator GoalJudge caseId must be non-empty")
        if self.category == "operator_surface":
            if self.operator_surface is None or self.goal_judge is not None:
                raise ValueError("operator surface cases require only operatorSurface")
            if self.surface != self.operator_surface.surface:
                raise ValueError("case surface must match operatorSurface surface")
        if self.category == "goaljudge_eval":
            if self.goal_judge is None or self.operator_surface is not None:
                raise ValueError("GoalJudge cases require only goalJudge metadata")
            if self.surface != "goaljudge":
                raise ValueError("GoalJudge cases must use goaljudge surface")
        return self


class MissionOperatorGoalJudgeContractFixture(BaseModel):
    model_config = _MODEL_CONFIG

    schema_version: Literal["missionOperatorGoalJudgeFixture.v1"] = Field(
        alias="schemaVersion",
    )
    fixture_id: str = Field(alias="fixtureId")
    source_runtime: Literal["typescript-core-agent"] = Field(alias="sourceRuntime")
    target_runtime: Literal["python-adk-future"] = Field(alias="targetRuntime")
    recording_mode: Literal["local_diagnostic_fixture"] = Field(alias="recordingMode")
    redaction_status: Literal["verified"] = Field(alias="redactionStatus")
    local_diagnostic: Literal[True] = Field(alias="localDiagnostic")
    metadata_only: Literal[True] = Field(alias="metadataOnly")
    default_off: Literal[True] = Field(alias="defaultOff")
    runtime_authority: MissionOperatorGoalJudgeRuntimeAuthority = Field(
        alias="runtimeAuthority",
    )
    attachment_flags: MissionOperatorGoalJudgeAttachmentFlags = Field(
        alias="attachmentFlags",
    )
    cases: tuple[MissionOperatorGoalJudgeCase, ...]

    @model_validator(mode="before")
    @classmethod
    def _validate_raw_fixture(cls, value: object) -> object:
        if isinstance(value, Mapping):
            _reject_unsafe_raw_value(value)
        return value

    @model_validator(mode="after")
    def _validate_fixture(self) -> Self:
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("mission operator GoalJudge caseIds must be unique")
        surfaces = {case.surface for case in self.cases}
        if not _REQUIRED_SURFACES.issubset(surfaces):
            raise ValueError("fixture must cover operator surfaces and GoalJudge")
        input_shapes = {
            case.goal_judge.input_shape
            for case in self.cases
            if case.goal_judge is not None
        }
        if not _REQUIRED_GOAL_INPUT_SHAPES.issubset(input_shapes):
            raise ValueError("fixture must cover GoalJudge valid, invalid, and error inputs")
        verdicts = {
            case.goal_judge.verdict
            for case in self.cases
            if case.goal_judge is not None
        }
        if not {"done", "continue", "blocked", "needs_user"}.issubset(verdicts):
            raise ValueError("fixture must cover required GoalJudge verdicts")
        return self


class MissionOperatorGoalJudgeProjection(BaseModel):
    model_config = _MODEL_CONFIG

    fixture_id: str = Field(alias="fixtureId")
    local_diagnostic: Literal[True] = Field(alias="localDiagnostic")
    metadata_only: Literal[True] = Field(alias="metadataOnly")
    default_off: Literal[True] = Field(alias="defaultOff")
    attachment_flags: MissionOperatorGoalJudgeAttachmentFlags = Field(
        alias="attachmentFlags",
    )
    runtime_authority: MissionOperatorGoalJudgeRuntimeAuthority = Field(
        alias="runtimeAuthority",
    )
    no_live_execution: Literal[True] = Field(alias="noLiveExecution")
    case_order: tuple[str, ...] = Field(alias="caseOrder")
    by_category: dict[str, int] = Field(alias="byCategory")
    by_surface: dict[str, int] = Field(alias="bySurface")
    by_verdict: dict[str, int] = Field(alias="byVerdict")
    case_snapshots: dict[str, dict[str, object]] = Field(alias="caseSnapshots")


def load_mission_operator_goaljudge_contract_fixture(
    path: str | Path,
    *,
    fixture_root: str | Path | None = None,
) -> MissionOperatorGoalJudgeContractFixture:
    resolved_path = _resolve_fixture_path(path, fixture_root=fixture_root)
    with resolved_path.open("r", encoding="utf-8") as fixture_file:
        payload: object = json.load(fixture_file)
    return MissionOperatorGoalJudgeContractFixture.model_validate(payload)


def project_mission_operator_goaljudge_contract_fixture(
    fixture: MissionOperatorGoalJudgeContractFixture | Mapping[str, Any],
) -> MissionOperatorGoalJudgeProjection:
    safe_fixture = _validated_fixture_snapshot(fixture)
    category_counts: Counter[str] = Counter()
    surface_counts: Counter[str] = Counter()
    verdict_counts: Counter[str] = Counter()
    case_snapshots: dict[str, dict[str, object]] = {}

    for case in safe_fixture.cases:
        category_counts[case.category] += 1
        surface_counts[case.surface] += 1
        snapshot = _case_snapshot(case)
        _validate_public_value(snapshot)
        case_snapshots[case.case_id] = snapshot
        if case.goal_judge is not None:
            verdict_counts[case.goal_judge.verdict] += 1

    return MissionOperatorGoalJudgeProjection(
        fixtureId=safe_fixture.fixture_id,
        localDiagnostic=True,
        metadataOnly=True,
        defaultOff=True,
        attachmentFlags=safe_fixture.attachment_flags,
        runtimeAuthority=safe_fixture.runtime_authority,
        noLiveExecution=True,
        caseOrder=tuple(case.case_id for case in safe_fixture.cases),
        byCategory=dict(category_counts),
        bySurface=dict(surface_counts),
        byVerdict=dict(verdict_counts),
        caseSnapshots=case_snapshots,
    )


def _validated_fixture_snapshot(
    fixture: MissionOperatorGoalJudgeContractFixture | Mapping[str, Any],
) -> MissionOperatorGoalJudgeContractFixture:
    if isinstance(fixture, MissionOperatorGoalJudgeContractFixture):
        return MissionOperatorGoalJudgeContractFixture.model_validate(
            fixture.model_dump(by_alias=True, mode="json", warnings=False)
        )
    return MissionOperatorGoalJudgeContractFixture.model_validate(fixture)


def _case_snapshot(case: MissionOperatorGoalJudgeCase) -> dict[str, object]:
    snapshot: dict[str, object] = {
        "caseId": case.case_id,
        "category": case.category,
        "surface": case.surface,
        "localDiagnostic": True,
        "metadataOnly": True,
        "defaultOff": True,
        "noLiveExecution": True,
        "schedulerTickAttached": False,
        "backgroundResumeAttached": False,
        "operatorPollingAttached": False,
        "missionWritesAttached": False,
        "routeApiDashboardAttached": False,
        "adkRunnerInvoked": False,
        "modelCalled": False,
        "toolHostDispatched": False,
        "missionUsesLongRunningFunctionTool": False,
    }
    if case.operator_surface is not None:
        snapshot.update(
            {
                "missionId": case.operator_surface.mission_id,
                "runId": case.operator_surface.run_id,
                "eventId": case.operator_surface.event_id,
                "publicProjection": case.operator_surface.public_projection,
            }
        )
    if case.goal_judge is not None:
        snapshot.update(
            {
                "missionId": case.goal_judge.mission_id,
                "runId": case.goal_judge.run_id,
                "evaluatorId": case.goal_judge.evaluator_id,
                "inputShape": case.goal_judge.input_shape,
                "verdict": case.goal_judge.verdict,
                "continuationAllowed": case.goal_judge.continuation_allowed,
                "validatorContract": case.goal_judge.validator_contract,
                "evalContract": case.goal_judge.eval_contract,
                "adkAttachmentBoundary": case.goal_judge.adk_attachment_boundary,
                "reasonCodes": case.goal_judge.reason_codes,
                "publicProjection": case.goal_judge.public_projection,
            }
        )
    return snapshot


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
        raise ValueError("mission operator GoalJudge fixture path must stay under fixture_root")
    return resolved_candidate


def _reject_unsafe_path_text(path_text: str) -> None:
    if _PRODUCTION_PATH_RE.search(path_text):
        raise ValueError("mission operator GoalJudge fixtures must be local")


def _reject_unsafe_raw_value(value: object) -> None:
    _validate_json_like(value)
    if isinstance(value, str):
        if _PRODUCTION_PATH_RE.search(value):
            raise ValueError("mission operator GoalJudge fixture contains unsafe path")
        if any(token.casefold() in value.casefold() for token in _FORBIDDEN_PUBLIC_TOKENS):
            raise ValueError("mission operator GoalJudge fixture contains unsafe data")
        if _SECRET_SHAPED_VALUE_RE.search(value):
            raise ValueError("mission operator GoalJudge fixture contains secret-shaped data")
        return
    if isinstance(value, Mapping):
        for key, nested_value in value.items():
            normalized_key = _normalize_key(key)
            if normalized_key in _FORBIDDEN_PUBLIC_KEYS:
                raise ValueError("mission operator GoalJudge fixture contains raw output")
            if nested_value is True and normalized_key in _FORBIDDEN_TRUE_KEYS:
                raise ValueError("mission operator GoalJudge fixture cannot claim live behavior")
            _reject_unsafe_raw_value(nested_value)
        return
    if isinstance(value, list | tuple):
        for item in value:
            _reject_unsafe_raw_value(item)


def _validate_public_value(value: object) -> None:
    _validate_json_like(value)
    _reject_forbidden_true_flags(value)
    rendered = json.dumps(value, sort_keys=True) if not isinstance(value, str) else value
    normalized = rendered.casefold()
    if _PRODUCTION_PATH_RE.search(rendered):
        raise ValueError("mission operator GoalJudge public projection contains unsafe path")
    if any(token in normalized for token in _FORBIDDEN_PUBLIC_TOKENS_NORMALIZED):
        raise ValueError("mission operator GoalJudge public projection contains unsafe data")
    if _SECRET_SHAPED_VALUE_RE.search(rendered):
        raise ValueError("mission operator GoalJudge public projection contains secret-shaped data")


def _reject_forbidden_true_flags(value: object) -> None:
    if isinstance(value, Mapping):
        for key, nested_value in value.items():
            if nested_value is True and _normalize_key(key) in _FORBIDDEN_TRUE_KEYS:
                raise ValueError("mission operator GoalJudge public projection cannot claim live behavior")
            _reject_forbidden_true_flags(nested_value)
        return
    if isinstance(value, list | tuple):
        for item in value:
            _reject_forbidden_true_flags(item)


def _validate_json_like(value: object) -> None:
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int) and not isinstance(value, bool):
        return
    if isinstance(value, float):
        if math.isfinite(value):
            return
        raise ValueError("mission operator GoalJudge values must be JSON-compatible")
    if isinstance(value, list | tuple):
        for item in value:
            _validate_json_like(item)
        return
    if isinstance(value, Mapping):
        for key, nested_value in value.items():
            if not isinstance(key, str):
                raise ValueError("mission operator GoalJudge mappings must use string keys")
            _validate_json_like(nested_value)
        return
    raise ValueError("mission operator GoalJudge values must be JSON-compatible")


def _normalize_key(key: object) -> str:
    if not isinstance(key, str):
        raise ValueError("mission operator GoalJudge mappings must use string keys")
    spaced = re.sub(r"(?<!^)(?=[A-Z])", "_", key)
    return re.sub(r"[^a-z0-9]+", "_", spaced.lower()).strip("_")


__all__ = [
    "GoalJudgeMetadata",
    "MissionOperatorGoalJudgeAttachmentFlags",
    "MissionOperatorGoalJudgeCase",
    "MissionOperatorGoalJudgeContractFixture",
    "MissionOperatorGoalJudgeProjection",
    "MissionOperatorGoalJudgeRuntimeAuthority",
    "MissionOperatorSurfaceMetadata",
    "load_mission_operator_goaljudge_contract_fixture",
    "project_mission_operator_goaljudge_contract_fixture",
]
