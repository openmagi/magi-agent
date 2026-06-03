"""Learning KB — data models.

All models are frozen pydantic v2 with camelCase aliases, matching
the conventions in magi_agent.recipes.first_party.self_improvement
and magi_agent.memory.contracts.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


LearningKind = Literal["rule", "example", "eval"]
LearningStatus = Literal["proposed", "active", "archived"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
)


class LearningScope(BaseModel):
    """Generic routing keys — kept intentionally domain-agnostic."""

    model_config = _MODEL_CONFIG

    task_kind: str = Field(alias="taskKind")
    tags: tuple[str, ...] = ()
    channel: str | None = None


class Provenance(BaseModel):
    model_config = _MODEL_CONFIG

    session_ids: tuple[str, ...] = Field(alias="sessionIds")
    derived_by: Literal["reflection", "user"] = Field(alias="derivedBy")
    created_at: str = Field(alias="createdAt")


class LearningStats(BaseModel):
    model_config = _MODEL_CONFIG

    applied: int = 0
    eval_score: float | None = Field(default=None, alias="evalScore")
    last_used: str | None = Field(default=None, alias="lastUsed")


class LearningItem(BaseModel):
    model_config = _MODEL_CONFIG

    id: str
    tenant_id: str = Field(default="local", alias="tenantId")
    kind: LearningKind
    status: LearningStatus = "proposed"
    scope: LearningScope
    content: Mapping[str, object]
    rationale: str
    provenance: Provenance
    version: int = 1
    supersedes: str | None = None
    embedding_ref: str | None = Field(default=None, alias="embeddingRef")
    stats: LearningStats = Field(default_factory=LearningStats)
    eval_observation_ref: str | None = Field(default=None, alias="evalObservationRef")
    approval_ref: str | None = Field(default=None, alias="approvalRef")

    @model_validator(mode="after")
    def _validate_content_per_kind(self) -> "LearningItem":
        content = dict(self.content)
        kind = self.kind

        if kind == "rule":
            missing = [k for k in ("when", "then") if k not in content]
            if missing:
                raise ValueError(
                    f"rule content must include keys 'when' and 'then'; "
                    f"missing: {missing}"
                )
        elif kind == "example":
            missing = [k for k in ("situation", "behavior") if k not in content]
            if missing:
                raise ValueError(
                    f"example content must include keys 'situation' and 'behavior'; "
                    f"missing: {missing}"
                )
        elif kind == "eval":
            missing = [k for k in ("input", "expected") if k not in content]
            if missing:
                raise ValueError(
                    f"eval content must include keys 'input' and 'expected'; "
                    f"missing: {missing}"
                )

        return self


__all__ = [
    "LearningItem",
    "LearningKind",
    "LearningScope",
    "LearningStats",
    "LearningStatus",
    "Provenance",
]
