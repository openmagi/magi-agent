from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Literal, Self

from pydantic import BaseModel, Field, model_validator

from magi_agent.ops.authority import FalseOnlyAuthorityModel


AgentRole = Literal["general", "coding", "research"]
RunOn = Literal["main", "child"]
ToolClass = Literal["read_only", "pure_compute", "stateful", "mutating", "external_side_effect"]
LimitClass = Literal["read_only", "pure_compute", "stateful", "mutating", "external_side_effect", "turn"]
SideEffectClass = Literal["none", "read_only", "local_process", "local_workspace", "external"]

ATTACHMENT_FLAGS: tuple[str, ...] = (
    "trafficAttached",
    "executionAttached",
    "runnerAttached",
    "routeAttached",
    "schedulerAttached",
    "toolExecutionAttached",
    "childExecutionAttached",
    "workspaceAttached",
    "canaryAttached",
)

_ATTACHMENT_FIELD_NAMES: tuple[str, ...] = (
    "traffic_attached",
    "execution_attached",
    "runner_attached",
    "route_attached",
    "scheduler_attached",
    "tool_execution_attached",
    "child_execution_attached",
    "workspace_attached",
    "canary_attached",
)
_HARD_TOOL_CLASSES = frozenset(("stateful", "mutating", "external_side_effect"))
_NON_HARD_TOOL_CLASSES = frozenset(("read_only", "pure_compute"))
_SAFE_SIDE_EFFECT_CLASSES = frozenset(("none", "read_only"))
_MAX_CONCURRENT_LIMIT = 64
_MAX_TIMEOUT_MS = 600_000
_MAX_PUBLIC_TEXT_CHARS = 400
_PUBLIC_REDACTION = "[REDACTED]"
_SECRET_KEY_PATTERN = (
    r"authorization|proxy[_-]?authorization|cookie|token|access[_-]?token|"
    r"refresh[_-]?token|session[_-]?token|auth[_-]?token|api[_-]?key|"
    r"openai[_-]?api[_-]?key|github[_-]?token|private[_-]?key|private[_-]?key|"
    r"service[_-]?role|service[_-]?role[_-]?key|secret|password"
)
_SECRET_ASSIGNMENT_RE = re.compile(
    rf"(?i)\b({_SECRET_KEY_PATTERN})\b\s*[:=]\s*"
    rf".*?(?=(?:\s+\b(?:{_SECRET_KEY_PATTERN})\b\s*[:=])|[,;\r\n]|$)"
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_BASIC_RE = re.compile(r"(?i)\bBasic\s+[A-Za-z0-9._~+/=-]+")
_PROVIDER_TOKEN_RE = re.compile(
    r"\b(?:sk-(?:proj-)?[A-Za-z0-9][A-Za-z0-9._-]*|"
    r"gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+)\b"
)
_PRIVATE_KEY_BLOCK_RE = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.IGNORECASE | re.DOTALL,
)


class _StrictFrozenModel(FalseOnlyAuthorityModel):
    """Parallel-execution frozen base.

    Inherits force-false validator/serializer/construct/copy from
    FalseOnlyAuthorityModel; preserves a per-class ``__getattr__`` shim that
    permits looking up fields by their camelCase alias (defense-in-depth on
    top of ``populate_by_name=True`` so any consumer fetching the
    alias-named attribute keeps working).
    """

    def __getattr__(self, item: str) -> Any:
        alias_to_name = {
            field.alias: name
            for name, field in self.__class__.model_fields.items()
            if field.alias is not None
        }
        if item in alias_to_name:
            return getattr(self, alias_to_name[item])
        return super().__getattr__(item)


class ParallelExecutionScope(_StrictFrozenModel):
    run_on: RunOn = Field(alias="runOn")
    agent_role: AgentRole = Field(alias="agentRole")
    spawn_depth: int = Field(alias="spawnDepth")

    @model_validator(mode="after")
    def _validate_scope(self) -> Self:
        if self.spawn_depth < 0:
            raise ValueError("spawnDepth must be non-negative")
        if self.run_on == "main" and self.spawn_depth != 0:
            raise ValueError("main scope must use spawnDepth=0")
        if self.run_on == "child" and self.spawn_depth <= 0:
            raise ValueError("child scope must use spawnDepth greater than 0")
        return self


