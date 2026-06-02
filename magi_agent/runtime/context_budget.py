from __future__ import annotations

import re
from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator

from magi_agent.runtime.model_tiers import ModelTier, ModelUsagePhase


ContextRefKind: TypeAlias = Literal[
    "source",
    "summary",
    "memory",
    "evidence",
    "tool_result",
    "artifact",
    "control",
]
MemoryMode: TypeAlias = Literal["normal", "read_only", "incognito"]
ContextBudgetStrategy: TypeAlias = Literal[
    "refs_only_with_chunk_summaries",
    "refs_with_summaries",
    "expanded_refs_with_summaries",
]


_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_PUBLIC_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,180}$")
_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_SECRET_REF_RE = re.compile(
    r"(?:"
    r"\s|"
    r"[\\/'\"`$=;|&<>]|"
    r"\.\.|"
    r"://|"
    r"^sk-|"
    r"^xox[a-z]-|"
    r"^gh[opusr]_|"
    r"^github_pat_|"
    r"^AIza|"
    r"\bbearer\b|"
    r"api[_-]?key|"
    r"secret|"
    r"token|"
    r"password|"
    r"private[_-]?key"
    r")",
    re.IGNORECASE,
)


class ContextBudgetRequest(BaseModel):
    model_config = _MODEL_CONFIG

    recipe_ids: tuple[str, ...] = Field(alias="recipeIds")
    model_tier: ModelTier = Field(alias="modelTier")
    phase: ModelUsagePhase
    source_refs: tuple[str, ...] = Field(default=(), alias="sourceRefs")
    summary_refs: tuple[str, ...] = Field(default=(), alias="summaryRefs")
    memory_refs: tuple[str, ...] = Field(default=(), alias="memoryRefs")
    evidence_refs: tuple[str, ...] = Field(default=(), alias="evidenceRefs")
    tool_result_refs: tuple[str, ...] = Field(default=(), alias="toolResultRefs")
    artifact_refs: tuple[str, ...] = Field(default=(), alias="artifactRefs")
    control_refs: tuple[str, ...] = Field(default=(), alias="controlRefs")
    compaction_boundary_refs: tuple[str, ...] = Field(
        default=(),
        alias="compactionBoundaryRefs",
    )
    raw_input_bytes: int = Field(default=0, ge=0, alias="rawInputBytes")
    memory_mode: MemoryMode = Field(default="normal", alias="memoryMode")
    allow_raw_context_for_local_test: bool = Field(
        default=False,
        alias="allowRawContextForLocalTest",
    )
    raw_context_digest: str | None = Field(default=None, alias="rawContextDigest")

    @field_validator(
        "source_refs",
        "summary_refs",
        "memory_refs",
        "evidence_refs",
        "tool_result_refs",
        "artifact_refs",
        "control_refs",
        "compaction_boundary_refs",
    )
    @classmethod
    def _validate_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(_validate_ref(ref) for ref in value))

    @field_validator("raw_context_digest")
    @classmethod
    def _validate_digest(cls, value: str | None) -> str | None:
        if value is not None and not _DIGEST_RE.fullmatch(value):
            raise ValueError("rawContextDigest must be a sha256 digest")
        return value


class ContextBudgetPlan(BaseModel):
    model_config = _MODEL_CONFIG

    strategy: ContextBudgetStrategy
    max_refs: int = Field(alias="maxRefs")
    included_refs: tuple[str, ...] = Field(default=(), alias="includedRefs")
    ref_groups: dict[str, list[str]] = Field(alias="refGroups")
    raw_context_included: bool = Field(default=False, alias="rawContextIncluded")
    raw_context_digest: str | None = Field(default=None, alias="rawContextDigest")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    public_projection_only: bool = Field(default=True, alias="publicProjectionOnly")

    def public_projection(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "strategy": self.strategy,
            "maxRefs": self.max_refs,
            "includedRefs": list(self.included_refs),
            "refGroups": self.ref_groups,
            "rawContextIncluded": self.raw_context_included,
            "reasonCodes": list(self.reason_codes),
            "publicProjectionOnly": True,
        }
        if self.raw_context_digest is not None:
            payload["rawContextDigest"] = self.raw_context_digest
        return payload


class ContextBudgetPlanner:
    @classmethod
    def with_defaults(cls) -> "ContextBudgetPlanner":
        return cls()

    def plan(self, request: ContextBudgetRequest) -> ContextBudgetPlan:
        max_refs = _max_refs_for_tier(request.model_tier)
        strategy = _strategy_for_tier(request.model_tier)
        memory_refs = () if request.memory_mode == "incognito" else request.memory_refs
        groups = {
            "source": list(request.source_refs),
            "summary": list(request.summary_refs),
            "memory": list(memory_refs),
            "evidence": list(request.evidence_refs),
            "tool_result": list(request.tool_result_refs),
            "artifact": list(request.artifact_refs),
            "control": list(request.control_refs),
            "compaction": list(request.compaction_boundary_refs),
        }
        ordered = _ordered_refs(groups)
        included = tuple(ordered[:max_refs])
        reason_codes: list[str] = ["refs_recorded"]
        if request.raw_input_bytes > _raw_budget_for_tier(request.model_tier):
            reason_codes.append("raw_context_too_large")
        if request.memory_mode == "incognito" and request.memory_refs:
            reason_codes.append("memory_refs_excluded_incognito")
        if request.compaction_boundary_refs:
            reason_codes.append("compaction_boundary_ref_used")

        raw_context_included = (
            request.allow_raw_context_for_local_test
            and request.raw_context_digest is not None
            and request.raw_input_bytes <= _raw_budget_for_tier(request.model_tier)
        )
        if raw_context_included:
            reason_codes.append("local_test_raw_context_digest_only")
        return ContextBudgetPlan(
            strategy=strategy,
            maxRefs=max_refs,
            includedRefs=included,
            refGroups=groups,
            rawContextIncluded=raw_context_included,
            rawContextDigest=request.raw_context_digest if raw_context_included else None,
            reasonCodes=tuple(sorted(dict.fromkeys(reason_codes))),
        )


def _validate_ref(value: str) -> str:
    text = value.strip()
    if _SECRET_REF_RE.search(text) or not _PUBLIC_REF_RE.fullmatch(text):
        raise ValueError("context refs must be sanitized public refs")
    return text


def _max_refs_for_tier(tier: ModelTier) -> int:
    if tier == "cheap":
        return 6
    if tier == "sota":
        return 24
    if tier == "long_context":
        return 40
    return 14


def _raw_budget_for_tier(tier: ModelTier) -> int:
    if tier == "cheap":
        return 8_192
    if tier in {"sota", "long_context"}:
        return 24_576
    return 16_384


def _strategy_for_tier(tier: ModelTier) -> ContextBudgetStrategy:
    if tier == "cheap":
        return "refs_only_with_chunk_summaries"
    if tier in {"sota", "long_context"}:
        return "expanded_refs_with_summaries"
    return "refs_with_summaries"


def _ordered_refs(groups: dict[str, list[str]]) -> list[str]:
    refs: list[str] = []
    for key in (
        "compaction",
        "summary",
        "evidence",
        "source",
        "memory",
        "tool_result",
        "artifact",
        "control",
    ):
        refs.extend(groups[key])
    return list(dict.fromkeys(refs))


__all__ = [
    "ContextBudgetPlan",
    "ContextBudgetPlanner",
    "ContextBudgetRequest",
    "ContextBudgetStrategy",
    "ContextRefKind",
    "MemoryMode",
]
