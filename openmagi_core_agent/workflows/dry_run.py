from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from openmagi_core_agent.workflows.compiler import (
    CompiledWorkflowContract,
    validate_compiled_workflow,
)


class WorkflowDryRunReport(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        populate_by_name=True,
        extra="forbid",
        hide_input_in_errors=True,
    )

    ok: bool
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    selected_recipe_ids: tuple[str, ...] = Field(alias="selectedRecipeIds")
    effective_policy_snapshot_digest: str = Field(alias="effectivePolicySnapshotDigest")
    enabled_validators: tuple[str, ...] = Field(alias="enabledValidators")
    available_tools: tuple[str, ...] = Field(alias="availableTools")
    denied_tools: tuple[str, ...] = Field(alias="deniedTools")
    approval_gates: tuple[str, ...] = Field(default=(), alias="approvalGates")
    expected_evidence_types: tuple[str, ...] = Field(alias="expectedEvidenceTypes")
    context_projection_mode: str = Field(alias="contextProjectionMode")
    output_projection_mode: str = Field(alias="outputProjectionMode")
    budget_limits: dict[str, object] = Field(alias="budgetLimits")
    predicted_terminal_states: tuple[str, ...] = Field(alias="predictedTerminalStates")
    model_call_attempted: Literal[False] = Field(default=False, alias="modelCallAttempted")
    tool_call_attempted: Literal[False] = Field(default=False, alias="toolCallAttempted")
    network_attempted: Literal[False] = Field(default=False, alias="networkAttempted")
    filesystem_attempted: Literal[False] = Field(default=False, alias="filesystemAttempted")

    def model_copy(self, *, update: Mapping[str, object] | None = None, deep: bool = False) -> "WorkflowDryRunReport":
        if update:
            raise ValueError("model_copy update is disabled for workflow dry-run reports")
        return type(self).model_validate(self.model_dump(by_alias=True, mode="json"))


def dry_run_governed_workflow(contract: CompiledWorkflowContract) -> WorkflowDryRunReport:
    verdict = validate_compiled_workflow(contract)
    terminals = (contract.no_match_terminal_state,) if contract.no_match_terminal_state else ("block",)
    return WorkflowDryRunReport(
        ok=verdict.ok,
        reasonCodes=verdict.reason_codes,
        selectedRecipeIds=contract.selected_recipes,
        effectivePolicySnapshotDigest=contract.effective_policy_snapshot_digest,
        enabledValidators=contract.validator_refs,
        availableTools=contract.available_tools,
        deniedTools=contract.tool_denylist,
        approvalGates=(contract.approval_policy,),
        expectedEvidenceTypes=contract.evidence_requirements,
        contextProjectionMode=contract.context_projection_policy,
        outputProjectionMode=contract.output_projection_mode,
        budgetLimits=dict(contract.budgets),
        predictedTerminalStates=terminals,
        modelCallAttempted=False,
        toolCallAttempted=False,
        networkAttempted=False,
        filesystemAttempted=False,
    )