class ToolLimitMetadata(_StrictFrozenModel):
    tool_class: LimitClass = Field(alias="toolClass")
    max_concurrent: int = Field(alias="maxConcurrent")
    metadata_only: Literal[True] = Field(default=True, alias="metadataOnly")
    scheduler_attached: Literal[False] = Field(default=False, alias="schedulerAttached")

    @model_validator(mode="after")
    def _validate_limit(self) -> Self:
        if not 1 <= self.max_concurrent <= _MAX_CONCURRENT_LIMIT:
            raise ValueError("maxConcurrent must be between 1 and 64")
        return self


class ParallelToolPolicyInput(_StrictFrozenModel):
    tool_name: str = Field(alias="toolName")
    tool_class: ToolClass = Field(alias="toolClass")
    side_effect_class: SideEffectClass = Field(alias="sideEffectClass")
    manifest_parallel_safety_proof: bool = Field(default=False, alias="manifestParallelSafetyProof")
    requested_parallel_eligible: bool = Field(default=False, alias="requestedParallelEligible")
    opt_out_non_hard_parallel: bool = Field(default=False, alias="optOutNonHardParallel")
    workspace_adoption_available: bool = Field(default=False, alias="workspaceAdoptionAvailable")
    scope: ParallelExecutionScope
    tool_class_limit: ToolLimitMetadata = Field(alias="toolClassLimit")
    turn_limit: ToolLimitMetadata = Field(alias="turnLimit")

    @model_validator(mode="after")
    def _validate_policy_input(self) -> Self:
        if not self.tool_name.strip():
            raise ValueError("toolName must be non-empty")
        if self.tool_class == "pure_compute" and self.side_effect_class != "none":
            raise ValueError("pure_compute tools must declare sideEffectClass=none")
        if self.tool_class == "read_only" and self.side_effect_class not in {"none", "read_only"}:
            raise ValueError("read_only tools cannot declare mutating side effects")
        if self.tool_class in _HARD_TOOL_CLASSES and self.requested_parallel_eligible:
            if not self.manifest_parallel_safety_proof:
                raise ValueError("hard-safety tools need manifest proof before parallel metadata")
        if self.tool_class_limit.tool_class != self.tool_class:
            raise ValueError("toolClassLimit must match toolClass")
        if self.turn_limit.tool_class != "turn":
            raise ValueError("turnLimit must use toolClass=turn")
        return self


class ParallelToolPolicyDecision(_StrictFrozenModel):
    tool_name: str = Field(alias="toolName")
    tool_class: ToolClass = Field(alias="toolClass")
    side_effect_class: SideEffectClass = Field(alias="sideEffectClass")
    scope: ParallelExecutionScope
    tool_class_limit: ToolLimitMetadata = Field(alias="toolClassLimit")
    turn_limit: ToolLimitMetadata = Field(alias="turnLimit")
    parallel_eligible: bool = Field(alias="parallelEligible")
    serialization_required: bool = Field(alias="serializationRequired")
    hard_safety_blocked: bool = Field(alias="hardSafetyBlocked")
    non_hard_parallel_opted_out: bool = Field(default=False, alias="nonHardParallelOptedOut")
    hard_safety_bypassable: Literal[False] = Field(default=False, alias="hardSafetyBypassable")
    default_enabled: Literal[False] = Field(default=False, alias="defaultEnabled")
    metadata_only: Literal[True] = Field(default=True, alias="metadataOnly")
    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    execution_attached: Literal[False] = Field(default=False, alias="executionAttached")
    runner_attached: Literal[False] = Field(default=False, alias="runnerAttached")
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")
    scheduler_attached: Literal[False] = Field(default=False, alias="schedulerAttached")
    tool_execution_attached: Literal[False] = Field(default=False, alias="toolExecutionAttached")
    child_execution_attached: Literal[False] = Field(default=False, alias="childExecutionAttached")
    workspace_attached: Literal[False] = Field(default=False, alias="workspaceAttached")
    canary_attached: Literal[False] = Field(default=False, alias="canaryAttached")

    @model_validator(mode="after")
    def _validate_detached(self) -> Self:
        _reject_any_attachment(self)
        if self.parallel_eligible and self.serialization_required:
            raise ValueError("parallel-eligible decisions cannot require serialization")
        if self.hard_safety_blocked and not self.serialization_required:
            raise ValueError("hard-safety blocked decisions must require serialization")
        return self


