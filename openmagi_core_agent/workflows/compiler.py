from __future__ import annotations

from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from openmagi_core_agent.workflows.registry import WorkflowRegistryEntry, require_digest


TerminalState = Literal["ask_user", "abstain", "block", "fallback"]
ContextProjectionPolicy = Literal["explicit", "last_step_only", "accumulate_verified", "general_chat_history"]
ProjectionPolicy = Literal["structured_claims_only", "artifact_projection", "raw_text_allowed"]
PRIVATE_IDENTIFIER_FRAGMENTS = (
    "author" + "ization",
    "coo" + "kie",
    "to" + "ken",
    "se" + "cret",
    "api_" + "key",
    "pass" + "word",
    "pro" + "mpt",
    "sess" + "ion",
    "priv" + "ate",
)
HARD_DENIED_TOOLS = frozenset(
    {
        "Bash",
        "TestRun",
        "FileWrite",
        "FileEdit",
        "PatchApply",
        "CronCreate",
        "CronUpdate",
        "CronDelete",
        "TaskStop",
        "TaskCreate",
        "TaskWait",
        "MemoryWrite",
        "BrowserClick",
        "BrowserFill",
        "TelegramSend",
        "DiscordSend",
        "FileDeliver",
        "WorkspaceMutate",
    }
)
ALLOWED_BUDGET_KEYS = frozenset({"maxIterations", "wallClockTimeoutMs"})
MAX_ITERATIONS_LIMIT = 100
MAX_WALL_CLOCK_TIMEOUT_MS = 300_000
REQUIRED_HARD_INVARIANTS = frozenset(
    {
        "rawDraftStreamingForbidden",
        "toolhostOnlyExecution",
        "validatorBeforeProjection",
    }
)


