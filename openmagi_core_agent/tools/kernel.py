from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Mapping, Sequence
from inspect import isawaitable
from time import monotonic
from typing import Any, Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_serializer, model_validator

from openmagi_core_agent.artifacts.local_result_store import (
    LocalResultStore,
    is_trusted_local_result_store,
)
from openmagi_core_agent.evidence.tool_boundary import (
    ToolEvidenceRecord,
    build_denied_tool_error_evidence,
    build_tool_call_evidence,
    build_tool_exception_evidence,
    build_tool_result_evidence,
    build_tool_timeout_evidence,
)
from openmagi_core_agent.runtime.request_ledger import (
    ApprovalGateResult,
    RequestLedgerAuthorityFlags,
    RequestLedgerConfig,
    RequestShapeLedger,
    RequestShapeLedgerEntry,
    RequestShapeLedgerResult,
)

from .context import ToolContext
from .manifest import RuntimeMode
from .output_budget import budget_tool_result
from .permission import ToolPermissionPolicy
from .registry import ToolRegistry
from .result import ToolResult
from .schema_validation import validate_tool_arguments


ToolExecutionStatus: TypeAlias = Literal[
    "ok",
    "error",
    "blocked",
    "needs_approval",
]
PolicyFailureReason: TypeAlias = Literal[
    "denied",
    "not_found",
    "not_exposed",
    "missing_handler",
]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    arbitrary_types_allowed=True,
    hide_input_in_errors=True,
)
_DISABLED_METADATA = {
    "reason": "tool execution kernel disabled by default",
    "defaultOff": True,
    "handlerExecutionAllowed": False,
}
_PRIVATE_TEXT_RE = re.compile(
    r"(?:"
    r"authorization\s*:\s*[^\n\r,;}\"']+|"
    r"\bbearer\s+[A-Za-z0-9._~+/=-]+|"
    r"\bbasic\s+[A-Za-z0-9._~+/=-]+|"
    r"\bcookie\s*:\s*[^\n\r]+|"
    r"\bsid=[A-Za-z0-9._-]+|"
    r"\bsk-[A-Za-z0-9._-]+|"
    r"gh[opusr]_[A-Za-z0-9_]+|"
    r"github_pat_[A-Za-z0-9_]+|"
    r"xox[a-z]-[A-Za-z0-9._-]+|"
    r"AKIA[0-9A-Z]{8,}|"
    r"AIza[A-Za-z0-9_-]+|"
    r"(?:(?:session(?:[_-]?(?:key|id)|key|id))\s*[:=]\s*|session\s*=\s*)"
    r"[^\s,;}\"']+|"
    r"/workspace(?:/[^\s,;}\"']*)?|"
    r"/data/bots(?:/[^\s,;}\"']*)?|"
    r"/Users(?:/[^\s,;}\"']*)?|"
    r"/home(?:/[^\s,;}\"']*)?|"
    r"/var/lib/kubelet(?:/[^\s,;}\"']*)?|"
    r"raw[_ -]?(?:tool|child|prompt|transcript|output|result|log|args)|"
    r"hidden[_ -]?reasoning|chain[_ -]?of[_ -]?thought"
    r")",
    re.IGNORECASE,
)
_PUBLIC_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,180}$")


class _ToolKernelModel(BaseModel):
    model_config = _MODEL_CONFIG

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        return cls(**values)

    def model_copy(
        self,
        *,
        update: Mapping[str, object] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=False, mode="python", warnings=False)
        if update:
            alias_to_name = {
                field.alias: name
                for name, field in self.__class__.model_fields.items()
                if field.alias is not None
            }
            data.update({alias_to_name.get(key, key): value for key, value in update.items()})
        return self.__class__.model_validate(data)


class ToolExecutionKernelConfig(_ToolKernelModel):
    enabled: bool = False
    local_fake_handler_execution_enabled: bool = Field(
        default=False,
        alias="localFakeHandlerExecutionEnabled",
    )
    request_ledger_enabled: bool = Field(default=False, alias="requestLedgerEnabled")
    output_budget_enabled: bool = Field(default=False, alias="outputBudgetEnabled")
    local_fake_result_store_enabled: bool = Field(
        default=False,
        alias="localFakeResultStoreEnabled",
    )
    public_event_projection_enabled: bool = Field(
        default=False,
        alias="publicEventProjectionEnabled",
    )