class ParallelBatchItemMetadata(_StrictFrozenModel):
    tool_name: str = Field(alias="toolName")
    input_order: int = Field(alias="inputOrder")

    @model_validator(mode="after")
    def _validate_item(self) -> Self:
        if not self.tool_name.strip():
            raise ValueError("toolName must be non-empty")
        if self.input_order < 0:
            raise ValueError("inputOrder must be non-negative")
        return self


class ParallelBatchMetadata(_StrictFrozenModel):
    batch_id: str = Field(alias="batchId")
    turn_id: str = Field(alias="turnId")
    batch_ordinal: int = Field(alias="batchOrdinal")
    items: tuple[ParallelBatchItemMetadata, ...]
    stable_ordering: Literal[True] = Field(default=True, alias="stableOrdering")
    metadata_only: Literal[True] = Field(default=True, alias="metadataOnly")
    scheduler_attached: Literal[False] = Field(default=False, alias="schedulerAttached")
    tool_execution_attached: Literal[False] = Field(default=False, alias="toolExecutionAttached")

    @property
    def ordered_tool_names(self) -> tuple[str, ...]:
        return tuple(item.tool_name for item in self.items)

    @classmethod
    def build(
        cls,
        *,
        turn_id: str,
        tool_names: Sequence[str],
        batch_ordinal: int,
    ) -> ParallelBatchMetadata:
        items = tuple(
            ParallelBatchItemMetadata(toolName=tool_name, inputOrder=index)
            for index, tool_name in enumerate(tool_names)
        )
        stable_payload = "\x1f".join((turn_id, str(batch_ordinal), *tool_names))
        batch_id = "parallel-batch-" + hashlib.sha256(stable_payload.encode("utf-8")).hexdigest()[:16]
        return cls(
            batchId=batch_id,
            turnId=turn_id,
            batchOrdinal=batch_ordinal,
            items=items,
        )

    @model_validator(mode="after")
    def _validate_batch(self) -> Self:
        if not self.turn_id.strip() or not self.batch_id.strip():
            raise ValueError("batch identifiers must be non-empty")
        if self.batch_ordinal < 0:
            raise ValueError("batchOrdinal must be non-negative")
        if tuple(item.input_order for item in self.items) != tuple(range(len(self.items))):
            raise ValueError("batch item inputOrder values must be contiguous and stable")
        return self


class ToolFailureMetadata(_StrictFrozenModel):
    tool_name: str = Field(alias="toolName")
    input_order: int = Field(alias="inputOrder")
    public_summary: str = Field(alias="publicSummary")
    metadata_only: Literal[True] = Field(default=True, alias="metadataOnly")

    @model_validator(mode="after")
    def _validate_failure(self) -> Self:
        if not self.tool_name.strip():
            raise ValueError("toolName must be non-empty")
        if self.input_order < 0:
            raise ValueError("inputOrder must be non-negative")
        object.__setattr__(self, "public_summary", _sanitize_public_text(self.public_summary))
        return self


