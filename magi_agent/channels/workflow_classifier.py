from __future__ import annotations

from typing import Any, Protocol, Self, get_args

from pydantic import BaseModel, ConfigDict, Field

from magi_agent.harness.inference_scaling import TaskKind

__all__ = [
    "ClassifierPort",
    "WorkflowEligibility",
    "classify_workflow_eligibility",
]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)

_ELIGIBLE_KINDS: frozenset[str] = frozenset(
    {"source_sensitive_research", "complex_synthesis", "ambiguous_architecture"}
)
_VALID_KINDS: frozenset[str] = frozenset(get_args(TaskKind))


class ClassifierPort(Protocol):
    def classify(self, message_text: str) -> str: ...


class WorkflowEligibility(BaseModel):
    model_config = _MODEL_CONFIG

    eligible: bool
    task_kind: str = Field(alias="taskKind")

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: Any) -> Self:
        raise TypeError("model_construct is disabled for WorkflowEligibility")


def classify_workflow_eligibility(
    message_text: str,
    *,
    classifier: ClassifierPort,
) -> WorkflowEligibility:
    """Map an inbound message to workflow-eligibility via an injected classifier
    port. Conservative: unknown kinds and all non-eligible kinds → not eligible.
    The real classifier (a Haiku/LLM call) is injected; tests use a fake."""
    raw = classifier.classify(message_text)
    kind = raw if raw in _VALID_KINDS else "general"
    return WorkflowEligibility(eligible=kind in _ELIGIBLE_KINDS, taskKind=kind)
