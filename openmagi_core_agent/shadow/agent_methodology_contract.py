from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator


MethodologyCategory = Literal[
    "onboarding",
    "design_refinement",
    "planning",
    "tdd",
    "debugging",
    "verification",
    "code_review",
    "subagent_development",
    "git_worktree",
    "branch_finish",
    "plan_auto_trigger",
    "approval_gate",
]
MethodologyMode = Literal[
    "instruction",
    "recipe-hook",
    "checkpoint",
    "validator",
    "evidence",
    "approval-gate",
]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_UNSAFE_PUBLIC_RE = re.compile(
    r"raw child transcript|hidden reasoning|tool log|Bearer\s+\S+|"
    r"sk-[A-Za-z0-9._-]+|gh[opusr]_[A-Za-z0-9_]+|"
    r"(?:^|[\\/])(?:data[\\/]bots|workspace)(?:[\\/]|$)",
    re.IGNORECASE,
)
_FORBIDDEN_MODELING_RE = re.compile(
    r"RunnerAdapter|runner-adapter|route-branch|session-controller|custom orchestration",
    re.IGNORECASE,
)
_LONG_RUNNING_METHODOLOGY_ORCHESTRATION_RE = re.compile(
    r"LongRunningFunctionTool.*(?:mission|planning|orchestrat)|"
    r"(?:mission|planning|orchestrat).*LongRunningFunctionTool",
    re.IGNORECASE,
)
_REQUIRED_CATEGORIES: tuple[MethodologyCategory, ...] = (
    "onboarding",
    "design_refinement",
    "planning",
    "tdd",
    "debugging",
    "verification",
    "code_review",
    "subagent_development",
    "git_worktree",
    "branch_finish",
    "plan_auto_trigger",
    "approval_gate",
)
_REQUIRED_CAPABILITY_REFS = frozenset(
    (
        "using-superpowers",
        "onboarding",
        "brainstorming",
        "design-refinement",
        "writing-plans",
        "executing-plans",
        "test-driven-development",
        "red-green-refactor",
        "systematic-debugging",
        "verification-before-completion",
        "requesting-code-review",
        "receiving-code-review",
        "subagent-driven-development",
        "using-git-worktrees",
        "finishing-a-development-branch",
        "plan-auto-trigger",
        "approval-gated-live-behavior",
    )
)


def _as_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)  # type: ignore[arg-type]