class FailureAggregationMetadata(_StrictFrozenModel):
    batch_id: str = Field(alias="batchId")
    failures: tuple[ToolFailureMetadata, ...]
    public_summary: str = Field(alias="publicSummary")
    stable_ordering: Literal[True] = Field(default=True, alias="stableOrdering")
    metadata_only: Literal[True] = Field(default=True, alias="metadataOnly")
    execution_attached: Literal[False] = Field(default=False, alias="executionAttached")
    tool_execution_attached: Literal[False] = Field(default=False, alias="toolExecutionAttached")

    @classmethod
    def from_failures(
        cls,
        *,
        batch_id: str,
        failures: Sequence[ToolFailureMetadata],
    ) -> FailureAggregationMetadata:
        ordered_failures = tuple(sorted(failures, key=lambda failure: failure.input_order))
        summary = "; ".join(
            f"{failure.tool_name}: {failure.public_summary}" for failure in ordered_failures
        )
        return cls(
            batchId=batch_id,
            failures=ordered_failures,
            publicSummary=_sanitize_public_text(summary),
        )

    @model_validator(mode="after")
    def _validate_aggregation(self) -> Self:
        if not self.batch_id.strip():
            raise ValueError("batchId must be non-empty")
        object.__setattr__(self, "public_summary", _sanitize_public_text(self.public_summary))
        return self


class ToolTimeoutBudgetMetadata(_StrictFrozenModel):
    scope: Literal["tool_class", "batch"]
    timeout_ms: int = Field(alias="timeoutMs")
    tool_class: LimitClass | None = Field(default=None, alias="toolClass")
    batch_id: str | None = Field(default=None, alias="batchId")
    metadata_only: Literal[True] = Field(default=True, alias="metadataOnly")
    scheduler_attached: Literal[False] = Field(default=False, alias="schedulerAttached")
    execution_attached: Literal[False] = Field(default=False, alias="executionAttached")
    tool_execution_attached: Literal[False] = Field(default=False, alias="toolExecutionAttached")

    @model_validator(mode="after")
    def _validate_timeout_budget(self) -> Self:
        if not 1 <= self.timeout_ms <= _MAX_TIMEOUT_MS:
            raise ValueError("timeoutMs must be between 1 and 600000")
        if self.scope == "tool_class" and self.tool_class is None:
            raise ValueError("toolClass is required for tool_class timeout budgets")
        if self.scope == "batch" and not (self.batch_id and self.batch_id.strip()):
            raise ValueError("batchId is required for batch timeout budgets")
        _reject_any_attachment(self)
        return self


class ParallelProgressSummaryMetadata(_StrictFrozenModel):
    batch_id: str = Field(alias="batchId")
    queued: int
    running: int
    completed: int
    failed: int
    total: int
    ordered_tool_names: tuple[str, ...] = Field(alias="orderedToolNames")
    item_refs: tuple[str, ...] = Field(alias="itemRefs")
    public_summary: str = Field(alias="publicSummary")
    timeout_budgets: tuple[ToolTimeoutBudgetMetadata, ...] = Field(
        default_factory=tuple,
        alias="timeoutBudgets",
    )
    stable_ordering: Literal[True] = Field(default=True, alias="stableOrdering")
    metadata_only: Literal[True] = Field(default=True, alias="metadataOnly")
    execution_attached: Literal[False] = Field(default=False, alias="executionAttached")
    scheduler_attached: Literal[False] = Field(default=False, alias="schedulerAttached")
    tool_execution_attached: Literal[False] = Field(default=False, alias="toolExecutionAttached")
    child_execution_attached: Literal[False] = Field(default=False, alias="childExecutionAttached")
    workspace_attached: Literal[False] = Field(default=False, alias="workspaceAttached")

    @classmethod
    def from_batch(
        cls,
        batch: ParallelBatchMetadata,
        *,
        queued: int,
        running: int,
        completed: int,
        failed: int,
        publicSummary: str,
        timeoutBudgets: Sequence[ToolTimeoutBudgetMetadata] = (),
    ) -> ParallelProgressSummaryMetadata:
        canonical_batch = ParallelBatchMetadata.model_validate(_canonical_model_data(batch))
        return cls(
            batchId=canonical_batch.batch_id,
            queued=queued,
            running=running,
            completed=completed,
            failed=failed,
            total=len(canonical_batch.items),
            orderedToolNames=canonical_batch.ordered_tool_names,
            itemRefs=tuple(
                f"{item.input_order}:{item.tool_name}" for item in canonical_batch.items
            ),
            publicSummary=_sanitize_public_text(publicSummary),
            timeoutBudgets=tuple(timeoutBudgets),
        )

    @model_validator(mode="after")
    def _validate_progress_summary(self) -> Self:
        if not self.batch_id.strip():
            raise ValueError("batchId must be non-empty")
        counts = (self.queued, self.running, self.completed, self.failed, self.total)
        if any(count < 0 for count in counts):
            raise ValueError("progress counts must be non-negative")
        if self.queued + self.running + self.completed + self.failed != self.total:
            raise ValueError("progress counts must add up to total")
        if len(self.ordered_tool_names) != self.total or len(self.item_refs) != self.total:
            raise ValueError("progress item metadata must match total")
        object.__setattr__(self, "public_summary", _sanitize_public_text(self.public_summary))
        _reject_any_attachment(self)
        return self


