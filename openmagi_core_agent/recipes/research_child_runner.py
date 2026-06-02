from __future__ import annotations

from collections.abc import Mapping, Sequence
import asyncio
import hashlib
import re
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from openmagi_core_agent.runtime import (
    ChildRunnerConfig,
    ChildRunnerResult,
    ChildTaskRequest,
    LocalChildRunnerBoundary,
)


ResearchChildRole = Literal["explore", "plan", "verifier"]
ResearchChildRunnerStatus = Literal["disabled", "accepted", "blocked", "partial", "error"]
ResearchChildSynthesisStatus = Literal["completed", "blocked", "failed", "error", "disabled"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_READ_ONLY_SOURCE_TOOLS = (
    "SourceLedgerRead",
    "SourceLedgerList",
    "FileRead",
    "SearchFiles",
    "ListFiles",
    "InspectSource",
)
_PUBLIC_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:!-]{0,180}$")
_RUNTIME_REF_RE = re.compile(r"^(?:child|evidence|artifact|audit):[a-f0-9]{16}$")
_SOURCE_REF_RE = re.compile(r"^(?:source|ledger):[A-Za-z0-9_.:!-]{1,180}$")
_CLAIM_REF_RE = re.compile(r"^claim:[A-Za-z0-9_.:!-]{1,180}$")
_PRIVATE_TEXT_RE = re.compile(
    r"(?:/Users/|/home/|/workspace/|/data/bots/|/var/lib/|authorization|"
    r"cookie|raw[_ -]?(?:child|tool|prompt|transcript|output|result|log|args)|"
    r"hidden[_-]?reasoning|token|secret|session[_-]?key|password|credential|"
    r"private[_-]?key|bearer\s+[A-Za-z0-9._~+/=-]{6,}|sk[-_][A-Za-z0-9._-]{6,}|"
    r"gh[opusr]_[A-Za-z0-9_]{6,}|github_pat_[A-Za-z0-9_]+|"
    r"xox[a-z]-[A-Za-z0-9._-]+|AKIA[0-9A-Z]{8,}|AIza[A-Za-z0-9_-]+)",
    re.IGNORECASE,
)
_URL_OR_CITATION_RE = re.compile(
    r"(?:[A-Za-z][A-Za-z0-9+.-]{0,31}://|www\.|[A-Za-z0-9.-]+\.[A-Za-z]{2,}|citation)",
    re.IGNORECASE,
)


class ResearchChildRunnerConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    local_fake_child_runner_enabled: bool = Field(
        default=False,
        alias="localFakeChildRunnerEnabled",
    )
    additional_allowed_tools: tuple[str, ...] = Field(default=(), alias="additionalAllowedTools")
    max_child_tasks: int = Field(default=4, alias="maxChildTasks", ge=1, le=8)
    max_spawn_depth: Literal[1] = Field(default=1, alias="maxSpawnDepth")
    production_child_execution_enabled: Literal[False] = Field(
        default=False,
        alias="productionChildExecutionEnabled",
    )
    live_tool_execution_enabled: Literal[False] = Field(
        default=False,
        alias="liveToolExecutionEnabled",
    )
    workspace_mutation_enabled: Literal[False] = Field(
        default=False,
        alias="workspaceMutationEnabled",
    )
    user_visible_activation: Literal[False] = Field(default=False, alias="userVisibleActivation")

    @field_validator("additional_allowed_tools", mode="before")
    @classmethod
    def _coerce_tools(cls, value: object) -> object:
        if value is None:
            return ()
        if isinstance(value, str):
            return (value,)
        return value

    @field_validator("additional_allowed_tools")
    @classmethod
    def _validate_tools(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        safe: list[str] = []
        for item in value:
            if not isinstance(item, str):
                continue
            clean = item.strip()
            if clean and _PUBLIC_REF_RE.fullmatch(clean):
                safe.append(clean)
        return tuple(dict.fromkeys(safe))


class ResearchChildRunnerAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    recipe_enabled: bool = Field(default=False, alias="recipeEnabled")
    local_fake_child_runner_enabled: bool = Field(
        default=False,
        alias="localFakeChildRunnerEnabled",
    )
    live_child_runner_enabled: Literal[False] = Field(
        default=False,
        alias="liveChildRunnerEnabled",
    )
    live_tool_execution_enabled: Literal[False] = Field(
        default=False,
        alias="liveToolExecutionEnabled",
    )
    workspace_mutation_enabled: Literal[False] = Field(
        default=False,
        alias="workspaceMutationEnabled",
    )
    workspace_mutated: Literal[False] = Field(default=False, alias="workspaceMutated")
    background_mode_enabled: Literal[False] = Field(default=False, alias="backgroundModeEnabled")
    memory_provider_called: Literal[False] = Field(default=False, alias="memoryProviderCalled")
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")
    production_authority: Literal[False] = Field(default=False, alias="productionAuthority")
    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    user_visible_activation: Literal[False] = Field(default=False, alias="userVisibleActivation")

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        values.update(_false_authority_overrides())
        return cls.model_validate(values)

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            data.update(dict(update))
        data.update(_false_authority_overrides())
        return type(self).model_validate(data)

    @field_serializer(
        "live_child_runner_enabled",
        "live_tool_execution_enabled",
        "workspace_mutation_enabled",
        "workspace_mutated",
        "background_mode_enabled",
        "memory_provider_called",
        "route_attached",
        "production_authority",
        "traffic_attached",
        "user_visible_activation",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class ResearchChildToolScope(BaseModel):
    model_config = _MODEL_CONFIG

    allowed_tools: tuple[str, ...] = Field(default=_READ_ONLY_SOURCE_TOOLS, alias="allowedTools")
    source_inspection_only: bool = Field(default=True, alias="sourceInspectionOnly")
    mutation_intent_allowed: Literal[False] = Field(default=False, alias="mutationIntentAllowed")
    live_provider_calls_allowed: Literal[False] = Field(
        default=False,
        alias="liveProviderCallsAllowed",
    )
    parent_owns_lifecycle: bool = Field(default=True, alias="parentOwnsLifecycle")

    def public_projection(self) -> dict[str, object]:
        return {
            "allowedTools": list(self.allowed_tools),
            "sourceInspectionOnly": self.source_inspection_only,
            "mutationIntentAllowed": False,
            "liveProviderCallsAllowed": False,
            "parentOwnsLifecycle": self.parent_owns_lifecycle,
        }


class ResearchChildTaskSpec(BaseModel):
    model_config = _MODEL_CONFIG

    task_id: str = Field(alias="taskId")
    child_role: ResearchChildRole = Field(alias="childRole")
    objective: str
    source_refs: tuple[str, ...] = Field(alias="sourceRefs")
    claim_refs: tuple[str, ...] = Field(alias="claimRefs")
    unsupported_claim_count: int = Field(default=0, alias="unsupportedClaimCount", ge=0)
    spawn_depth: int = Field(default=1, alias="spawnDepth", ge=1)
    budget_tokens: int = Field(default=768, alias="budgetTokens", ge=0)
    budget_ms: int = Field(default=5000, alias="budgetMs", ge=0)

    @field_validator("task_id", "objective")
    @classmethod
    def _validate_text(cls, value: str) -> str:
        return _safe_text_field(value, "research child task fields")

    @field_validator("source_refs", mode="before")
    @classmethod
    def _coerce_source_refs(cls, value: object) -> object:
        return _coerce_tuple(value)

    @field_validator("claim_refs", mode="before")
    @classmethod
    def _coerce_claim_refs(cls, value: object) -> object:
        return _coerce_tuple(value)

    @field_validator("source_refs")
    @classmethod
    def _validate_source_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _safe_refs(value, _SOURCE_REF_RE)

    @field_validator("claim_refs")
    @classmethod
    def _validate_claim_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _safe_refs(value, _CLAIM_REF_RE)


class ResearchSynthesisRequest(BaseModel):
    model_config = _MODEL_CONFIG

    parent_execution_id: str = Field(alias="parentExecutionId")
    turn_id: str = Field(alias="turnId")
    synthesis_id: str = Field(alias="synthesisId")
    objective: str
    parent_source_refs: tuple[str, ...] = Field(alias="parentSourceRefs")
    parent_claim_refs: tuple[str, ...] = Field(default=(), alias="parentClaimRefs")
    tasks: tuple[ResearchChildTaskSpec, ...]

    @field_validator("parent_execution_id", "turn_id", "synthesis_id", "objective")
    @classmethod
    def _validate_text(cls, value: str) -> str:
        return _safe_text_field(value, "research synthesis fields")

    @field_validator("parent_source_refs", mode="before")
    @classmethod
    def _coerce_parent_source_refs(cls, value: object) -> object:
        return _coerce_tuple(value)

    @field_validator("parent_claim_refs", mode="before")
    @classmethod
    def _coerce_parent_claim_refs(cls, value: object) -> object:
        return _coerce_tuple(value)

    @field_validator("parent_source_refs")
    @classmethod
    def _validate_parent_source_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _safe_refs(value, _SOURCE_REF_RE)

    @field_validator("parent_claim_refs")
    @classmethod
    def _validate_parent_claim_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            return ()
        return _safe_refs(value, _CLAIM_REF_RE)

    @field_validator("tasks")
    @classmethod
    def _validate_tasks(cls, value: tuple[ResearchChildTaskSpec, ...]) -> tuple[ResearchChildTaskSpec, ...]:
        if not value:
            raise ValueError("research synthesis requires at least one child task")
        return value


class ResearchChildSynthesisInput(BaseModel):
    model_config = _MODEL_CONFIG

    task_id: str = Field(alias="taskId")
    child_role: ResearchChildRole = Field(alias="childRole")
    source_refs: tuple[str, ...] = Field(default=(), alias="sourceRefs")
    evidence_refs: tuple[str, ...] = Field(default=(), alias="evidenceRefs")
    artifact_refs: tuple[str, ...] = Field(default=(), alias="artifactRefs")
    audit_event_refs: tuple[str, ...] = Field(default=(), alias="auditEventRefs")
    claim_refs: tuple[str, ...] = Field(default=(), alias="claimRefs")
    public_child_summary: str = Field(default="", alias="publicChildSummary")
    unsupported_claim_count: int = Field(default=0, alias="unsupportedClaimCount", ge=0)
    child_status: ResearchChildSynthesisStatus = Field(alias="childStatus")
    child_ref: str | None = Field(default=None, alias="childRef")
    error_code: str | None = Field(default=None, alias="errorCode")

    def public_projection(self) -> dict[str, object]:
        projection: dict[str, object] = {
            "taskId": self.task_id,
            "childRole": self.child_role,
            "sourceRefs": list(self.source_refs),
            "evidenceRefs": list(self.evidence_refs),
            "artifactRefs": list(self.artifact_refs),
            "auditEventRefs": list(self.audit_event_refs),
            "claimRefs": list(self.claim_refs),
            "publicChildSummary": _sanitize_summary(self.public_child_summary),
            "unsupportedClaimCount": self.unsupported_claim_count,
            "childStatus": self.child_status,
        }
        if self.child_ref is not None:
            projection["childRef"] = self.child_ref
        if self.error_code is not None:
            projection["errorCode"] = _sanitize_text(self.error_code, max_chars=120)
        return projection


class ResearchParentSynthesisInput(BaseModel):
    model_config = _MODEL_CONFIG

    synthesis_id: str = Field(alias="synthesisId")
    parent_source_refs: tuple[str, ...] = Field(default=(), alias="parentSourceRefs")
    parent_claim_refs: tuple[str, ...] = Field(default=(), alias="parentClaimRefs")
    parent_evidence_refs: tuple[str, ...] = Field(default=(), alias="parentEvidenceRefs")
    child_inputs: tuple[ResearchChildSynthesisInput, ...] = Field(alias="childInputs")

    def public_projection(self) -> dict[str, object]:
        return {
            "synthesisId": self.synthesis_id,
            "parentSourceRefs": list(self.parent_source_refs),
            "parentClaimRefs": list(self.parent_claim_refs),
            "parentEvidenceRefs": list(self.parent_evidence_refs),
            "childInputs": [item.public_projection() for item in self.child_inputs],
        }


class ResearchChildRunnerResult(BaseModel):
    model_config = _MODEL_CONFIG

    status: ResearchChildRunnerStatus
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")
    tool_scope: ResearchChildToolScope = Field(alias="toolScope")
    parent_synthesis_input: ResearchParentSynthesisInput = Field(alias="parentSynthesisInput")
    authority_flags: ResearchChildRunnerAuthorityFlags = Field(alias="authorityFlags")

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        values["authorityFlags"] = _coerce_authority_flags(values.get("authorityFlags"))
        return cls.model_validate(values)

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            data.update(dict(update))
        data["authorityFlags"] = self.authority_flags
        return type(self).model_validate(data)

    def public_projection(self) -> dict[str, object]:
        return _sanitize_projection(
            {
                "status": self.status,
                "reasonCodes": list(self.reason_codes),
                "toolScope": self.tool_scope.public_projection(),
                "parentSynthesisInput": self.parent_synthesis_input.public_projection(),
                "authorityFlags": self.authority_flags.model_dump(by_alias=True),
            }
        )


class ResearchChildRunnerRecipe:
    """Research-owned policy around the generic local child boundary."""

    def __init__(
        self,
        config: ResearchChildRunnerConfig | None = None,
        *,
        child_runner: object | None = None,
    ) -> None:
        self.config = config or ResearchChildRunnerConfig()
        self.child_runner = child_runner

    async def run(
        self,
        request: ResearchSynthesisRequest | Mapping[str, object],
    ) -> ResearchChildRunnerResult:
        parsed = (
            request
            if isinstance(request, ResearchSynthesisRequest)
            else ResearchSynthesisRequest.model_validate(request)
        )
        scope = ResearchChildToolScope()
        flags = ResearchChildRunnerAuthorityFlags(
            recipeEnabled=self.config.enabled,
            localFakeChildRunnerEnabled=(
                self.config.local_fake_child_runner_enabled and self.child_runner is not None
            ),
            **_false_authority_overrides(),
        )
        empty_input = _parent_input(parsed, ())
        if not self.config.enabled:
            return _result(
                "disabled",
                ("research_child_runner_recipe_disabled",),
                scope,
                empty_input,
                flags=ResearchChildRunnerAuthorityFlags(),
            )
        if not self.config.local_fake_child_runner_enabled or self.child_runner is None:
            return _result(
                "disabled",
                ("local_fake_child_runner_disabled",),
                scope,
                empty_input,
                flags=flags,
            )
        if len(parsed.tasks) > self.config.max_child_tasks:
            return _result(
                "blocked",
                ("max_child_tasks_exceeded",),
                scope,
                empty_input,
                flags=flags,
            )
        if any(task.spawn_depth > self.config.max_spawn_depth for task in parsed.tasks):
            return _result(
                "blocked",
                ("max_spawn_depth_exceeded",),
                scope,
                empty_input,
                flags=flags,
            )

        child_results = await asyncio.gather(
            *(self._run_child(parsed, task, scope) for task in parsed.tasks)
        )
        child_inputs = tuple(
            _synthesis_input_from_child(task, child)
            for task, child in zip(parsed.tasks, child_results, strict=True)
        )
        parent_input = _parent_input(parsed, child_inputs)
        status, reasons = _status_and_reasons(child_inputs, child_results)
        return _result(status, reasons, scope, parent_input, flags=flags)

    async def _run_child(
        self,
        request: ResearchSynthesisRequest,
        task: ResearchChildTaskSpec,
        scope: ResearchChildToolScope,
    ) -> ChildRunnerResult:
        child_request = ChildTaskRequest(
            parentExecutionId=request.parent_execution_id,
            turnId=request.turn_id,
            taskId=task.task_id,
            objective=task.objective,
            role="research",
            delivery="return",
            budgetTokens=task.budget_tokens,
            budgetMs=task.budget_ms,
            metadata={
                "researchChildRole": task.child_role,
                "allowedTools": scope.allowed_tools,
                "toolScopeRef": f"policy:{_digest(':'.join(scope.allowed_tools))}",
                "parentOwnsLifecycle": True,
                "sourceInspectionOnly": True,
                "spawnDepth": task.spawn_depth,
                "maxSpawnDepth": self.config.max_spawn_depth,
                "sourceRefs": task.source_refs,
                "claimRefs": task.claim_refs,
            },
        )
        return await LocalChildRunnerBoundary(
            ChildRunnerConfig(
                enabled=self.config.enabled,
                localFakeChildRunnerEnabled=self.config.local_fake_child_runner_enabled,
            ),
            child_runner=self.child_runner,
        ).run(child_request)


def _result(
    status: ResearchChildRunnerStatus,
    reason_codes: tuple[str, ...],
    scope: ResearchChildToolScope,
    parent_input: ResearchParentSynthesisInput,
    *,
    flags: ResearchChildRunnerAuthorityFlags,
) -> ResearchChildRunnerResult:
    return ResearchChildRunnerResult(
        status=status,
        reasonCodes=reason_codes,
        toolScope=scope,
        parentSynthesisInput=parent_input,
        authorityFlags=flags,
    )


def _synthesis_input_from_child(
    task: ResearchChildTaskSpec,
    child: ChildRunnerResult,
) -> ResearchChildSynthesisInput:
    projection = child.public_projection()
    envelope = projection.get("childEnvelope")
    error_code = child.error_code
    if child.status != "ok" or not isinstance(envelope, Mapping):
        return ResearchChildSynthesisInput(
            taskId=task.task_id,
            childRole=task.child_role,
            sourceRefs=task.source_refs,
            evidenceRefs=(),
            artifactRefs=(),
            auditEventRefs=(),
            claimRefs=task.claim_refs,
            publicChildSummary="",
            unsupportedClaimCount=task.unsupported_claim_count,
            childStatus=_child_status(child.status),
            childRef=None,
            errorCode=error_code or f"child_runner_{child.status}",
        )

    evidence_refs = _runtime_refs(envelope.get("evidenceRefs"), namespace="evidence")
    artifact_refs = _runtime_refs(envelope.get("artifactRefs"), namespace="artifact")
    audit_refs = _runtime_refs(envelope.get("auditEventRefs"), namespace="audit")
    child_ref = _runtime_ref(envelope.get("childRef"), namespace="child")
    envelope_status = envelope.get("status")
    child_status: ResearchChildSynthesisStatus = (
        envelope_status if envelope_status in {"completed", "blocked", "failed"} else "blocked"
    )
    if child_status == "completed" and (not evidence_refs or not task.source_refs or not task.claim_refs):
        child_status = "blocked"
        error_code = "child_evidence_refs_required"
    return ResearchChildSynthesisInput(
        taskId=task.task_id,
        childRole=task.child_role,
        sourceRefs=task.source_refs,
        evidenceRefs=evidence_refs,
        artifactRefs=artifact_refs,
        auditEventRefs=audit_refs,
        claimRefs=task.claim_refs,
        publicChildSummary=_sanitize_summary(str(envelope.get("summary") or "")),
        unsupportedClaimCount=task.unsupported_claim_count,
        childStatus=child_status,
        childRef=child_ref,
        errorCode=error_code,
    )


def _parent_input(
    request: ResearchSynthesisRequest,
    child_inputs: tuple[ResearchChildSynthesisInput, ...],
) -> ResearchParentSynthesisInput:
    parent_evidence_refs: list[str] = []
    for child in child_inputs:
        for ref in (
            child.child_ref,
            *child.evidence_refs,
            *child.artifact_refs,
            *child.audit_event_refs,
        ):
            if isinstance(ref, str) and _RUNTIME_REF_RE.fullmatch(ref):
                parent_evidence_refs.append(ref)
    return ResearchParentSynthesisInput(
        synthesisId=request.synthesis_id,
        parentSourceRefs=request.parent_source_refs,
        parentClaimRefs=request.parent_claim_refs,
        parentEvidenceRefs=tuple(dict.fromkeys(parent_evidence_refs)),
        childInputs=child_inputs,
    )


def _status_and_reasons(
    child_inputs: tuple[ResearchChildSynthesisInput, ...],
    child_results: tuple[ChildRunnerResult, ...],
) -> tuple[ResearchChildRunnerStatus, tuple[str, ...]]:
    error_codes = tuple(
        child.error_code or "child_runner_error"
        for child in child_results
        if child.status == "error"
    )
    if error_codes:
        return "partial", tuple(dict.fromkeys(error_codes))
    blocked_reasons: list[str] = []
    for child in child_inputs:
        if child.child_status == "disabled":
            blocked_reasons.append(child.error_code or "child_runner_disabled")
        elif child.child_status == "blocked":
            blocked_reasons.append(child.error_code or "child_runner_blocked")
        elif child.child_status == "failed":
            blocked_reasons.append(child.error_code or "child_runner_failed")
    if blocked_reasons:
        return "blocked", tuple(dict.fromkeys(blocked_reasons))
    return "accepted", ("research_child_runner_children_completed",)


def _child_status(status: str) -> ResearchChildSynthesisStatus:
    if status == "error":
        return "error"
    if status == "disabled":
        return "disabled"
    if status == "blocked":
        return "blocked"
    return "blocked"


def _runtime_refs(
    value: object,
    *,
    namespace: Literal["evidence", "artifact", "audit"],
) -> tuple[str, ...]:
    refs: list[str] = []
    if isinstance(value, str):
        items: Sequence[object] = (value,)
    elif isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        items = value
    else:
        items = ()
    for item in items:
        ref = _runtime_ref(item, namespace=namespace)
        if ref is not None:
            refs.append(ref)
    return tuple(dict.fromkeys(refs))


def _runtime_ref(
    value: object,
    *,
    namespace: Literal["child", "evidence", "artifact", "audit"],
) -> str | None:
    if not isinstance(value, str):
        return None
    clean = _sanitize_text(value, max_chars=180)
    if re.fullmatch(rf"{namespace}:[a-f0-9]{{16}}", clean):
        return clean
    return None


def _safe_refs(value: tuple[str, ...], pattern: re.Pattern[str]) -> tuple[str, ...]:
    refs: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        clean = _sanitize_text(item.strip(), max_chars=180)
        if pattern.fullmatch(clean):
            refs.append(clean)
    if not refs:
        raise ValueError("research synthesis refs must contain public source or claim refs")
    return tuple(dict.fromkeys(refs))


def _coerce_tuple(value: object) -> object:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return value


def _safe_text_field(value: str, field_group: str) -> str:
    clean = _sanitize_text(value.strip(), max_chars=240)
    if not clean:
        raise ValueError(f"{field_group} must be non-empty")
    return clean


def _sanitize_summary(value: str) -> str:
    lines = []
    for line in value.splitlines():
        stripped = line.strip()
        if _PRIVATE_TEXT_RE.search(stripped) is not None:
            continue
        clean = _sanitize_text(stripped, max_chars=360)
        if clean and _URL_OR_CITATION_RE.search(clean) is None:
            lines.append(clean)
    return "\n".join(lines)[:360]


def _sanitize_text(value: str, *, max_chars: int) -> str:
    clean = _PRIVATE_TEXT_RE.sub("[redacted]", value)
    return clean[:max_chars]


def _sanitize_projection(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _sanitize_projection(item)
            for key, item in value.items()
            if _PRIVATE_TEXT_RE.search(str(key)) is None
        }
    if isinstance(value, tuple | list):
        return [_sanitize_projection(item) for item in value]
    if isinstance(value, str):
        return _sanitize_text(value, max_chars=512)
    return value


def _coerce_authority_flags(value: object) -> ResearchChildRunnerAuthorityFlags:
    if isinstance(value, ResearchChildRunnerAuthorityFlags):
        return value
    if isinstance(value, Mapping):
        return ResearchChildRunnerAuthorityFlags.model_construct(**dict(value))
    return ResearchChildRunnerAuthorityFlags()


def _false_authority_overrides() -> dict[str, Literal[False]]:
    return {
        "liveChildRunnerEnabled": False,
        "liveToolExecutionEnabled": False,
        "workspaceMutationEnabled": False,
        "workspaceMutated": False,
        "backgroundModeEnabled": False,
        "memoryProviderCalled": False,
        "routeAttached": False,
        "productionAuthority": False,
        "trafficAttached": False,
        "userVisibleActivation": False,
    }


def _digest(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


__all__ = [
    "ResearchChildRole",
    "ResearchChildRunnerAuthorityFlags",
    "ResearchChildRunnerConfig",
    "ResearchChildRunnerRecipe",
    "ResearchChildRunnerResult",
    "ResearchChildRunnerStatus",
    "ResearchChildSynthesisInput",
    "ResearchChildTaskSpec",
    "ResearchChildToolScope",
    "ResearchParentSynthesisInput",
    "ResearchSynthesisRequest",
]