class AgentMethodologyAttachmentFlags(BaseModel):
    model_config = _MODEL_CONFIG

    adk_runner_invoked: Literal[False] = Field(default=False, alias="adkRunnerInvoked")
    child_execution_attached: Literal[False] = Field(
        default=False,
        alias="childExecutionAttached",
    )
    live_slash_runtime_attached: Literal[False] = Field(
        default=False,
        alias="liveSlashRuntimeAttached",
    )
    tool_host_dispatched: Literal[False] = Field(default=False, alias="toolHostDispatched")
    session_controller_branch_attached: Literal[False] = Field(
        default=False,
        alias="sessionControllerBranchAttached",
    )
    runner_route_attached: Literal[False] = Field(default=False, alias="runnerRouteAttached")
    workspace_mutated: Literal[False] = Field(default=False, alias="workspaceMutated")
    production_authority: Literal[False] = Field(default=False, alias="productionAuthority")
    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    evidence_block_enabled: Literal[False] = Field(default=False, alias="evidenceBlockEnabled")

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        return cls()

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        return type(self)()

    @field_serializer(
        "adk_runner_invoked",
        "child_execution_attached",
        "live_slash_runtime_attached",
        "tool_host_dispatched",
        "session_controller_branch_attached",
        "runner_route_attached",
        "workspace_mutated",
        "production_authority",
        "traffic_attached",
        "evidence_block_enabled",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class ParentContextIsolation(BaseModel):
    model_config = _MODEL_CONFIG

    raw_child_transcript_injected: Literal[False] = Field(
        default=False,
        alias="rawChildTranscriptInjected",
    )
    tool_logs_injected: Literal[False] = Field(default=False, alias="toolLogsInjected")
    hidden_reasoning_injected: Literal[False] = Field(
        default=False,
        alias="hiddenReasoningInjected",
    )
    sanitized_structured_envelope_only: Literal[True] = Field(
        default=True,
        alias="sanitizedStructuredEnvelopeOnly",
    )


class SanitizedChildEnvelope(BaseModel):
    model_config = _MODEL_CONFIG

    envelope_ref: str = Field(alias="envelopeRef")
    status: str
    preview: str
    evidence_refs: tuple[str, ...] = Field(default=(), alias="evidenceRefs")
    private_refs: tuple[str, ...] = Field(default=(), alias="privateRefs")

    @field_validator("evidence_refs", "private_refs", mode="before")
    @classmethod
    def _tuple_refs(cls, value: object) -> tuple[str, ...]:
        return _as_tuple(value)

    @field_validator("preview")
    @classmethod
    def _reject_unsafe_preview(cls, value: str) -> str:
        if _UNSAFE_PUBLIC_RE.search(value):
            raise ValueError("sanitized child envelope preview contains unsafe parent-context content")
        return value

    @field_validator("envelope_ref", "status")
    @classmethod
    def _reject_unsafe_public_string(cls, value: str) -> str:
        if _UNSAFE_PUBLIC_RE.search(value):
            raise ValueError("sanitized child envelope contains unsafe public string")
        return value


class AgentMethodologyCase(BaseModel):
    model_config = _MODEL_CONFIG

    case_id: str = Field(alias="caseId")
    category: MethodologyCategory
    capability_refs: tuple[str, ...] = Field(default=(), alias="capabilityRefs")
    modeled_as: tuple[MethodologyMode, ...] = Field(default=(), alias="modeledAs")
    instruction_refs: tuple[str, ...] = Field(default=(), alias="instructionRefs")
    callback_refs: tuple[str, ...] = Field(default=(), alias="callbackRefs")
    checkpoint_refs: tuple[str, ...] = Field(default=(), alias="checkpointRefs")
    validator_refs: tuple[str, ...] = Field(default=(), alias="validatorRefs")
    evidence_refs: tuple[str, ...] = Field(default=(), alias="evidenceRefs")
    audit_refs: tuple[str, ...] = Field(default=(), alias="auditRefs")
    live_behavior_approval_gated: Literal[True] = Field(
        default=True,
        alias="liveBehaviorApprovalGated",
    )
    live_slash_runtime_attached: Literal[False] = Field(
        default=False,
        alias="liveSlashRuntimeAttached",
    )
    runner_route_refs: tuple[str, ...] = Field(default=(), alias="runnerRouteRefs")
    attachment_flags: AgentMethodologyAttachmentFlags = Field(
        default_factory=AgentMethodologyAttachmentFlags,
        alias="attachmentFlags",
    )
    parent_context_isolation: ParentContextIsolation | None = Field(
        default=None,
        alias="parentContextIsolation",
    )
    sanitized_child_envelope: SanitizedChildEnvelope | None = Field(
        default=None,
        alias="sanitizedChildEnvelope",
    )

    @field_validator(
        "capability_refs",
        "modeled_as",
        "instruction_refs",
        "callback_refs",
        "checkpoint_refs",
        "validator_refs",
        "evidence_refs",
        "audit_refs",
        "runner_route_refs",
        mode="before",
    )
    @classmethod
    def _tuple_refs(cls, value: object) -> tuple[str, ...]:
        return _as_tuple(value)

    @field_validator(
        "case_id",
        "capability_refs",
        "instruction_refs",
        "callback_refs",
        "checkpoint_refs",
        "validator_refs",
        "evidence_refs",
        "audit_refs",
        mode="after",
    )
    @classmethod
    def _reject_unsafe_public_refs(cls, value: object) -> object:
        values = (value,) if isinstance(value, str) else value
        for item in values:  # type: ignore[union-attr]
            if _UNSAFE_PUBLIC_RE.search(str(item)) or _FORBIDDEN_MODELING_RE.search(str(item)):
                raise ValueError("agent methodology public refs contain unsafe runtime content")
        return value

    @model_validator(mode="after")
    def _validate_metadata_only_methodology_case(self) -> Self:
        if self.runner_route_refs:
            raise ValueError("methodology fixtures cannot declare runner route refs")
        if self.category == "subagent_development":
            if self.parent_context_isolation is None or self.sanitized_child_envelope is None:
                raise ValueError("subagent methodology cases require parent context isolation")
        if self.sanitized_child_envelope is not None:
            if not self.sanitized_child_envelope.evidence_refs:
                raise ValueError("sanitized child envelopes must carry structured evidence refs")
        return self

    def public_snapshot(self) -> dict[str, object]:
        snapshot: dict[str, object] = {
            "category": self.category,
            "capabilityRefs": self.capability_refs,
            "modeledAs": self.modeled_as,
            "instructionRefs": self.instruction_refs,
            "callbackRefs": self.callback_refs,
            "checkpointRefs": self.checkpoint_refs,
            "validatorRefs": self.validator_refs,
            "evidenceRefs": self.evidence_refs,
            "auditRefs": self.audit_refs,
            "liveBehaviorApprovalGated": self.live_behavior_approval_gated,
            "liveSlashRuntimeAttached": self.live_slash_runtime_attached,
            "runnerRouteRefs": self.runner_route_refs,
        }
        if self.parent_context_isolation is not None:
            snapshot["parentContextIsolation"] = self.parent_context_isolation.model_dump(
                by_alias=True,
                mode="python",
            )
        if self.sanitized_child_envelope is not None:
            snapshot["childEnvelopeRefs"] = self.sanitized_child_envelope.evidence_refs
            snapshot["sanitizedChildEnvelope"] = {
                "envelopeRef": self.sanitized_child_envelope.envelope_ref,
                "status": self.sanitized_child_envelope.status,
                "preview": self.sanitized_child_envelope.preview,
                "evidenceRefs": self.sanitized_child_envelope.evidence_refs,
            }
        return snapshot


class AgentMethodologyFixture(BaseModel):
    model_config = _MODEL_CONFIG

    fixture_id: str = Field(alias="fixtureId")
    local_diagnostic: Literal[True] = Field(default=True, alias="localDiagnostic")
    recipe_pack_ids: tuple[str, ...] = Field(alias="recipePackIds")
    future_live_surfaces: tuple[str, ...] = Field(default=(), alias="futureLiveSurfaces")
    attachment_flags: AgentMethodologyAttachmentFlags = Field(
        default_factory=AgentMethodologyAttachmentFlags,
        alias="attachmentFlags",
    )
    cases: tuple[AgentMethodologyCase, ...]

    @field_validator("recipe_pack_ids", "future_live_surfaces", mode="before")
    @classmethod
    def _tuple_refs(cls, value: object) -> tuple[str, ...]:
        return _as_tuple(value)

    @field_validator("future_live_surfaces")
    @classmethod
    def _reject_disallowed_live_surfaces(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            if _FORBIDDEN_MODELING_RE.search(item):
                raise ValueError("future live surfaces must not describe custom runtime branches")
            if _LONG_RUNNING_METHODOLOGY_ORCHESTRATION_RE.search(item):
                raise ValueError("LongRunningFunctionTool is not methodology orchestration")
        return value

    @model_validator(mode="after")
    def _validate_complete_case_matrix(self) -> Self:
        if not self.cases:
            raise ValueError("agent methodology fixture must contain cases")
        if len({case.case_id for case in self.cases}) != len(self.cases):
            raise ValueError("agent methodology fixture case ids must be unique")
        if "openmagi.agent-methodology" not in self.recipe_pack_ids:
            raise ValueError("fixture must include openmagi.agent-methodology")
        categories = {case.category for case in self.cases}
        missing_categories = set(_REQUIRED_CATEGORIES) - categories
        if missing_categories:
            raise ValueError("agent methodology fixture missing required categories")
        capability_refs = {
            capability_ref for case in self.cases for capability_ref in case.capability_refs
        }
        if not _REQUIRED_CAPABILITY_REFS.issubset(capability_refs):
            raise ValueError("agent methodology fixture missing required capability refs")
        return self

    @property
    def no_live_execution(self) -> bool:
        fixture_flags_clear = all(
            value is False
            for value in self.attachment_flags.model_dump(mode="python").values()
        )
        case_flags_clear = all(
            all(value is False for value in case.attachment_flags.model_dump(mode="python").values())
            for case in self.cases
        )
        return (
            self.local_diagnostic is True
            and fixture_flags_clear
            and case_flags_clear
            and all(case.live_slash_runtime_attached is False for case in self.cases)
            and all(not case.runner_route_refs for case in self.cases)
        )


class PublicAgentMethodologyFixtureProjection(BaseModel):
    model_config = _MODEL_CONFIG

    fixture_id: str = Field(alias="fixtureId")
    local_diagnostic: Literal[True] = Field(alias="localDiagnostic")
    recipe_pack_ids: tuple[str, ...] = Field(alias="recipePackIds")
    future_live_surfaces: tuple[str, ...] = Field(alias="futureLiveSurfaces")
    case_order: tuple[str, ...] = Field(alias="caseOrder")
    by_category: Mapping[str, int] = Field(alias="byCategory")
    case_snapshots: Mapping[str, Mapping[str, object]] = Field(alias="caseSnapshots")
    no_live_execution: Literal[True] = Field(alias="noLiveExecution")
    attachment_flags: AgentMethodologyAttachmentFlags = Field(alias="attachmentFlags")


def project_agent_methodology_fixture(
    fixture: AgentMethodologyFixture,
) -> PublicAgentMethodologyFixtureProjection:
    fixture = AgentMethodologyFixture.model_validate(
        fixture.model_dump(by_alias=True, mode="python")
    )
    by_category = Counter(case.category for case in fixture.cases)
    return PublicAgentMethodologyFixtureProjection(
        fixtureId=fixture.fixture_id,
        localDiagnostic=fixture.local_diagnostic,
        recipePackIds=fixture.recipe_pack_ids,
        futureLiveSurfaces=fixture.future_live_surfaces,
        caseOrder=tuple(case.case_id for case in fixture.cases),
        byCategory=dict(by_category),
        caseSnapshots={case.case_id: case.public_snapshot() for case in fixture.cases},
        noLiveExecution=fixture.no_live_execution,
        attachmentFlags=AgentMethodologyAttachmentFlags(),
    )


def load_agent_methodology_fixture(
    filename: str,
    *,
    fixture_root: Path,
) -> AgentMethodologyFixture:
    path = fixture_root / filename
    return AgentMethodologyFixture.model_validate(json.loads(path.read_text()))