class SpeculativePlanningEligibilityMetadata(_StrictFrozenModel):
    verifier_can_cheaply_reject_bad_drafts: bool = Field(alias="verifierCanCheaplyRejectBadDrafts")
    side_effect_class: SideEffectClass = Field(alias="sideEffectClass")
    max_drafts: int = Field(default=1, alias="maxDrafts")
    eligible: bool = False
    default_enabled: Literal[False] = Field(default=False, alias="defaultEnabled")
    metadata_only: Literal[True] = Field(default=True, alias="metadataOnly")
    runner_attached: Literal[False] = Field(default=False, alias="runnerAttached")
    execution_attached: Literal[False] = Field(default=False, alias="executionAttached")

    @model_validator(mode="after")
    def _validate_planning(self) -> Self:
        if self.max_drafts < 1:
            raise ValueError("maxDrafts must be at least 1")
        requested = self.max_drafts > 1
        if requested and self.verifier_can_cheaply_reject_bad_drafts:
            if self.side_effect_class not in _SAFE_SIDE_EFFECT_CLASSES:
                raise ValueError("speculative planning requires no or read-only side effects")
            object.__setattr__(self, "eligible", True)
        else:
            object.__setattr__(self, "eligible", False)
        return self


class SpeculativeReasoningExperimentMetadata(_StrictFrozenModel):
    benchmark_id: str = Field(alias="benchmarkId")
    max_variants: int = Field(alias="maxVariants")
    verifier_can_rank_outcomes: bool = Field(alias="verifierCanRankOutcomes")
    eligible: bool = False
    benchmark_only: Literal[True] = Field(default=True, alias="benchmarkOnly")
    default_enabled: Literal[False] = Field(default=False, alias="defaultEnabled")
    runtime_rollout_attached: Literal[False] = Field(default=False, alias="runtimeRolloutAttached")
    runner_attached: Literal[False] = Field(default=False, alias="runnerAttached")
    execution_attached: Literal[False] = Field(default=False, alias="executionAttached")
    canary_attached: Literal[False] = Field(default=False, alias="canaryAttached")

    @model_validator(mode="after")
    def _validate_experiment(self) -> Self:
        if not self.benchmark_id.strip():
            raise ValueError("benchmarkId must be non-empty")
        if self.max_variants < 1:
            raise ValueError("maxVariants must be at least 1")
        object.__setattr__(
            self,
            "eligible",
            self.max_variants > 1 and self.verifier_can_rank_outcomes,
        )
        return self