class WorkflowCompileInput(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        populate_by_name=True,
        extra="forbid",
        hide_input_in_errors=True,
    )

    workflow_id: str = Field(alias="workflowId")
    version: str
    selected_recipes: tuple[str, ...] = Field(alias="selectedRecipes")
    registered_workflows: tuple[WorkflowRegistryEntry, ...] = Field(default=(), alias="registeredWorkflows")
    tool_allowlist: tuple[str, ...] = Field(default=(), alias="toolAllowlist")
    tool_denylist: tuple[str, ...] = Field(default=(), alias="toolDenylist")
    evidence_requirements: tuple[str, ...] = Field(default=(), alias="evidenceRequirements")
    validator_refs: tuple[str, ...] = Field(default=(), alias="validatorRefs")
    projection_policy: ProjectionPolicy = Field(alias="projectionPolicy")
    repair_policy: str = Field(alias="repairPolicy")
    approval_policy: str = Field(alias="approvalPolicy")
    context_projection_policy: ContextProjectionPolicy = Field(alias="contextProjectionPolicy")
    budgets: Mapping[str, object]
    hard_invariants: Mapping[str, bool] = Field(alias="hardInvariants")
    effective_policy_snapshot_digest: str = Field(alias="effectivePolicySnapshotDigest")
    available_tools: tuple[str, ...] = Field(default=(), alias="availableTools")
    available_validators: tuple[str, ...] = Field(default=(), alias="availableValidators")
    available_renderers: tuple[str, ...] = Field(default=(), alias="availableRenderers")
    evidence_producers: tuple[str, ...] = Field(default=(), alias="evidenceProducers")
    route_precedence: tuple[str, ...] = Field(default=(), alias="routePrecedence")
    no_match_terminal_state: TerminalState | None = Field(default=None, alias="noMatchTerminalState")

    @field_validator(
        "workflow_id",
        "version",
        "repair_policy",
        "approval_policy",
    )
    @classmethod
    def _reject_empty(cls, value: str) -> str:
        _reject_protected_fragments(value, "workflow compile identifier")
        if not value.strip():
            raise ValueError("workflow compile identifiers must be non-empty")
        return value

    @field_validator(
        "selected_recipes",
        "tool_allowlist",
        "tool_denylist",
        "evidence_requirements",
        "validator_refs",
        "available_tools",
        "available_validators",
        "available_renderers",
        "evidence_producers",
        "route_precedence",
        mode="before",
    )
    @classmethod
    def _normalize_tuple(cls, value: object) -> tuple[str, ...]:
        if isinstance(value, str) or not isinstance(value, Iterable):
            raise ValueError("workflow tuples must be arrays of non-empty strings")
        values = tuple(value or ())  # type: ignore[arg-type]
        if any(not isinstance(item, str) or not item.strip() for item in values):
            raise ValueError("workflow tuples must contain non-empty strings")
        for item in values:
            _reject_protected_fragments(item, "workflow tuple item")
        return values

    @field_validator("budgets", mode="before")
    @classmethod
    def _normalize_budgets(cls, value: object) -> Mapping[str, object]:
        return _normalize_budget_mapping(value)

    @field_validator("budgets")
    @classmethod
    def _freeze_budgets(cls, value: Mapping[str, object]) -> Mapping[str, object]:
        return MappingProxyType(dict(value))

    @field_validator("hard_invariants", mode="before")
    @classmethod
    def _normalize_hard_invariants(cls, value: object) -> Mapping[str, bool]:
        if not isinstance(value, Mapping):
            raise ValueError("hardInvariants must be an object of boolean invariants")
        normalized: dict[str, bool] = {}
        for key, enabled in value.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError("hardInvariants keys must be non-empty strings")
            _reject_protected_fragments(key, "hard invariant key")
            if not isinstance(enabled, bool):
                raise ValueError("hardInvariants values must be booleans")
            normalized[key] = enabled
        return normalized

    @field_validator("hard_invariants")
    @classmethod
    def _freeze_hard_invariants(cls, value: Mapping[str, bool]) -> Mapping[str, bool]:
        return MappingProxyType(dict(value))

    @field_validator("selected_recipes")
    @classmethod
    def _require_recipe(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("selectedRecipes must contain at least one recipe")
        return value

    @field_validator("effective_policy_snapshot_digest")
    @classmethod
    def _validate_snapshot_digest(cls, value: str) -> str:
        return require_digest(value, "effectivePolicySnapshotDigest")


class CompiledWorkflowContract(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        populate_by_name=True,
        extra="forbid",
        hide_input_in_errors=True,
    )

    workflow_id: str = Field(alias="workflowId")
    version: str
    selected_recipes: tuple[str, ...] = Field(alias="selectedRecipes")
    registered_workflows: tuple[WorkflowRegistryEntry, ...] = Field(alias="registeredWorkflows")
    tool_allowlist: tuple[str, ...] = Field(alias="toolAllowlist")
    tool_denylist: tuple[str, ...] = Field(alias="toolDenylist")
    evidence_requirements: tuple[str, ...] = Field(alias="evidenceRequirements")
    validator_refs: tuple[str, ...] = Field(alias="validatorRefs")
    context_projection_policy: ContextProjectionPolicy = Field(alias="contextProjectionPolicy")
    output_projection_mode: ProjectionPolicy = Field(alias="outputProjectionMode")
    repair_policy: str = Field(alias="repairPolicy")
    approval_policy: str = Field(alias="approvalPolicy")
    budgets: Mapping[str, object]
    hard_invariants: Mapping[str, bool] = Field(alias="hardInvariants")
    effective_policy_snapshot_digest: str = Field(alias="effectivePolicySnapshotDigest")
    available_tools: tuple[str, ...] = Field(alias="availableTools")
    available_validators: tuple[str, ...] = Field(alias="availableValidators")
    available_renderers: tuple[str, ...] = Field(alias="availableRenderers")
    evidence_producers: tuple[str, ...] = Field(alias="evidenceProducers")
    route_precedence: tuple[str, ...] = Field(alias="routePrecedence")
    no_match_terminal_state: TerminalState | None = Field(alias="noMatchTerminalState")
    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    execution_attached: Literal[False] = Field(default=False, alias="executionAttached")

    @field_validator("budgets", mode="before")
    @classmethod
    def _normalize_budgets(cls, value: object) -> Mapping[str, object]:
        return _normalize_budget_mapping(value)

    @field_validator("budgets")
    @classmethod
    def _freeze_budgets(cls, value: Mapping[str, object]) -> Mapping[str, object]:
        return MappingProxyType(dict(value))

    @field_validator("hard_invariants", mode="before")
    @classmethod
    def _normalize_hard_invariants(cls, value: object) -> Mapping[str, bool]:
        if not isinstance(value, Mapping):
            raise ValueError("hardInvariants must be an object of boolean invariants")
        normalized: dict[str, bool] = {}
        for key, enabled in value.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError("hardInvariants keys must be non-empty strings")
            _reject_protected_fragments(key, "hard invariant key")
            if not isinstance(enabled, bool):
                raise ValueError("hardInvariants values must be booleans")
            normalized[key] = enabled
        return normalized

    @field_validator("hard_invariants")
    @classmethod
    def _freeze_hard_invariants(cls, value: Mapping[str, bool]) -> Mapping[str, bool]:
        return MappingProxyType(dict(value))

    def model_copy(self, *, update: Mapping[str, object] | None = None, deep: bool = False) -> "CompiledWorkflowContract":
        if update:
            raise ValueError("model_copy update is disabled for compiled workflow contracts")
        return type(self).model_validate(self.model_dump(by_alias=True, mode="json"))


class WorkflowValidationVerdict(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        populate_by_name=True,
        extra="forbid",
        hide_input_in_errors=True,
    )

    ok: bool
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")


def compile_governed_workflow(config: WorkflowCompileInput) -> CompiledWorkflowContract:
    return CompiledWorkflowContract(
        workflowId=config.workflow_id,
        version=config.version,
        selectedRecipes=config.selected_recipes,
        registeredWorkflows=config.registered_workflows,
        toolAllowlist=config.tool_allowlist,
        toolDenylist=config.tool_denylist,
        evidenceRequirements=config.evidence_requirements,
        validatorRefs=config.validator_refs,
        contextProjectionPolicy=config.context_projection_policy,
        outputProjectionMode=config.projection_policy,
        repairPolicy=config.repair_policy,
        approvalPolicy=config.approval_policy,
        budgets=dict(config.budgets),
        hardInvariants=dict(config.hard_invariants),
        effectivePolicySnapshotDigest=config.effective_policy_snapshot_digest,
        availableTools=config.available_tools,
        availableValidators=config.available_validators,
        availableRenderers=config.available_renderers,
        evidenceProducers=config.evidence_producers,
        routePrecedence=config.route_precedence,
        noMatchTerminalState=config.no_match_terminal_state,
        trafficAttached=False,
        executionAttached=False,
    )


def validate_compiled_workflow(contract: CompiledWorkflowContract) -> WorkflowValidationVerdict:
    reasons: list[str] = []
    if _has_duplicate_registered_workflow_versions(contract.registered_workflows):
        reasons.append("duplicate_registered_workflow_version")
    for tool in contract.tool_allowlist:
        if tool not in contract.available_tools:
            reasons.append("unknown_tool_ref")
    for recipe_id in contract.selected_recipes:
        matches = tuple(
            entry for entry in contract.registered_workflows if _recipe_matches_workflow_entry(recipe_id, entry)
        )
        if not matches:
            reasons.append("selected_workflow_not_registered")
        elif not any(
            entry.status in {"staging", "active"}
            and entry.compatible_runtime_contract_version == "programmable-determinism.v1"
            for entry in matches
        ):
            reasons.append("selected_workflow_not_runnable")
    for validator in contract.validator_refs:
        if validator not in contract.available_validators:
            reasons.append("unknown_validator_ref")
    if contract.output_projection_mode not in contract.available_renderers:
        reasons.append("unknown_renderer_ref")
    if contract.output_projection_mode == "raw_text_allowed":
        reasons.append("governed_raw_text_projection_forbidden")
    if set(contract.tool_allowlist).intersection(contract.tool_denylist):
        reasons.append("allow_deny_conflict")
    if set(contract.tool_allowlist).intersection(HARD_DENIED_TOOLS):
        reasons.append("hard_denied_tool_allowlisted")
    if contract.no_match_terminal_state is None:
        reasons.append("no_match_terminal_state_missing")
    if "maxIterations" not in contract.budgets:
        reasons.append("loop_limit_missing")
    elif not _is_positive_integer(contract.budgets["maxIterations"], upper_bound=MAX_ITERATIONS_LIMIT):
        reasons.append("loop_limit_invalid")
    if "wallClockTimeoutMs" not in contract.budgets:
        reasons.append("wall_clock_timeout_missing")
    elif not _is_positive_integer(
        contract.budgets["wallClockTimeoutMs"],
        upper_bound=MAX_WALL_CLOCK_TIMEOUT_MS,
    ):
        reasons.append("wall_clock_timeout_invalid")
    if set(contract.budgets) - ALLOWED_BUDGET_KEYS:
        reasons.append("unknown_budget_key")
    missing_producers = set(contract.evidence_requirements) - set(contract.evidence_producers)
    if missing_producers:
        reasons.append("required_evidence_has_no_producer")
    if contract.context_projection_policy == "general_chat_history":
        reasons.append("governed_implicit_history_forbidden")
    missing_invariants = REQUIRED_HARD_INVARIANTS - set(contract.hard_invariants)
    if missing_invariants:
        reasons.append("hard_invariant_missing")
    if any(enabled is False for enabled in contract.hard_invariants.values()):
        reasons.append("hard_invariant_weakened")
    if contract.traffic_attached or contract.execution_attached:
        reasons.append("live_attachment_forbidden_in_contract")
    return WorkflowValidationVerdict(ok=not reasons, reasonCodes=tuple(dict.fromkeys(reasons)))


def _is_positive_integer(value: object, *, upper_bound: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 < value <= upper_bound


def _recipe_matches_workflow_entry(recipe_id: str, entry: WorkflowRegistryEntry) -> bool:
    return recipe_id == f"{entry.workflow_id}.v{entry.version}"


def _has_duplicate_registered_workflow_versions(entries: tuple[WorkflowRegistryEntry, ...]) -> bool:
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        key = (entry.workflow_id, entry.version)
        if key in seen:
            return True
        seen.add(key)
    return False


def _normalize_budget_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("budgets must be an object")
    normalized: dict[str, object] = {}
    for key, budget_value in value.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError("budget keys must be non-empty strings")
        _reject_protected_fragments(key, "budget key")
        normalized[key] = budget_value
    return normalized


def _reject_protected_fragments(value: str, field_name: str) -> None:
    lowered = value.lower()
    if any(fragment in lowered for fragment in PRIVATE_IDENTIFIER_FRAGMENTS):
        raise ValueError(f"{field_name} contains protected runtime data marker")