class ToolExecutionRequest(_ToolKernelModel):
    tool_name: str = Field(alias="toolName")
    arguments: dict[str, object] = Field(default_factory=dict)
    context: ToolContext
    mode: RuntimeMode
    exposed_tool_names: tuple[str, ...] | None = Field(
        default=None,
        alias="exposedToolNames",
    )
    tool_call_id: str | None = Field(default=None, alias="toolCallId")
    model_input_refs: tuple[str, ...] = Field(default=(), alias="modelInputRefs")
    control_refs: tuple[str, ...] = Field(default=(), alias="controlRefs")
    validator_refs: tuple[str, ...] = Field(default=(), alias="validatorRefs")
    checkpoint_refs: tuple[str, ...] = Field(default=(), alias="checkpointRefs")
    evidence_refs: tuple[str, ...] = Field(default=(), alias="evidenceRefs")
    _approval_resume_decision: object | None = PrivateAttr(default=None)

    @field_serializer("context")
    def _serialize_context(self, value: ToolContext) -> dict[str, object]:
        return value.model_dump(by_alias=True, mode="json", warnings=False)

    def _attach_approval_resume_decision(self, decision: object | None) -> None:
        object.__setattr__(self, "_approval_resume_decision", decision)

    def _private_approval_resume_decision(self) -> object | None:
        return self._approval_resume_decision


class ToolExecutionOutcome(_ToolKernelModel):
    status: ToolExecutionStatus
    reason_code: str = Field(alias="reasonCode")
    result: ToolResult
    request_ledger_result: RequestShapeLedgerResult = Field(alias="requestLedgerResult")
    evidence_records: tuple[ToolEvidenceRecord, ...] = Field(
        default=(),
        alias="evidenceRecords",
    )
    output_projection: Mapping[str, object] | None = Field(
        default=None,
        alias="outputProjection",
    )
    approval_gate: ApprovalGateResult | None = Field(default=None, alias="approvalGate")
    handler_called: bool = Field(default=False, alias="handlerCalled")
    executed: bool = False
    blocking: bool = True
    authority_flags: RequestLedgerAuthorityFlags = Field(
        default_factory=RequestLedgerAuthorityFlags,
        alias="authorityFlags",
    )

    @model_validator(mode="after")
    def _force_authority_flags(self) -> Self:
        if self.authority_flags == RequestLedgerAuthorityFlags():
            return self
        return type(self)(
            status=self.status,
            reasonCode=self.reason_code,
            result=self.result,
            requestLedgerResult=self.request_ledger_result,
            evidenceRecords=self.evidence_records,
            outputProjection=self.output_projection,
            approvalGate=self.approval_gate,
            handlerCalled=self.handler_called,
            executed=self.executed,
            blocking=self.blocking,
            authorityFlags=RequestLedgerAuthorityFlags(),
        )