class SpeculativeCodingEligibilityMetadata(_StrictFrozenModel):
    workspace_isolation_represented: bool = Field(alias="workspaceIsolationRepresented")
    workspace_adoption_represented: bool = Field(alias="workspaceAdoptionRepresented")
    future_integration_approved: bool = Field(default=False, alias="futureIntegrationApproved")
    eligible: bool = False
    blocked_reason: str = Field(default="workspace_adoption_not_represented", alias="blockedReason")
    hard_safety_bypassable: Literal[False] = Field(default=False, alias="hardSafetyBypassable")
    default_enabled: Literal[False] = Field(default=False, alias="defaultEnabled")
    metadata_only: Literal[True] = Field(default=True, alias="metadataOnly")
    workspace_attached: Literal[False] = Field(default=False, alias="workspaceAttached")
    child_execution_attached: Literal[False] = Field(default=False, alias="childExecutionAttached")

    @model_validator(mode="after")
    def _validate_coding(self) -> Self:
        if self.workspace_isolation_represented and self.workspace_adoption_represented:
            if self.future_integration_approved:
                object.__setattr__(self, "eligible", True)
                object.__setattr__(self, "blocked_reason", "")
            else:
                object.__setattr__(self, "blocked_reason", "future_integration_not_approved")
        elif not self.workspace_isolation_represented:
            object.__setattr__(self, "blocked_reason", "workspace_isolation_not_represented")
        else:
            object.__setattr__(self, "blocked_reason", "workspace_adoption_not_represented")
        return self


def build_parallel_tool_policy_decision(
    policy_input: ParallelToolPolicyInput,
) -> ParallelToolPolicyDecision:
    data = ParallelToolPolicyInput.model_validate(_canonical_model_data(policy_input))
    non_hard = data.tool_class in _NON_HARD_TOOL_CLASSES
    hard = data.tool_class in _HARD_TOOL_CLASSES

    non_hard_eligible = (
        non_hard
        and data.manifest_parallel_safety_proof
        and not data.opt_out_non_hard_parallel
        and data.side_effect_class in _SAFE_SIDE_EFFECT_CLASSES
    )
    hard_eligible = (
        hard
        and data.requested_parallel_eligible
        and data.manifest_parallel_safety_proof
        and data.workspace_adoption_available
    )
    parallel_eligible = non_hard_eligible or hard_eligible
    hard_blocked = hard and not hard_eligible

    return ParallelToolPolicyDecision(
        toolName=data.tool_name,
        toolClass=data.tool_class,
        sideEffectClass=data.side_effect_class,
        scope=data.scope,
        toolClassLimit=data.tool_class_limit,
        turnLimit=data.turn_limit,
        parallelEligible=parallel_eligible,
        serializationRequired=not parallel_eligible,
        hardSafetyBlocked=hard_blocked,
        nonHardParallelOptedOut=data.opt_out_non_hard_parallel if non_hard else False,
    )


def _canonical_model_data(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(by_alias=True, mode="python", warnings=False)
    if isinstance(value, Mapping):
        return {key: _canonical_model_data(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_canonical_model_data(item) for item in value]
    return value


def _reject_any_attachment(model: BaseModel) -> None:
    enabled = [
        field_name
        for field_name in _ATTACHMENT_FIELD_NAMES
        if hasattr(model, field_name) and getattr(model, field_name) is not False
    ]
    if enabled:
        raise ValueError("parallel execution metadata must remain detached")


def _sanitize_public_text(value: str) -> str:
    redacted = _PRIVATE_KEY_BLOCK_RE.sub(_PUBLIC_REDACTION, value)
    redacted = _BASIC_RE.sub(f"Basic {_PUBLIC_REDACTION}", redacted)
    redacted = _BEARER_RE.sub(f"Bearer {_PUBLIC_REDACTION}", redacted)
    redacted = _PROVIDER_TOKEN_RE.sub(_PUBLIC_REDACTION, redacted)
    redacted = _SECRET_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}={_PUBLIC_REDACTION}", redacted)
    redacted = " ".join(redacted.split())
    if len(redacted) > _MAX_PUBLIC_TEXT_CHARS:
        return redacted[: _MAX_PUBLIC_TEXT_CHARS - 3].rstrip() + "..."
    return redacted
