from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field

from magi_agent.channels.research_command import prepare_research_command
from magi_agent.channels.workflow_classifier import (
    ClassifierPort,
    classify_workflow_eligibility,
)
from magi_agent.channels.workflow_confirm_store import PendingConfirmationStore
from magi_agent.channels.workflow_gate import (
    channel_workflows_enabled,
    executor_enabled,
)
from magi_agent.channels.workflow_routing import decide_workflow_route
from magi_agent.harness.workflow_executor import execute_workflow

__all__ = [
    "OrchestratorOutcome",
    "WorkflowOrchestratorResult",
    "route_inbound",
    "start_research",
    "resolve_confirmation",
]

OrchestratorOutcome = Literal[
    "normal_llm", "awaiting_confirmation", "executed", "declined", "not_pending"
]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)

_AFFIRM = frozenset({"예", "네", "yes", "y", "ㅇㅇ", "ok", "진행", "ㄱㄱ"})


class WorkflowOrchestratorResult(BaseModel):
    model_config = _MODEL_CONFIG

    outcome: OrchestratorOutcome
    message: str = ""
    executor_status: str | None = Field(default=None, alias="executorStatus")

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: Any) -> Self:
        raise TypeError("model_construct is disabled for WorkflowOrchestratorResult")


def route_inbound(
    message_text: str,
    *,
    session_id: str,
    classifier: ClassifierPort,
    store: PendingConfirmationStore,
    per_child_token_estimate: int,
    model_microcents_per_1k: int,
) -> WorkflowOrchestratorResult:
    """Auto-detect path. If the channel feature is off or the message isn't
    workflow-eligible, signal a normal LLM turn. Otherwise prepare the research
    command, stash it as pending confirmation, and return the confirm prompt."""
    if not channel_workflows_enabled():
        return WorkflowOrchestratorResult(outcome="normal_llm")
    eligibility = classify_workflow_eligibility(message_text, classifier=classifier)
    if not eligibility.eligible:
        return WorkflowOrchestratorResult(outcome="normal_llm")
    pending = prepare_research_command(
        query=message_text,
        per_child_token_estimate=per_child_token_estimate,
        model_microcents_per_1k=model_microcents_per_1k,
    )
    store.put(session_id, pending)
    return WorkflowOrchestratorResult(
        outcome="awaiting_confirmation", message=pending.confirm_prompt
    )


def start_research(
    query: str,
    *,
    session_id: str,
    store: PendingConfirmationStore,
    per_child_token_estimate: int,
    model_microcents_per_1k: int,
) -> WorkflowOrchestratorResult:
    """Explicit /research path. Skips the classifier but still requires the
    channel feature flag. Prepares + stashes pending confirmation."""
    if not channel_workflows_enabled():
        return WorkflowOrchestratorResult(outcome="normal_llm")
    pending = prepare_research_command(
        query=query,
        per_child_token_estimate=per_child_token_estimate,
        model_microcents_per_1k=model_microcents_per_1k,
    )
    store.put(session_id, pending)
    return WorkflowOrchestratorResult(
        outcome="awaiting_confirmation", message=pending.confirm_prompt
    )


async def resolve_confirmation(
    answer_text: str,
    *,
    session_id: str,
    store: PendingConfirmationStore,
) -> WorkflowOrchestratorResult:
    """Handle the user's yes/no on a pending confirmation. On affirmative AND
    executor enabled -> route + execute the stored workflow. Otherwise decline.
    The executor self-gates on MAGI_WORKFLOW_EXECUTOR_ENABLED, so when the
    executor is off this never dispatches children."""
    pending = store.pop(session_id)
    if pending is None:
        return WorkflowOrchestratorResult(outcome="not_pending")
    if answer_text.strip().lower() not in {a.lower() for a in _AFFIRM}:
        return WorkflowOrchestratorResult(outcome="declined", message="조사를 취소했어요.")
    decision = decide_workflow_route(eligible=True, confirmed=True, enabled=executor_enabled())
    if not decision.routed:
        return WorkflowOrchestratorResult(
            outcome="declined", message="지금은 실행할 수 없어요."
        )
    result = await execute_workflow(
        pending.compiled_bundle.contract,
        cross_review_step=pending.compiled_bundle.cross_review_step,
    )
    return WorkflowOrchestratorResult(outcome="executed", executorStatus=result.status)