class ToolExecutionKernel:
    def __init__(
        self,
        registry: ToolRegistry,
        *,
        permission_policy: ToolPermissionPolicy | None = None,
        config: ToolExecutionKernelConfig | Mapping[str, object] | None = None,
        request_ledger: RequestShapeLedger | None = None,
        local_fake_executor: object | None = None,
        local_result_store: object | None = None,
    ) -> None:
        self.registry = registry
        self.permission_policy = permission_policy or ToolPermissionPolicy()
        self.config = ToolExecutionKernelConfig.model_validate(config or {})
        self.request_ledger = request_ledger or RequestShapeLedger()
        self.local_fake_executor = local_fake_executor
        self.local_result_store = local_result_store

    async def execute(
        self,
        request: ToolExecutionRequest | Mapping[str, object],
    ) -> ToolExecutionOutcome:
        approval_resume_decision = _private_resume_decision_from_request(request)
        safe_request = (
            request
            if isinstance(request, ToolExecutionRequest)
            else ToolExecutionRequest.model_validate(request)
        )
        safe_request._attach_approval_resume_decision(approval_resume_decision)
        ledger_result = self._record_request_shape(safe_request)
        available_tools = _available_tool_names(
            self.registry,
            safe_request.exposed_tool_names,
            mode=safe_request.mode,
        )

        if not self.config.enabled or not self.config.local_fake_handler_execution_enabled:
            return await self._emit_public_events(
                safe_request,
                self._blocked_outcome(
                    safe_request,
                    ledger_result=ledger_result,
                    reason_code="tool_execution_disabled",
                    result=ToolResult(
                        status="blocked",
                        metadata={
                            **_DISABLED_METADATA,
                            "toolName": safe_request.tool_name,
                            "mode": safe_request.mode,
                            "availableTools": available_tools,
                            "localFakeHandlerExecutionEnabled": (
                                self.config.local_fake_handler_execution_enabled
                            ),
                        },
                    ),
                    evidence_reason="denied",
                    evidence_message="tool execution kernel disabled by default",
                ),
            )

        registration = self.registry.resolve_registration(safe_request.tool_name)
        if registration is None:
            return await self._emit_public_events(
                safe_request,
                self._blocked_outcome(
                    safe_request,
                    ledger_result=ledger_result,
                    reason_code="tool_not_found",
                    result=ToolResult(
                        status="error",
                        errorCode="tool_not_found",
                        errorMessage="tool not found",
                        metadata={
                            "toolName": safe_request.tool_name,
                            "mode": safe_request.mode,
                            "reason": "tool not found",
                            "availableTools": available_tools,
                        },
                    ),
                    evidence_reason="not_found",
                    evidence_message="tool not found",
                ),
            )

        manifest = registration.manifest
        if (
            safe_request.exposed_tool_names is not None
            and manifest.name not in safe_request.exposed_tool_names
        ):
            return await self._emit_public_events(
                safe_request,
                self._blocked_outcome(
                    safe_request,
                    ledger_result=ledger_result,
                    reason_code="tool_not_exposed",
                    result=ToolResult(
                        status="error",
                        errorCode="tool_not_exposed",
                        errorMessage="tool not exposed to this turn",
                        metadata={
                            "toolName": manifest.name,
                            "permissionClass": manifest.permission,
                            "mode": safe_request.mode,
                            "dangerous": manifest.dangerous,
                            "mutatesWorkspace": manifest.mutates_workspace,
                            "reason": "not exposed to this turn",
                            "availableTools": available_tools,
                        },
                    ),
                    evidence_reason="not_exposed",
                    evidence_message="tool not exposed to this turn",
                    tool_id=manifest.name,
                ),
            )

        if not registration.enabled:
            return await self._emit_public_events(
                safe_request,
                self._blocked_outcome(
                    safe_request,
                    ledger_result=ledger_result,
                    reason_code="tool_disabled",
                    result=ToolResult(
                        status="blocked",
                        metadata=_manifest_metadata(
                            manifest,
                            mode=safe_request.mode,
                            reason="tool disabled",
                        ),
                    ),
                    evidence_reason="denied",
                    evidence_message="tool disabled",
                    tool_id=manifest.name,
                ),
            )

        if safe_request.mode not in manifest.available_in_modes:
            return await self._emit_public_events(
                safe_request,
                self._blocked_outcome(
                    safe_request,
                    ledger_result=ledger_result,
                    reason_code="tool_unavailable_in_mode",
                    result=ToolResult(
                        status="blocked",
                        metadata=_manifest_metadata(
                            manifest,
                            mode=safe_request.mode,
                            reason=f"tool unavailable in {safe_request.mode} mode",
                        ),
                    ),
                    evidence_reason="denied",
                    evidence_message=f"tool unavailable in {safe_request.mode} mode",
                    tool_id=manifest.name,
                ),
            )

        schema_decision = validate_tool_arguments(manifest, safe_request.arguments)
        if not schema_decision.valid:
            return await self._emit_public_events(
                safe_request,
                self._blocked_outcome(
                    safe_request,
                    ledger_result=ledger_result,
                    reason_code="tool_input_schema_invalid",
                    result=ToolResult(
                        status="blocked",
                        errorCode="tool_input_schema_invalid",
                        errorMessage="tool input did not match manifest schema",
                        metadata={
                            "toolName": manifest.name,
                            "mode": safe_request.mode,
                            "reason": "input schema validation failed",
                            "schemaValidation": schema_decision.public_projection(),
                        },
                    ),
                    evidence_reason="denied",
                    evidence_message="tool input schema invalid",
                    tool_id=manifest.name,
                ),
            )

        if self.local_fake_executor is None:
            return await self._emit_public_events(
                safe_request,
                self._blocked_outcome(
                    safe_request,
                    ledger_result=ledger_result,
                    reason_code="local_fake_tool_executor_disabled",
                    result=ToolResult(
                        status="error",
                        errorCode="local_fake_tool_executor_disabled",
                        errorMessage="local fake tool executor disabled",
                        metadata=_manifest_metadata(
                            manifest,
                            mode=safe_request.mode,
                            reason="local fake tool executor disabled",
                        ),
                    ),
                    evidence_reason="missing_handler",
                    evidence_message="local fake tool executor disabled",
                    tool_id=manifest.name,
                ),
            )
        if getattr(self.local_fake_executor, "openmagi_local_fake_provider", False) is not True:
            return await self._emit_public_events(
                safe_request,
                self._blocked_outcome(
                    safe_request,
                    ledger_result=ledger_result,
                    reason_code="local_fake_tool_executor_untrusted",
                    result=ToolResult(
                        status="blocked",
                        metadata=_manifest_metadata(
                            manifest,
                            mode=safe_request.mode,
                            reason="local fake tool executor untrusted",
                        ),
                    ),
                    evidence_reason="denied",
                    evidence_message="local fake tool executor untrusted",
                    tool_id=manifest.name,
                ),
            )

        decision = self.permission_policy.decide(
            manifest,
            safe_request.arguments,
            safe_request.context,
            mode=safe_request.mode,
        )
        has_resume_decision = _private_resume_decision_from_request(safe_request) is not None
        resume_grant_accepted = False
        if decision.action == "deny":
            return await self._emit_public_events(
                safe_request,
                self._blocked_outcome(
                    safe_request,
                    ledger_result=ledger_result,
                    reason_code="tool_permission_denied",
                    result=ToolResult(status="blocked", metadata=decision.metadata),
                    evidence_reason="denied",
                    evidence_message=decision.reason,
                    tool_id=manifest.name,
                ),
            )
        if decision.action == "ask":
            resume_grant_accepted = _approved_resume_grant_matches(safe_request, manifest.name)
            if not resume_grant_accepted:
                control_refs = _control_refs_from_metadata(decision.metadata)
                approval_gate = ApprovalGateResult(
                    requestId=control_refs[0].replace("control://", "", 1)
                    if control_refs
                    else (
                        f"tool-permission:"
                        f"{safe_request.context.turn_id or 'unknown-turn'}:{manifest.name}"
                    ),
                    status="pending",
                    controlRefs=control_refs,
                )
                evidence = (
                    build_denied_tool_error_evidence(
                        tool_call_id=_tool_call_id(safe_request, manifest.name),
                        tool_id=manifest.name,
                        tool_name=manifest.name,
                        reason="denied",
                        message=decision.reason,
                        observed_at=monotonic(),
                    ),
                )
                return await self._emit_public_events(
                    safe_request,
                    ToolExecutionOutcome(
                        status="needs_approval",
                        reasonCode="tool_approval_required",
                        result=ToolResult(
                            status="needs_approval",
                            metadata=_sanitize_mapping(decision.metadata),
                        ),
                        requestLedgerResult=ledger_result,
                        evidenceRecords=evidence,
                        approvalGate=approval_gate,
                        handlerCalled=False,
                        executed=False,
                        blocking=True,
                        authorityFlags=RequestLedgerAuthorityFlags(),
                    ),
                )
        elif has_resume_decision:
            resume_grant_accepted = _approved_resume_grant_matches(safe_request, manifest.name)
            if not resume_grant_accepted:
                return await self._emit_public_events(
                    safe_request,
                    self._blocked_outcome(
                        safe_request,
                        ledger_result=ledger_result,
                        reason_code="approval_resume_invalid_or_reused",
                        result=ToolResult(
                            status="blocked",
                            metadata={
                                **_manifest_metadata(
                                    manifest,
                                    mode=safe_request.mode,
                                    reason="approval resume grant invalid or already used",
                                ),
                                "approvalResumeRequired": True,
                            },
                        ),
                        evidence_reason="denied",
                        evidence_message="approval resume grant invalid or already used",
                        tool_id=manifest.name,
                    ),
                )
        if resume_grant_accepted:
            safe_request = safe_request.model_copy(
                update={
                    "validator_refs": _dedupe_refs(
                        (*safe_request.validator_refs, "validator:approval_resume_checked")
                    )
                }
            )

        started = monotonic()
        call_record = build_tool_call_evidence(
            tool_call_id=_tool_call_id(safe_request, manifest.name),
            tool_id=manifest.name,
            tool_name=manifest.name,
            args=safe_request.arguments,
            observed_at=started,
        )
        try:
            maybe_result = self.local_fake_executor.execute_tool(
                tool_name=manifest.name,
                arguments=safe_request.arguments,
                context=safe_request.context,
            )
            if isawaitable(maybe_result):
                result = await asyncio.wait_for(maybe_result, timeout=manifest.timeout_ms / 1000)
            else:
                result = maybe_result
            duration_ms = _duration_ms(started)
            safe_result = _sanitize_tool_result(ToolResult.model_validate(result))
            result_record = build_tool_result_evidence(
                tool_call_id=_tool_call_id(safe_request, manifest.name),
                tool_id=manifest.name,
                tool_name=manifest.name,
                status="ok" if safe_result.status == "ok" else "error",
                result=safe_result.model_dump(by_alias=True, mode="python", warnings=False),
                duration_ms=duration_ms,
                observed_at=monotonic(),
            )
            output_projection = self._project_output_result(
                manifest,
                safe_result,
                schema_decision=schema_decision,
            )
            return await self._emit_public_events(
                safe_request,
                ToolExecutionOutcome(
                    status=safe_result.status,
                    reasonCode="tool_executed",
                    result=safe_result,
                    requestLedgerResult=ledger_result,
                    evidenceRecords=(call_record, result_record),
                    outputProjection=output_projection,
                    handlerCalled=True,
                    executed=True,
                    blocking=False,
                    authorityFlags=RequestLedgerAuthorityFlags(),
                ),
            )
        except TimeoutError:
            duration_ms = _duration_ms(started)
            timeout_record = build_tool_timeout_evidence(
                tool_call_id=_tool_call_id(safe_request, manifest.name),
                tool_id=manifest.name,
                tool_name=manifest.name,
                timeout_ms=manifest.timeout_ms,
                duration_ms=duration_ms,
                observed_at=monotonic(),
            )
            return await self._emit_public_events(
                safe_request,
                ToolExecutionOutcome(
                    status="error",
                    reasonCode="tool_timeout",
                    result=ToolResult(
                        status="error",
                        errorCode="tool_timeout",
                        errorMessage="tool timed out",
                        durationMs=duration_ms,
                        retryable=True,
                        metadata=_manifest_metadata(
                            manifest,
                            mode=safe_request.mode,
                            reason="tool timed out",
                        ),
                    ),
                    requestLedgerResult=ledger_result,
                    evidenceRecords=(call_record, timeout_record),
                    handlerCalled=True,
                    executed=True,
                    blocking=True,
                    authorityFlags=RequestLedgerAuthorityFlags(),
                ),
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            duration_ms = _duration_ms(started)
            error_record = build_tool_exception_evidence(
                tool_call_id=_tool_call_id(safe_request, manifest.name),
                tool_id=manifest.name,
                tool_name=manifest.name,
                error=error,
                duration_ms=duration_ms,
                observed_at=monotonic(),
            )
            return await self._emit_public_events(
                safe_request,
                ToolExecutionOutcome(
                    status="error",
                    reasonCode="tool_exception",
                    result=ToolResult(
                        status="error",
                        errorCode="tool_threw",
                        errorMessage="[redacted-error]",
                        durationMs=duration_ms,
                        retryable=False,
                        metadata=_manifest_metadata(
                            manifest,
                            mode=safe_request.mode,
                            reason="tool handler raised",
                        ),
                    ),
                    requestLedgerResult=ledger_result,
                    evidenceRecords=(call_record, error_record),
                    handlerCalled=True,
                    executed=True,
                    blocking=True,
                    authorityFlags=RequestLedgerAuthorityFlags(),
                ),
            )

    async def _emit_public_events(
        self,
        request: ToolExecutionRequest,
        outcome: ToolExecutionOutcome,
    ) -> ToolExecutionOutcome:
        if not self.config.public_event_projection_enabled:
            return outcome
        emitter = request.context.emit_agent_event
        if not callable(emitter):
            return outcome
        from .event_projection import project_tool_execution_events

        for event in project_tool_execution_events(request, outcome):
            emitted = emitter(event)
            if isawaitable(emitted):
                await emitted
        return outcome

    def _project_output_result(
        self,
        manifest: object,
        result: ToolResult,
        *,
        schema_decision: object,
    ) -> dict[str, object] | None:
        if not self.config.output_budget_enabled:
            return None

        budgeted = budget_tool_result(result, budget=getattr(manifest, "budget", None))
        store_receipt = None
        if (
            self.config.local_fake_result_store_enabled
            and self.local_result_store is not None
            and is_trusted_local_result_store(self.local_result_store)
        ):
            store_receipt = LocalResultStore.put_tool_result(
                self.local_result_store,
                budgeted,
                metadata={"toolName": getattr(manifest, "name", "unknown-tool")},
            )
        return budgeted.public_projection(
            store_receipt=store_receipt,
            validation_decision=schema_decision,
        )

    def _record_request_shape(
        self,
        request: ToolExecutionRequest,
    ) -> RequestShapeLedgerResult:
        return self.request_ledger.record(
            RequestShapeLedgerEntry(
                turnId=request.context.turn_id or "unknown-turn",
                stage="tool_request",
                modelInputRefs=request.model_input_refs,
                toolRefs=(f"tool:{request.tool_name}",),
                controlRefs=request.control_refs,
                validatorRefs=request.validator_refs,
                checkpointRefs=request.checkpoint_refs,
                evidenceRefs=request.evidence_refs,
                rawPayload=request.arguments,
            ),
            config=RequestLedgerConfig(enabled=self.config.request_ledger_enabled),
        )

    def _blocked_outcome(
        self,
        request: ToolExecutionRequest,
        *,
        ledger_result: RequestShapeLedgerResult,
        reason_code: str,
        result: ToolResult,
        evidence_reason: PolicyFailureReason,
        evidence_message: str,
        tool_id: str | None = None,
    ) -> ToolExecutionOutcome:
        name = tool_id or request.tool_name
        evidence = (
            build_denied_tool_error_evidence(
                tool_call_id=_tool_call_id(request, name),
                tool_id=name,
                tool_name=name,
                reason=evidence_reason,
                message=evidence_message,
                observed_at=monotonic(),
            ),
        )
        return ToolExecutionOutcome(
            status=result.status,
            reasonCode=reason_code,
            result=_sanitize_tool_result(result),
            requestLedgerResult=ledger_result,
            evidenceRecords=evidence,
            handlerCalled=False,
            executed=False,
            blocking=True,
            authorityFlags=RequestLedgerAuthorityFlags(),
        )


def _available_tool_names(
    registry: ToolRegistry,
    exposed_tool_names: tuple[str, ...] | None,
    *,
    mode: RuntimeMode,
) -> tuple[str, ...]:
    if exposed_tool_names is not None:
        return tuple(sorted(dict.fromkeys(exposed_tool_names)))
    return tuple(tool.name for tool in registry.list_available(mode=mode))


def _dedupe_refs(refs: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(ref for ref in refs if ref))


def _private_resume_decision_from_request(request: object) -> object | None:
    getter = getattr(request, "_private_approval_resume_decision", None)
    if not callable(getter):
        return None
    return getter()


def _approved_resume_grant_matches(request: ToolExecutionRequest, tool_name: str) -> bool:
    decision = _private_resume_decision_from_request(request)
    if getattr(decision, "status", None) != "approved":
        return False

    pending_getter = getattr(decision, "_private_pending_tool_call", None)
    pending = pending_getter() if callable(pending_getter) else None
    if pending is None:
        return False

    raw_request_getter = getattr(pending, "_private_tool_execution_request", None)
    raw_request = raw_request_getter() if callable(raw_request_getter) else None
    if raw_request is None:
        return False
    expected_request = ToolExecutionRequest.model_validate(raw_request)

    if expected_request.tool_name != request.tool_name or expected_request.tool_name != tool_name:
        return False
    if expected_request.arguments != request.arguments:
        return False
    if expected_request.mode != request.mode:
        return False
    if expected_request.context != request.context:
        return False
    if expected_request.exposed_tool_names != request.exposed_tool_names:
        return False

    control_request_id = getattr(decision, "control_request_id", None)
    approval_decision_ref = getattr(decision, "approval_decision_ref", None)
    if not isinstance(control_request_id, str) or control_request_id not in request.control_refs:
        return False
    if not isinstance(approval_decision_ref, str) or approval_decision_ref not in request.control_refs:
        return False

    claim_execution = getattr(decision, "_claim_execution_once", None)
    if not callable(claim_execution):
        return False
    return bool(claim_execution())


def _manifest_metadata(manifest: object, *, mode: RuntimeMode, reason: str) -> dict[str, object]:
    return {
        "toolName": getattr(manifest, "name"),
        "permissionClass": getattr(manifest, "permission"),
        "mode": mode,
        "dangerous": getattr(manifest, "dangerous"),
        "mutatesWorkspace": getattr(manifest, "mutates_workspace"),
        "reason": reason,
    }


def _sanitize_tool_result(result: ToolResult) -> ToolResult:
    return ToolResult(
        status=result.status,
        output=_sanitize_value(result.output),
        llmOutput=_sanitize_value(result.llm_output),
        transcriptOutput=_sanitize_value(result.transcript_output),
        errorCode=_sanitize_text(result.error_code) if result.error_code else None,
        errorMessage=_sanitize_text(result.error_message) if result.error_message else None,
        durationMs=result.duration_ms,
        artifactRefs=tuple(_sanitize_ref(ref, prefix="artifact") for ref in result.artifact_refs),
        fileRefs=tuple(_sanitize_ref(ref, prefix="file") for ref in result.file_refs),
        deliveryReceipts=tuple(
            _sanitize_ref(ref, prefix="delivery") for ref in result.delivery_receipts
        ),
        retryable=result.retryable,
        metadata=_sanitize_mapping(result.metadata),
    )


def _sanitize_mapping(value: Mapping[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key, item in value.items():
        key_text = str(key)
        if _PRIVATE_TEXT_RE.search(key_text) or _is_sensitive_key(key_text):
            continue
        if key_text in {"controlRequest", "control_request"} and isinstance(item, Mapping):
            control_refs = _control_refs_from_metadata({"controlRequest": item})
            if control_refs:
                safe[key_text] = {
                    "requestRef": control_refs[0],
                    "status": "pending",
                }
            continue
        safe[key_text] = _sanitize_value(item)
    return safe


def _sanitize_value(value: object) -> object:
    if isinstance(value, str):
        return _sanitize_text(value)
    if isinstance(value, Mapping):
        return _sanitize_mapping(value)
    if isinstance(value, list | tuple):
        return tuple(_sanitize_value(item) for item in value)
    if isinstance(value, bool | int | float) or value is None:
        return value
    return _sanitize_text(str(value))


def _sanitize_text(value: str) -> str:
    return _PRIVATE_TEXT_RE.sub("[redacted-private]", value)[:500]


def _sanitize_ref(value: str, *, prefix: str) -> str:
    clean = _sanitize_text(value)
    if clean == value and _PUBLIC_REF_RE.fullmatch(clean):
        return clean
    return f"{prefix}:{hashlib.sha1(value.encode('utf-8')).hexdigest()[:16]}"


def _hashed_ref(value: str, *, prefix: str) -> str:
    return f"{prefix}:{hashlib.sha1(value.encode('utf-8')).hexdigest()[:16]}"


def _is_sensitive_key(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", value.casefold())
    return any(
        marker in normalized
        for marker in (
            "authorization",
            "cookie",
            "credential",
            "secret",
            "token",
            "password",
            "privatekey",
            "apikey",
            "servicekey",
            "key",
        )
    )


def _tool_call_id(request: ToolExecutionRequest, tool_name: str) -> str:
    if request.tool_call_id:
        return _sanitize_ref(request.tool_call_id, prefix="tool-call")
    turn_id = request.context.turn_id or "unknown-turn"
    return _hashed_ref(f"{turn_id}:{tool_name}", prefix="tool-call")


def _duration_ms(started: float) -> int:
    return max(0, int((monotonic() - started) * 1000))


def _control_refs_from_metadata(metadata: Mapping[str, object]) -> tuple[str, ...]:
    request = metadata.get("controlRequest")
    if not isinstance(request, Mapping):
        return ()
    request_id = request.get("requestId") or request.get("request_id")
    if not isinstance(request_id, str):
        return ()
    return (_hashed_ref(request_id, prefix="control"),)
