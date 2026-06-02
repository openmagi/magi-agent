from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from magi_agent.runtime.request_ledger import (
    RequestLedgerAuthorityFlags,
    RequestLedgerDiagnostics,
    RequestShapeLedgerResult,
)

from .kernel import ToolExecutionKernel, ToolExecutionOutcome, ToolExecutionRequest
from .manifest import ToolManifest
from .registry import ToolRegistry
from .result import ToolResult
from .schema_validation import validate_tool_arguments


ToolSchedulerStatus = Literal["disabled", "ok", "blocked"]
ToolScheduleStepStatus = Literal[
    "blocked",
    "scheduled",
    "duplicate_blocked",
    "conflict_serialized",
    "budget_serialized",
]
ToolScheduleMode = Literal["none", "parallel", "serial"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    arbitrary_types_allowed=True,
    hide_input_in_errors=True,
)


class _ToolSchedulerModel(BaseModel):
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


class ToolSchedulerConfig(_ToolSchedulerModel):
    enabled: bool = False
    local_fake_scheduler_enabled: bool = Field(default=False, alias="localFakeSchedulerEnabled")
    live_execution_enabled: Literal[False] = Field(default=False, alias="liveExecutionEnabled")
    production_execution_attached: Literal[False] = Field(
        default=False,
        alias="productionExecutionAttached",
    )


class ToolScheduleTask(_ToolSchedulerModel):
    request: ToolExecutionRequest
    task_id: str | None = Field(default=None, alias="taskId")
    conflict_key: str | None = Field(default=None, alias="conflictKey")
    strategy_metadata: Mapping[str, object] = Field(
        default_factory=dict,
        alias="strategyMetadata",
    )

    @field_serializer("request")
    def _serialize_request(self, value: ToolExecutionRequest) -> dict[str, object]:
        return value.model_dump(by_alias=True, mode="json", warnings=False)


class ToolScheduleStep(_ToolSchedulerModel):
    sequence_index: int = Field(alias="sequenceIndex")
    tool_name: str = Field(alias="toolName")
    task_id: str | None = Field(default=None, alias="taskId")
    conflict_key: str | None = Field(default=None, alias="conflictKey")
    status: ToolScheduleStepStatus
    mode: ToolScheduleMode
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")


class ToolSchedulerOutcome(_ToolSchedulerModel):
    status: ToolSchedulerStatus
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    steps: tuple[ToolScheduleStep, ...] = ()
    results: tuple[ToolExecutionOutcome, ...] = ()
    authority_flags: RequestLedgerAuthorityFlags = Field(
        default_factory=RequestLedgerAuthorityFlags,
        alias="authorityFlags",
    )

    @model_validator(mode="after")
    def _force_authority_flags(self) -> Self:
        if self.authority_flags == RequestLedgerAuthorityFlags():
            return self
        return type(self)(
            status=self.status,
            reasonCodes=self.reason_codes,
            steps=self.steps,
            results=self.results,
            authorityFlags=RequestLedgerAuthorityFlags(),
        )


