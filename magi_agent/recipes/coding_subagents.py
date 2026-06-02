from __future__ import annotations

from collections.abc import Mapping
import hashlib
import re
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_serializer, field_validator

from magi_agent.recipes.coding_mutation import (
    CodingMutationConfig,
    CodingMutationDecision,
    CodingMutationRecipe,
    CodingMutationRequest,
)
from magi_agent.runtime import (
    ChildRunnerConfig,
    ChildRunnerResult,
    ChildTaskRequest,
    LocalChildRunnerBoundary,
)
from magi_agent.tools.read_ledger import ReadLedger


CodingSubagentMode = Literal["inspect", "code_review", "implement_local", "research"]
CodingSubagentStatus = Literal["disabled", "accepted", "blocked", "approval_required", "error"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_INSPECT_TOOLS = ("ReadFile", "SearchFiles", "ListFiles", "InspectSymbols", "GitDiff")
_CODE_REVIEW_TOOLS = (*_INSPECT_TOOLS, "ReportFinding", "AttachEvidence")
_IMPLEMENT_TOOLS = (*_INSPECT_TOOLS, "MutationIntent")
_PUBLIC_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:!-]{0,180}$")
_PRIVATE_TEXT_RE = re.compile(
    r"(?:/Users/|/home/|/workspace/|/data/bots/|/var/lib/|authorization|"
    r"cookie|raw[_ -]?(?:child|tool|prompt|transcript|output|result|log|args)|"
    r"hidden[_-]?reasoning|token|secret|session[_-]?key|password|credential|"
    r"private[_-]?key|bearer\s+[A-Za-z0-9._~+/=-]{6,}|sk[-_][A-Za-z0-9._-]{6,}|"
    r"gh[opusr]_[A-Za-z0-9_]{6,}|github_pat_[A-Za-z0-9_]+|"
    r"xox[a-z]-[A-Za-z0-9._-]+|AKIA[0-9A-Z]{8,}|AIza[A-Za-z0-9_-]+)",
    re.IGNORECASE,
)


class CodingSubagentConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    local_fake_child_runner_enabled: bool = Field(
        default=False,
        alias="localFakeChildRunnerEnabled",
    )
    additional_allowed_tools: tuple[str, ...] = Field(default=(), alias="additionalAllowedTools")
    production_child_execution_enabled: Literal[False] = Field(
        default=False,
        alias="productionChildExecutionEnabled",
    )
    production_workspace_mutation_enabled: Literal[False] = Field(
        default=False,
        alias="productionWorkspaceMutationEnabled",
    )
    live_tool_execution_enabled: Literal[False] = Field(
        default=False,
        alias="liveToolExecutionEnabled",
    )
    background_mode_enabled: Literal[False] = Field(default=False, alias="backgroundModeEnabled")
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


class CodingSubagentAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    recipe_enabled: bool = Field(default=False, alias="recipeEnabled")
    local_fake_child_runner_enabled: bool = Field(
        default=False,
        alias="localFakeChildRunnerEnabled",
    )
    workspace_mutation_enabled: Literal[False] = Field(
        default=False,
        alias="workspaceMutationEnabled",
    )
    workspace_mutated: Literal[False] = Field(default=False, alias="workspaceMutated")
    background_mode_enabled: Literal[False] = Field(default=False, alias="backgroundModeEnabled")
    live_child_runner_enabled: Literal[False] = Field(
        default=False,
        alias="liveChildRunnerEnabled",
    )
    live_tool_execution_enabled: Literal[False] = Field(
        default=False,
        alias="liveToolExecutionEnabled",
    )
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
        "workspace_mutation_enabled",
        "workspace_mutated",
        "background_mode_enabled",
        "live_child_runner_enabled",
        "live_tool_execution_enabled",
        "production_authority",
        "traffic_attached",
        "user_visible_activation",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class CodingSubagentToolScope(BaseModel):
    model_config = _MODEL_CONFIG

    allowed_tools: tuple[str, ...] = Field(alias="allowedTools")
    denied_tools: tuple[str, ...] = Field(default=(), alias="deniedTools")
    mutation_intent_allowed: bool = Field(default=False, alias="mutationIntentAllowed")
    evidence_reporting_allowed: bool = Field(default=False, alias="evidenceReportingAllowed")

    @classmethod
    def inspect(cls, extra_tools: tuple[str, ...] = ()) -> Self:
        return cls(
            allowedTools=_INSPECT_TOOLS,
            deniedTools=_denied_tools(extra_tools, _INSPECT_TOOLS),
            mutationIntentAllowed=False,
            evidenceReportingAllowed=False,
        )

    @classmethod
    def code_review(cls, extra_tools: tuple[str, ...] = ()) -> Self:
        return cls(
            allowedTools=_CODE_REVIEW_TOOLS,
            deniedTools=_denied_tools(extra_tools, _CODE_REVIEW_TOOLS),
            mutationIntentAllowed=False,
            evidenceReportingAllowed=True,
        )

    @classmethod
    def implement_local(cls, extra_tools: tuple[str, ...] = ()) -> Self:
        return cls(
            allowedTools=_IMPLEMENT_TOOLS,
            deniedTools=_denied_tools(extra_tools, _IMPLEMENT_TOOLS),
            mutationIntentAllowed=True,
            evidenceReportingAllowed=True,
        )

    @classmethod
    def blocked(cls, extra_tools: tuple[str, ...] = ()) -> Self:
        return cls(
            allowedTools=(),
            deniedTools=tuple(sorted(set(extra_tools))),
            mutationIntentAllowed=False,
            evidenceReportingAllowed=False,
        )

    @field_validator("allowed_tools", "denied_tools")
    @classmethod
    def _validate_tool_names(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(item for item in dict.fromkeys(value) if _PUBLIC_REF_RE.fullmatch(item))

    def public_projection(self) -> dict[str, object]:
        return {
            "allowedTools": list(self.allowed_tools),
            "deniedTools": list(self.denied_tools),
            "mutationIntentAllowed": self.mutation_intent_allowed,
            "evidenceReportingAllowed": self.evidence_reporting_allowed,
        }


class CodingSubagentModeRequest(BaseModel):
    model_config = _MODEL_CONFIG

    mode: CodingSubagentMode
    parent_execution_id: str = Field(alias="parentExecutionId")
    turn_id: str = Field(alias="turnId")
    task_id: str = Field(alias="taskId")
    objective: str
    session_id: str = Field(alias="sessionId")
    workspace_ref: str = Field(alias="workspaceRef")
    budget_tokens: int = Field(default=512, alias="budgetTokens", ge=0)
    budget_ms: int = Field(default=5000, alias="budgetMs", ge=0)
    metadata: Mapping[str, object] = Field(default_factory=dict)

    @field_validator(
        "parent_execution_id",
        "turn_id",
        "task_id",
        "objective",
        "session_id",
        "workspace_ref",
    )
    @classmethod
    def _validate_public_text(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("coding subagent request fields must be non-empty")
        return clean[:240]


class CodingSubagentFinding(BaseModel):
    model_config = _MODEL_CONFIG

    finding_ref: str = Field(alias="findingRef")
    severity: Literal["info", "warning", "error"] = "warning"
    evidence_refs: tuple[str, ...] = Field(default=(), alias="evidenceRefs")
    artifact_refs: tuple[str, ...] = Field(default=(), alias="artifactRefs")

    def public_projection(self) -> dict[str, object]:
        return {
            "findingRef": self.finding_ref,
            "severity": self.severity,
            "evidenceRefs": list(self.evidence_refs),
            "artifactRefs": list(self.artifact_refs),
        }


class CodingSubagentResult(BaseModel):
    model_config = _MODEL_CONFIG

    mode: CodingSubagentMode
    status: CodingSubagentStatus
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")
    tool_scope: CodingSubagentToolScope = Field(alias="toolScope")
    child: ChildRunnerResult | None = None
    mutation_intent: CodingMutationDecision | None = Field(default=None, alias="mutationIntent")
    findings: tuple[CodingSubagentFinding, ...] = ()
    failure_event_ref: str | None = Field(default=None, alias="failureEventRef")
    authority_flags: CodingSubagentAuthorityFlags = Field(alias="authorityFlags")

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
        child_projection = self.child.public_projection() if self.child is not None else None
        projection: dict[str, object] = {
            "mode": self.mode,
            "status": self.status,
            "reasonCodes": list(self.reason_codes),
            "toolScope": self.tool_scope.public_projection(),
            "child": child_projection,
            "mutationIntent": (
                self.mutation_intent.public_projection()
                if self.mutation_intent is not None
                else None
            ),
            "findings": [finding.public_projection() for finding in self.findings],
            "authorityFlags": self.authority_flags.model_dump(by_alias=True),
        }
        if self.failure_event_ref is not None:
            projection["failureEvent"] = {
                "eventRef": self.failure_event_ref,
                "errorCode": self.reason_codes[0] if self.reason_codes else "child_failed",
            }
        return _sanitize_projection(projection)


class CodingSubagentRecipe:
    """Coding-owned child recipe policy; generic child execution stays in runtime."""

    def __init__(
        self,
        config: CodingSubagentConfig | None = None,
        *,
        child_runner: object | None = None,
        read_ledger: ReadLedger | None = None,
    ) -> None:
        self.config = config or CodingSubagentConfig()
        self.child_runner = child_runner
        self.read_ledger = read_ledger

    async def run(
        self,
        request: CodingSubagentModeRequest | Mapping[str, object],
    ) -> CodingSubagentResult:
        parsed = (
            request
            if isinstance(request, CodingSubagentModeRequest)
            else CodingSubagentModeRequest.model_validate(request)
        )
        scope = _tool_scope(parsed.mode, self.config.additional_allowed_tools)
        flags = CodingSubagentAuthorityFlags(
            recipeEnabled=self.config.enabled,
            localFakeChildRunnerEnabled=(
                self.config.local_fake_child_runner_enabled and self.child_runner is not None
            ),
            **_false_authority_overrides(),
        )
        if not self.config.enabled:
            return _result(
                parsed,
                "disabled",
                ("coding_subagent_recipe_disabled",),
                scope,
                flags=CodingSubagentAuthorityFlags(),
            )
        if parsed.metadata.get("delivery") == "background":
            return _result(
                parsed,
                "blocked",
                ("background_child_lifecycle_disabled",),
                scope,
                flags=flags,
            )
        if parsed.mode == "research":
            return _result(
                parsed,
                "blocked",
                ("research_mode_unavailable_for_coding_recipe",),
                scope,
                flags=flags,
            )
        if parsed.mode == "implement_local":
            return self._implementation_intent(parsed, scope, flags)

        child_request = ChildTaskRequest(
            parentExecutionId=parsed.parent_execution_id,
            turnId=parsed.turn_id,
            taskId=parsed.task_id,
            objective=parsed.objective,
            role="coding",
            delivery="return",
            budgetTokens=parsed.budget_tokens,
            budgetMs=parsed.budget_ms,
            metadata={
                "mode": parsed.mode,
                "allowedTools": scope.allowed_tools,
                "toolScopeRef": f"policy:{_digest(':'.join(scope.allowed_tools))}",
                "parentOwnsLifecycle": True,
            },
        )
        child = await LocalChildRunnerBoundary(
            ChildRunnerConfig(
                enabled=self.config.enabled,
                localFakeChildRunnerEnabled=self.config.local_fake_child_runner_enabled,
            ),
            child_runner=self.child_runner,
        ).run(child_request)
        if child.status == "disabled":
            return _result(
                parsed,
                "disabled",
                (child.error_code or "local_fake_child_runner_disabled",),
                scope,
                child=child,
                flags=flags,
            )
        if child.status == "blocked":
            return _result(
                parsed,
                "blocked",
                (child.error_code or "child_runner_blocked",),
                scope,
                child=child,
                flags=flags,
            )
        if child.status == "error":
            return _result(
                parsed,
                "error",
                (child.error_code or "child_runner_error",),
                scope,
                child=child,
                failure_event_ref=f"audit:{_digest(f'{parsed.task_id}:failure')}",
                flags=flags,
            )
        return _result(
            parsed,
            "accepted",
            ("coding_subagent_child_completed",),
            scope,
            child=child,
            findings=_findings(parsed, child) if parsed.mode == "code_review" else (),
            flags=flags,
        )

    def _implementation_intent(
        self,
        request: CodingSubagentModeRequest,
        scope: CodingSubagentToolScope,
        flags: CodingSubagentAuthorityFlags,
    ) -> CodingSubagentResult:
        raw_intent = request.metadata.get("mutationIntent")
        if not isinstance(raw_intent, Mapping):
            return _result(
                request,
                "blocked",
                ("mutation_intent_required",),
                scope,
                flags=flags,
            )
        if self.read_ledger is None:
            return _result(
                request,
                "blocked",
                ("read_ledger_required",),
                scope,
                flags=flags,
            )
        try:
            mutation = CodingMutationRequest.model_validate(
                {
                    "toolName": raw_intent.get("toolName"),
                    "sessionId": request.session_id,
                    "workspaceRef": request.workspace_ref,
                    "path": raw_intent.get("path"),
                    "currentDigest": raw_intent.get("currentDigest"),
                    "currentText": raw_intent.get("currentText"),
                    "oldString": raw_intent.get("oldString"),
                    "newString": raw_intent.get("newString"),
                    "patch": raw_intent.get("patch"),
                    "mutationKind": raw_intent.get("mutationKind", "edit"),
                    "replaceAll": raw_intent.get("replaceAll", False),
                    "explicitApproval": raw_intent.get("explicitApproval", False),
                }
            )
        except ValidationError:
            return _result(
                request,
                "blocked",
                ("invalid_mutation_intent",),
                scope,
                flags=flags,
            )
        decision = CodingMutationRecipe(
            CodingMutationConfig(enabled=True, localFakeApplyEnabled=False),
            read_ledger=self.read_ledger,
        ).evaluate(mutation)
        status: CodingSubagentStatus = (
            "approval_required" if decision.status == "approval_required" else "blocked"
        )
        return _result(
            request,
            status,
            decision.reason_codes,
            scope,
            mutation_intent=decision,
            flags=flags,
        )


def _result(
    request: CodingSubagentModeRequest,
    status: CodingSubagentStatus,
    reason_codes: tuple[str, ...],
    scope: CodingSubagentToolScope,
    *,
    child: ChildRunnerResult | None = None,
    mutation_intent: CodingMutationDecision | None = None,
    findings: tuple[CodingSubagentFinding, ...] = (),
    failure_event_ref: str | None = None,
    flags: CodingSubagentAuthorityFlags,
) -> CodingSubagentResult:
    _ = request
    return CodingSubagentResult(
        mode=request.mode,
        status=status,
        reasonCodes=reason_codes,
        toolScope=scope,
        child=child,
        mutationIntent=mutation_intent,
        findings=findings,
        failureEventRef=failure_event_ref,
        authorityFlags=flags,
    )


def _tool_scope(mode: CodingSubagentMode, extra_tools: tuple[str, ...]) -> CodingSubagentToolScope:
    if mode == "inspect":
        return CodingSubagentToolScope.inspect(extra_tools)
    if mode == "code_review":
        return CodingSubagentToolScope.code_review(extra_tools)
    if mode == "implement_local":
        return CodingSubagentToolScope.implement_local(extra_tools)
    return CodingSubagentToolScope.blocked(extra_tools)


def _findings(
    request: CodingSubagentModeRequest,
    child: ChildRunnerResult,
) -> tuple[CodingSubagentFinding, ...]:
    projection = child.public_projection()
    child_envelope = projection.get("childEnvelope")
    if not isinstance(child_envelope, Mapping):
        return ()
    evidence_refs = tuple(
        item for item in child_envelope.get("evidenceRefs", ()) if isinstance(item, str)
    )
    artifact_refs = tuple(
        item for item in child_envelope.get("artifactRefs", ()) if isinstance(item, str)
    )
    if not evidence_refs and not artifact_refs:
        return ()
    return (
        CodingSubagentFinding(
            findingRef=f"finding:{_digest(f'{request.task_id}:code-review:0')}",
            severity="warning",
            evidenceRefs=evidence_refs[:4],
            artifactRefs=artifact_refs[:2],
        ),
    )


def _denied_tools(extra_tools: tuple[str, ...], allowed_tools: tuple[str, ...]) -> tuple[str, ...]:
    allowed = set(allowed_tools)
    return tuple(sorted({tool for tool in extra_tools if tool not in allowed}))


def _coerce_authority_flags(value: object) -> CodingSubagentAuthorityFlags:
    if isinstance(value, CodingSubagentAuthorityFlags):
        return value
    if isinstance(value, Mapping):
        return CodingSubagentAuthorityFlags.model_construct(**dict(value))
    return CodingSubagentAuthorityFlags()


def _false_authority_overrides() -> dict[str, Literal[False]]:
    return {
        "workspaceMutationEnabled": False,
        "workspaceMutated": False,
        "backgroundModeEnabled": False,
        "liveChildRunnerEnabled": False,
        "liveToolExecutionEnabled": False,
        "productionAuthority": False,
        "trafficAttached": False,
        "userVisibleActivation": False,
    }


def _sanitize_projection(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _sanitize_projection(item)
            for key, item in value.items()
            if not _PRIVATE_TEXT_RE.search(str(key))
        }
    if isinstance(value, tuple | list):
        return [_sanitize_projection(item) for item in value]
    if isinstance(value, str):
        return _sanitize_text(value)
    return value


def _sanitize_text(value: str) -> str:
    lines = [line for line in value.splitlines() if _PRIVATE_TEXT_RE.search(line) is None]
    clean = "\n".join(lines)
    clean = _PRIVATE_TEXT_RE.sub("[redacted]", clean)
    return clean[:512]


def _digest(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


__all__ = [
    "CodingSubagentConfig",
    "CodingSubagentFinding",
    "CodingSubagentModeRequest",
    "CodingSubagentRecipe",
    "CodingSubagentResult",
    "CodingSubagentToolScope",
]