class ToolScheduler:
    def __init__(
        self,
        *,
        kernel: ToolExecutionKernel | None = None,
        registry: ToolRegistry | None = None,
        config: ToolSchedulerConfig | Mapping[str, object] | None = None,
    ) -> None:
        self.kernel = kernel
        self.registry = registry or (kernel.registry if kernel is not None else None)
        self.config = ToolSchedulerConfig.model_validate(config or {})

    @property
    def core_strategy_names(self) -> tuple[str, ...]:
        return ()

    async def execute(
        self,
        tasks: Sequence[ToolScheduleTask | Mapping[str, object]],
    ) -> ToolSchedulerOutcome:
        safe_tasks = tuple(ToolScheduleTask.model_validate(task) for task in tasks)
        if not self.config.enabled or not self.config.local_fake_scheduler_enabled:
            return ToolSchedulerOutcome(
                status="disabled",
                reasonCodes=("tool_scheduler_disabled",),
                steps=(),
                results=(),
                authorityFlags=RequestLedgerAuthorityFlags(),
            )

        steps, preblocked = self._plan(safe_tasks)
        if self.kernel is None:
            results = tuple(
                outcome
                if outcome is not None
                else _blocked_outcome(task.request, reason_code="tool_scheduler_kernel_missing")
                for task, outcome in zip(safe_tasks, preblocked, strict=True)
            )
            return ToolSchedulerOutcome(
                status="blocked",
                reasonCodes=("tool_scheduler_kernel_missing",),
                steps=tuple(steps),
                results=results,
                authorityFlags=RequestLedgerAuthorityFlags(),
            )

        results = list(preblocked)
        index = 0
        while index < len(safe_tasks):
            if results[index] is not None:
                index += 1
                continue
            step = steps[index]
            if step.mode == "parallel":
                batch_indexes: list[int] = []
                while (
                    index < len(safe_tasks)
                    and results[index] is None
                    and steps[index].mode == "parallel"
                ):
                    batch_indexes.append(index)
                    index += 1
                batch_results = await asyncio.gather(
                    *(self.kernel.execute(safe_tasks[item].request) for item in batch_indexes)
                )
                for item, result in zip(batch_indexes, batch_results, strict=True):
                    results[item] = result
                continue

            results[index] = await self.kernel.execute(safe_tasks[index].request)
            index += 1

        concrete_results = tuple(result for result in results if result is not None)
        plan_reason_codes = tuple(
            dict.fromkeys(
                reason
                for step in steps
                for reason in step.reason_codes
                if reason not in {"conflict_serialized", "max_parallel_serialized"}
            )
        )
        result_reason_codes = tuple(
            dict.fromkeys(
                reason
                for result in concrete_results
                if (reason := _result_reason_code(result)) is not None
            )
        )
        reason_codes = tuple(dict.fromkeys((*plan_reason_codes, *result_reason_codes)))
        return ToolSchedulerOutcome(
            status="blocked" if reason_codes else "ok",
            reasonCodes=reason_codes,
            steps=tuple(steps),
            results=concrete_results,
            authorityFlags=RequestLedgerAuthorityFlags(),
        )

    def _plan(
        self,
        tasks: tuple[ToolScheduleTask, ...],
    ) -> tuple[list[ToolScheduleStep], list[ToolExecutionOutcome | None]]:
        steps: list[ToolScheduleStep] = []
        preblocked: list[ToolExecutionOutcome | None] = []
        seen_task_ids: set[str] = set()
        seen_conflict_keys: set[str] = set()
        parallel_window_counts: dict[str, int] = {}

        for index, task in enumerate(tasks):
            request = task.request
            if task.task_id is not None and task.task_id in seen_task_ids:
                steps.append(
                    _step(
                        index,
                        task,
                        status="duplicate_blocked",
                        mode="none",
                        reason_codes=("duplicate_task_blocked",),
                    )
                )
                preblocked.append(
                    _blocked_outcome(request, reason_code="duplicate_task_blocked")
                )
                continue
            if task.task_id is not None:
                seen_task_ids.add(task.task_id)

            manifest = self._manifest_for(request.tool_name)
            schema_decision = (
                validate_tool_arguments(manifest, request.arguments)
                if manifest is not None
                else None
            )
            if schema_decision is not None and not schema_decision.valid:
                steps.append(
                    _step(
                        index,
                        task,
                        status="blocked",
                        mode="none",
                        reason_codes=("tool_input_schema_invalid",),
                    )
                )
                preblocked.append(
                    _blocked_outcome(
                        request,
                        reason_code="tool_input_schema_invalid",
                        metadata={
                            "toolName": request.tool_name,
                            "mode": request.mode,
                            "reason": "input schema validation failed",
                            "schemaValidation": schema_decision.public_projection(),
                        },
                    )
                )
                continue

            safe_for_parallel = manifest is not None and _is_parallel_safe(manifest)
            max_parallel = _max_parallel(manifest) if manifest is not None else None
            conflicts = task.conflict_key is not None and task.conflict_key in seen_conflict_keys
            if task.conflict_key is not None:
                seen_conflict_keys.add(task.conflict_key)

            over_parallel_budget = (
                safe_for_parallel
                and max_parallel is not None
                and parallel_window_counts.get(request.tool_name, 0) >= max_parallel
            )

            if safe_for_parallel and not conflicts and not over_parallel_budget:
                steps.append(_step(index, task, status="scheduled", mode="parallel"))
                parallel_window_counts[request.tool_name] = (
                    parallel_window_counts.get(request.tool_name, 0) + 1
                )
            elif safe_for_parallel and conflicts:
                steps.append(
                    _step(
                        index,
                        task,
                        status="conflict_serialized",
                        mode="serial",
                        reason_codes=("conflict_serialized",),
                    )
                )
                parallel_window_counts = {}
            elif safe_for_parallel and over_parallel_budget:
                steps.append(
                    _step(
                        index,
                        task,
                        status="budget_serialized",
                        mode="serial",
                        reason_codes=("max_parallel_serialized",),
                    )
                )
                parallel_window_counts = {}
            else:
                steps.append(_step(index, task, status="scheduled", mode="serial"))
                parallel_window_counts = {}
            preblocked.append(None)

        return steps, preblocked

    def _manifest_for(self, name: str) -> ToolManifest | None:
        if self.registry is None:
            return None
        registration = self.registry.resolve_registration(name)
        return registration.manifest if registration is not None else None


def _is_parallel_safe(manifest: ToolManifest) -> bool:
    if manifest.dangerous or manifest.mutates_workspace:
        return False
    if manifest.permission not in {"read", "meta"}:
        return False
    if manifest.side_effect_class != "none":
        return False
    if manifest.parallel_safety == "concurrency_safe":
        return manifest.is_concurrency_safe
    return manifest.parallel_safety == "readonly" and manifest.is_concurrency_safe


def _max_parallel(manifest: ToolManifest) -> int | None:
    if manifest.budget.max_parallel is None:
        return None
    return max(manifest.budget.max_parallel, 0)


def _result_reason_code(result: ToolExecutionOutcome) -> str | None:
    if result.status == "ok":
        return None
    if result.reason_code != "tool_executed":
        return result.reason_code
    return f"tool_result_{result.status}"


def _step(
    index: int,
    task: ToolScheduleTask,
    *,
    status: ToolScheduleStepStatus,
    mode: ToolScheduleMode,
    reason_codes: tuple[str, ...] = (),
) -> ToolScheduleStep:
    return ToolScheduleStep(
        sequenceIndex=index,
        toolName=task.request.tool_name,
        taskId=task.task_id,
        conflictKey=task.conflict_key,
        status=status,
        mode=mode,
        reasonCodes=reason_codes,
    )


def _blocked_outcome(
    request: ToolExecutionRequest,
    *,
    reason_code: str,
    metadata: Mapping[str, object] | None = None,
) -> ToolExecutionOutcome:
    return ToolExecutionOutcome(
        status="blocked",
        reasonCode=reason_code,
        result=ToolResult(
            status="blocked",
            metadata=dict(
                metadata
                or {
                    "toolName": request.tool_name,
                    "mode": request.mode,
                    "reason": reason_code,
                }
            ),
        ),
        requestLedgerResult=RequestShapeLedgerResult(
            status="skipped",
            reason="disabled",
            recorded=False,
            diagnostics=RequestLedgerDiagnostics(reasonCodes=(reason_code,)),
            authorityFlags=RequestLedgerAuthorityFlags(),
        ),
        evidenceRecords=(),
        handlerCalled=False,
        executed=False,
        blocking=True,
        authorityFlags=RequestLedgerAuthorityFlags(),
    )


__all__ = [
    "ToolScheduleStep",
    "ToolScheduleTask",
    "ToolScheduler",
    "ToolSchedulerConfig",
    "ToolSchedulerOutcome",
]
