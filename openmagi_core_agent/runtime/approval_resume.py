from __future__ import annotations

import hashlib
import json
import re
import secrets
from copy import deepcopy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from openmagi_core_agent.runtime.request_ledger import RequestLedgerAuthorityFlags

if TYPE_CHECKING:
    from openmagi_core_agent.tools.kernel import ToolExecutionRequest
    from openmagi_core_agent.tools.result import ToolResult


ApprovalRiskLevel: TypeAlias = Literal["unknown", "low", "medium", "high", "critical"]
PendingToolCallState: TypeAlias = Literal["pending", "approved", "denied", "expired"]
ApprovalDecisionStatus: TypeAlias = Literal["approved", "denied", "blocked"]
ResumeDecisionStatus: TypeAlias = Literal["approved", "blocked"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    arbitrary_types_allowed=True,
    hide_input_in_errors=True,
)
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_PUBLIC_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,180}$")
_PRIVATE_TEXT_RE = re.compile(
    r"(?:"
    r"authorization\s*:\s*[^\n\r,;}\"']+|"
    r"\bbearer\s+[A-Za-z0-9._~+/=-]+|"
    r"\bbasic\s+[A-Za-z0-9._~+/=-]+|"
    r"\bcookie\s*:\s*[^\n\r,;}\"']+|"
    r"\bsid=[A-Za-z0-9._-]+|"
    r"\bsk-[A-Za-z0-9._-]+|"
    r"gh[opusr]_[A-Za-z0-9_]+|"
    r"github_pat_[A-Za-z0-9_]+|"
    r"xox[a-z]-[A-Za-z0-9._-]+|"
    r"AKIA[0-9A-Z]{8,}|"
    r"AIza[A-Za-z0-9_-]+|"
    r"session\s*[:=]|"
    r"session[_-]?(?:key|id)|"
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
_SENSITIVE_KEY_RE = re.compile(
    r"(?:authorization|cookie|credential|secret|token|password|private.?key|api.?key|"
    r"service.?key|session.?key|session.?id)",
    re.IGNORECASE,
)
_CAMEL_BOUNDARY_RE = re.compile(
    r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])"
)
_NON_ALNUM_RE = re.compile(r"[^0-9A-Za-z]+")
_COMMAND_KEYS = {"command", "cmd", "shell", "commandpreview"}
_PATH_KEYS = {
    "path",
    "filepath",
    "file",
    "targetpath",
    "targetpaths",
    "target",
    "workspacepath",
}


class _ApprovalResumeModel(BaseModel):
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


class ApprovalRequest(_ApprovalResumeModel):
    approval_request_id: str = Field(alias="approvalRequestId")
    control_request_id: str = Field(alias="controlRequestId")
    request_digest: str = Field(alias="requestDigest")
    turn_id: str = Field(alias="turnId")
    tool_name: str = Field(alias="toolName")
    tool_call_ref: str | None = Field(default=None, alias="toolCallRef")
    arguments_digest: str = Field(alias="argumentsDigest")
    arguments_ref: str = Field(alias="argumentsRef")
    reason: str
    reason_digest: str = Field(alias="reasonDigest")
    risk_level: ApprovalRiskLevel = Field(alias="riskLevel")
    target_path_refs: tuple[str, ...] = Field(default=(), alias="targetPathRefs")
    command_preview_digest: str | None = Field(
        default=None,
        alias="commandPreviewDigest",
    )
    command_preview_ref: str | None = Field(default=None, alias="commandPreviewRef")
    transcript_order_refs: tuple[str, ...] = Field(
        default=(),
        alias="transcriptOrderRefs",
    )
    created_at: int | float = Field(alias="createdAt")
    expires_at: int | float = Field(alias="expiresAt")


class PendingToolCall(_ApprovalResumeModel):
    pending_tool_call_ref: str = Field(alias="pendingToolCallRef")
    state: PendingToolCallState
    approval_request: ApprovalRequest = Field(alias="approvalRequest")
    _tool_execution_request: object | None = PrivateAttr(default=None)

    @property
    def request_digest(self) -> str:
        return self.approval_request.request_digest

    def _attach_tool_execution_request(self, request: object) -> None:
        object.__setattr__(self, "_tool_execution_request", request)

    def _private_tool_execution_request(self) -> object | None:
        return self._tool_execution_request


class ApprovalDecision(_ApprovalResumeModel):
    status: ApprovalDecisionStatus
    decision: Literal["approved", "denied"] | None = None
    approval_decision_ref: str = Field(alias="approvalDecisionRef")
    control_request_id: str = Field(alias="controlRequestId")
    request_digest: str = Field(alias="requestDigest")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    decided_at: int | float = Field(alias="decidedAt")
    resume_token_digest: str | None = Field(default=None, alias="resumeTokenDigest")
    resume_token_ref: str | None = Field(default=None, alias="resumeTokenRef")
    expires_at: int | float | None = Field(default=None, alias="expiresAt")
    authority_flags: RequestLedgerAuthorityFlags = Field(
        default_factory=RequestLedgerAuthorityFlags,
        alias="authorityFlags",
    )
    _resume_token: str | None = PrivateAttr(default=None)

    @property
    def resume_token(self) -> str | None:
        return self._resume_token

    def _attach_resume_token(self, token: str | None) -> None:
        object.__setattr__(self, "_resume_token", token)

    def to_blocked_tool_result(self) -> ToolResult:
        from openmagi_core_agent.tools.result import ToolResult

        reason_code = (
            "approval_denied"
            if self.status == "denied"
            else (self.reason_codes[0] if self.reason_codes else "approval_blocked")
        )
        return ToolResult(
            status="blocked",
            metadata={
                "reasonCode": reason_code,
                "requestDigest": self.request_digest,
                "controlRequestRef": self.control_request_id,
                "approvalDecisionRef": self.approval_decision_ref,
                "decision": self.decision or "blocked",
                "reasonCodes": self.reason_codes,
                "defaultOff": True,
                "handlerCalled": False,
                "executed": False,
            },
        )


class ResumeDecision(_ApprovalResumeModel):
    status: ResumeDecisionStatus
    control_request_id: str = Field(alias="controlRequestId")
    request_digest: str = Field(alias="requestDigest")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    pending_tool_call_ref: str | None = Field(default=None, alias="pendingToolCallRef")
    approval_decision_ref: str | None = Field(default=None, alias="approvalDecisionRef")
    resume_token_digest: str | None = Field(default=None, alias="resumeTokenDigest")
    resume_token_ref: str | None = Field(default=None, alias="resumeTokenRef")
    transcript_order_refs: tuple[str, ...] = Field(
        default=(),
        alias="transcriptOrderRefs",
    )
    execution_allowed: Literal[False] = Field(default=False, alias="executionAllowed")
    handler_called: Literal[False] = Field(default=False, alias="handlerCalled")
    authority_flags: RequestLedgerAuthorityFlags = Field(
        default_factory=RequestLedgerAuthorityFlags,
        alias="authorityFlags",
    )
    _pending_tool_call: PendingToolCall | None = PrivateAttr(default=None)
    _execution_claimed: bool = PrivateAttr(default=False)

    def _attach_pending_tool_call(self, pending: PendingToolCall | None) -> None:
        object.__setattr__(self, "_pending_tool_call", pending)

    def _private_pending_tool_call(self) -> PendingToolCall | None:
        return self._pending_tool_call

    def _claim_execution_once(self) -> bool:
        if self._execution_claimed:
            return False
        object.__setattr__(self, "_execution_claimed", True)
        return True


@dataclass
class _ResumeTokenRecord:
    token_digest: str
    token_ref: str
    control_request_id: str
    request_digest: str
    approval_decision_ref: str
    pending_tool_call_ref: str
    expires_at: int | float
    used: bool = False


class ApprovalResumeStore:
    """In-memory approval pause/resume store for local harness tests only."""

    durable_writes_enabled: Literal[False] = False
    production_writes_enabled: Literal[False] = False

    def __init__(self) -> None:
        self._pending_by_digest: dict[str, PendingToolCall] = {}
        self._terminal_by_digest: dict[str, PendingToolCall] = {}
        self._tokens: dict[str, _ResumeTokenRecord] = {}
        self._seq = 0

    def get_pending(self, request_digest: str) -> PendingToolCall | None:
        pending = self._pending_by_digest.get(request_digest)
        if pending is None or pending.state != "pending":
            return None
        return pending

    def get_terminal(self, request_digest: str) -> PendingToolCall | None:
        return self._terminal_by_digest.get(request_digest)

    def create_pending_from_needs_approval(
        self,
        tool_request: object,
        needs_approval: object,
        *,
        now: int | float,
        expires_at: int | float,
        transcript_order_refs: Sequence[str] = (),
        risk_level: ApprovalRiskLevel | None = None,
    ) -> PendingToolCall:
        request = _coerce_tool_execution_request(tool_request)
        arguments = _request_arguments(request)
        metadata = _approval_metadata(needs_approval)
        control_request_id = _safe_ref(
            _metadata_control_request_id(metadata, needs_approval)
            or f"{_request_turn_id(request)}:{_request_tool_name(request)}",
            prefix="control",
        )
        reason = _sanitize_reason(str(metadata.get("reason") or "approval required"))
        arguments_digest = _digest_value(arguments)
        reason_digest = _digest_text(reason)
        command = _extract_command(arguments)
        command_digest = _digest_text(command) if command is not None else None
        request_digest = _digest_value(
            {
                "argumentsDigest": arguments_digest,
                "commandPreviewDigest": command_digest,
                "controlRequestId": control_request_id,
                "executionSnapshot": _request_execution_snapshot(request),
                "reasonDigest": reason_digest,
                "toolName": _request_tool_name(request),
                "transcriptOrderRefs": list(transcript_order_refs),
                "turnId": _request_turn_id(request),
            }
        )
        duplicate = self._pending_by_digest.get(request_digest) or self._terminal_by_digest.get(
            request_digest
        )
        if duplicate is not None:
            return duplicate
        pending = PendingToolCall(
            pendingToolCallRef=_digest_ref(request_digest, prefix="pending"),
            state="pending",
            approvalRequest=ApprovalRequest(
                approvalRequestId=_digest_ref(request_digest, prefix="approval-request"),
                controlRequestId=control_request_id,
                requestDigest=request_digest,
                turnId=_sanitize_public_text(_request_turn_id(request)) or "unknown-turn",
                toolName=_sanitize_public_text(_request_tool_name(request)) or "unknown-tool",
                toolCallRef=_safe_optional_ref(_request_tool_call_id(request), prefix="tool-call"),
                argumentsDigest=arguments_digest,
                argumentsRef=_digest_ref(arguments_digest, prefix="args"),
                reason=reason,
                reasonDigest=reason_digest,
                riskLevel=risk_level or _metadata_risk_level(metadata),
                targetPathRefs=_extract_target_path_refs(arguments),
                commandPreviewDigest=command_digest,
                commandPreviewRef=(
                    _digest_ref(command_digest, prefix="command")
                    if command_digest is not None
                    else None
                ),
                transcriptOrderRefs=tuple(
                    _safe_ref(ref, prefix="transcript") for ref in transcript_order_refs
                ),
                createdAt=now,
                expiresAt=expires_at,
            ),
        )
        pending._attach_tool_execution_request(deepcopy(request))
        self._pending_by_digest[request_digest] = pending
        return pending

    def approve(
        self,
        *,
        control_request_id: str,
        request_digest: str,
        now: int | float,
    ) -> ApprovalDecision:
        pending = self._verified_pending(
            control_request_id=control_request_id,
            request_digest=request_digest,
            now=now,
        )
        if not isinstance(pending, PendingToolCall):
            return pending

        self._seq += 1
        token = _resume_token(
            request_digest=request_digest,
            control_request_id=pending.approval_request.control_request_id,
            seq=self._seq,
        )
        token_digest = _digest_text(token)
        approval_ref = _digest_ref(
            _digest_value(
                {
                    "controlRequestId": pending.approval_request.control_request_id,
                    "requestDigest": request_digest,
                    "seq": self._seq,
                }
            ),
            prefix="approval",
        )
        decision = ApprovalDecision(
            status="approved",
            decision="approved",
            approvalDecisionRef=approval_ref,
            controlRequestId=pending.approval_request.control_request_id,
            requestDigest=request_digest,
            reasonCodes=("approval_granted",),
            decidedAt=now,
            resumeTokenDigest=token_digest,
            resumeTokenRef=_digest_ref(token_digest, prefix="resume"),
            expiresAt=pending.approval_request.expires_at,
        )
        decision._attach_resume_token(token)
        self._tokens[token] = _ResumeTokenRecord(
            token_digest=token_digest,
            token_ref=decision.resume_token_ref or _digest_ref(token_digest, prefix="resume"),
            control_request_id=pending.approval_request.control_request_id,
            request_digest=request_digest,
            approval_decision_ref=approval_ref,
            pending_tool_call_ref=pending.pending_tool_call_ref,
            expires_at=pending.approval_request.expires_at,
        )
        self._set_terminal_state(pending, "approved")
        return decision

    def deny(
        self,
        *,
        control_request_id: str,
        request_digest: str,
        now: int | float,
        reason: str | None = None,
    ) -> ApprovalDecision:
        pending = self._verified_pending(
            control_request_id=control_request_id,
            request_digest=request_digest,
            now=now,
        )
        if not isinstance(pending, PendingToolCall):
            return pending

        decision_ref = _digest_ref(
            _digest_value(
                {
                    "controlRequestId": pending.approval_request.control_request_id,
                    "requestDigest": request_digest,
                    "reasonDigest": _digest_text(_sanitize_reason(reason or "denied")),
                }
            ),
            prefix="approval",
        )
        self._set_terminal_state(pending, "denied")
        return ApprovalDecision(
            status="denied",
            decision="denied",
            approvalDecisionRef=decision_ref,
            controlRequestId=pending.approval_request.control_request_id,
            requestDigest=request_digest,
            reasonCodes=("approval_denied",),
            decidedAt=now,
            expiresAt=pending.approval_request.expires_at,
        )

    def resume(
        self,
        resume_token: str | None,
        *,
        control_request_id: str,
        request_digest: str,
        now: int | float,
    ) -> ResumeDecision:
        safe_control_request_id = _safe_ref(control_request_id, prefix="control")
        safe_request_digest = _safe_digest(request_digest)
        if not resume_token or resume_token not in self._tokens:
            return _blocked_resume_decision(
                control_request_id=safe_control_request_id,
                request_digest=safe_request_digest,
                reason_code="invalid_resume_token",
            )

        record = self._tokens[resume_token]
        if record.used:
            return _blocked_resume_decision(
                control_request_id=safe_control_request_id,
                request_digest=safe_request_digest,
                reason_code="resume_token_reused",
                token_record=record,
            )
        if (
            record.control_request_id != safe_control_request_id
            or record.request_digest != safe_request_digest
        ):
            return _blocked_resume_decision(
                control_request_id=safe_control_request_id,
                request_digest=safe_request_digest,
                reason_code="approval_request_mismatch",
                token_record=record,
            )

        pending = self._terminal_by_digest.get(record.request_digest)
        if pending is None:
            return _blocked_resume_decision(
                control_request_id=safe_control_request_id,
                request_digest=safe_request_digest,
                reason_code="pending_tool_call_not_found",
                token_record=record,
            )
        if now > pending.approval_request.expires_at:
            self._set_terminal_state(pending, "expired")
            return _blocked_resume_decision(
                control_request_id=safe_control_request_id,
                request_digest=safe_request_digest,
                reason_code="approval_request_expired",
                token_record=record,
            )
        if pending.state == "denied":
            return _blocked_resume_decision(
                control_request_id=safe_control_request_id,
                request_digest=safe_request_digest,
                reason_code="approval_request_denied",
                token_record=record,
            )
        if pending.state != "approved":
            return _blocked_resume_decision(
                control_request_id=safe_control_request_id,
                request_digest=safe_request_digest,
                reason_code="approval_request_not_approved",
                token_record=record,
            )

        record.used = True
        decision = ResumeDecision(
            status="approved",
            controlRequestId=record.control_request_id,
            requestDigest=record.request_digest,
            reasonCodes=("resume_token_accepted",),
            pendingToolCallRef=record.pending_tool_call_ref,
            approvalDecisionRef=record.approval_decision_ref,
            resumeTokenDigest=record.token_digest,
            resumeTokenRef=record.token_ref,
            transcriptOrderRefs=pending.approval_request.transcript_order_refs,
        )
        decision._attach_pending_tool_call(pending)
        return decision

    def public_projection(self) -> dict[str, object]:
        return {
            "durableWritesEnabled": False,
            "productionWritesEnabled": False,
            "pendingCount": sum(
                1 for pending in self._pending_by_digest.values() if pending.state == "pending"
            ),
            "terminalCount": len(self._terminal_by_digest),
            "resumeTokenCount": len(self._tokens),
        }

    def _verified_pending(
        self,
        *,
        control_request_id: str,
        request_digest: str,
        now: int | float,
    ) -> PendingToolCall | ApprovalDecision:
        safe_control_request_id = _safe_ref(control_request_id, prefix="control")
        safe_request_digest = _safe_digest(request_digest)
        pending = self._pending_by_digest.get(safe_request_digest)
        if pending is None:
            terminal = self._terminal_by_digest.get(safe_request_digest)
            if terminal is not None and terminal.state == "denied":
                reason_code = "approval_request_denied"
            else:
                reason_code = "pending_tool_call_not_found"
            return _blocked_approval_decision(
                control_request_id=safe_control_request_id,
                request_digest=safe_request_digest,
                now=now,
                reason_code=reason_code,
            )
        if pending.approval_request.control_request_id != safe_control_request_id:
            return _blocked_approval_decision(
                control_request_id=safe_control_request_id,
                request_digest=safe_request_digest,
                now=now,
                reason_code="approval_request_mismatch",
            )
        if now > pending.approval_request.expires_at:
            self._set_terminal_state(pending, "expired")
            return _blocked_approval_decision(
                control_request_id=safe_control_request_id,
                request_digest=safe_request_digest,
                now=now,
                reason_code="approval_request_expired",
            )
        return pending

    def _set_terminal_state(
        self,
        pending: PendingToolCall,
        state: PendingToolCallState,
    ) -> PendingToolCall:
        updated = pending.model_copy(update={"state": state})
        updated._attach_tool_execution_request(pending._private_tool_execution_request())
        self._pending_by_digest.pop(pending.request_digest, None)
        self._terminal_by_digest[pending.request_digest] = updated
        return updated


def build_tool_execution_request_for_resume(decision: ResumeDecision) -> ToolExecutionRequest:
    if decision.status != "approved":
        raise ValueError("resume decision is not approved")
    pending = decision._private_pending_tool_call()
    if pending is None:
        raise ValueError("approved resume decision is missing pending tool call")
    raw_request = pending._private_tool_execution_request()
    if raw_request is None:
        raise ValueError("pending tool call is missing private execution request")

    from openmagi_core_agent.tools.kernel import ToolExecutionRequest

    request = ToolExecutionRequest.model_validate(raw_request)
    control_refs = _dedupe_refs(
        (
            *request.control_refs,
            pending.approval_request.control_request_id,
            decision.approval_decision_ref or "approval:unknown",
        )
    )
    validator_refs = _dedupe_refs((*request.validator_refs, "validator:approval_resume"))
    resumed = request.model_copy(
        update={
            "control_refs": control_refs,
            "validator_refs": validator_refs,
        }
    )
    attach = getattr(resumed, "_attach_approval_resume_decision", None)
    if callable(attach):
        attach(decision)
    return resumed


def _coerce_tool_execution_request(value: object) -> object:
    if isinstance(value, Mapping):
        from openmagi_core_agent.tools.kernel import ToolExecutionRequest

        return ToolExecutionRequest.model_validate(value)
    return value


def _request_tool_name(request: object) -> str:
    value = _attr_or_item(request, "tool_name", "toolName")
    return str(value or "unknown-tool")


def _request_turn_id(request: object) -> str:
    context = _attr_or_item(request, "context")
    value = _attr_or_item(context, "turn_id", "turnId") if context is not None else None
    return str(value or "unknown-turn")


def _request_tool_call_id(request: object) -> str | None:
    value = _attr_or_item(request, "tool_call_id", "toolCallId")
    return str(value) if value is not None else None


def _request_arguments(request: object) -> Mapping[str, object]:
    value = _attr_or_item(request, "arguments")
    return value if isinstance(value, Mapping) else {}


def _request_execution_snapshot(request: object) -> dict[str, object]:
    context = _attr_or_item(request, "context")
    return {
        "checkpointRefs": list(_string_sequence(_attr_or_item(request, "checkpoint_refs", "checkpointRefs"))),
        "context": _context_snapshot(context),
        "controlRefs": list(_string_sequence(_attr_or_item(request, "control_refs", "controlRefs"))),
        "evidenceRefs": list(_string_sequence(_attr_or_item(request, "evidence_refs", "evidenceRefs"))),
        "exposedToolNames": _optional_sequence_snapshot(
            _attr_or_item(request, "exposed_tool_names", "exposedToolNames")
        ),
        "mode": str(_attr_or_item(request, "mode") or ""),
        "modelInputRefs": list(_string_sequence(_attr_or_item(request, "model_input_refs", "modelInputRefs"))),
        "toolCallId": str(_attr_or_item(request, "tool_call_id", "toolCallId") or ""),
        "validatorRefs": list(_string_sequence(_attr_or_item(request, "validator_refs", "validatorRefs"))),
    }


def _context_snapshot(context: object | None) -> dict[str, object]:
    if context is None:
        return {}
    fields = (
        ("botId", ("bot_id", "botId")),
        ("channel", ("channel",)),
        ("deadlineMs", ("deadline_ms", "deadlineMs")),
        ("locale", ("locale",)),
        ("memoryMode", ("memory_mode", "memoryMode")),
        ("permissionScope", ("permission_scope", "permissionScope")),
        ("pluginId", ("plugin_id", "pluginId")),
        ("secretScope", ("secret_scope", "secretScope")),
        ("sessionId", ("session_id", "sessionId")),
        ("sessionKey", ("session_key", "sessionKey")),
        ("spawnDepth", ("spawn_depth", "spawnDepth")),
        ("spawnWorkspace", ("spawn_workspace", "spawnWorkspace")),
        ("toolUseId", ("tool_use_id", "toolUseId")),
        ("traceId", ("trace_id", "traceId")),
        ("turnId", ("turn_id", "turnId")),
        ("userId", ("user_id", "userId")),
        ("workspaceRoot", ("workspace_root", "workspaceRoot")),
    )
    snapshot: dict[str, object] = {}
    for public_key, names in fields:
        value = _attr_or_item(context, *names)
        if value is not None:
            snapshot[public_key] = str(value)
    snapshot["filesRead"] = list(_string_sequence(_attr_or_item(context, "files_read", "filesRead")))
    return snapshot


def _string_sequence(value: object | None) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, Sequence) and not isinstance(value, str):
        return tuple(str(item) for item in value)
    return (str(value),)


def _optional_sequence_snapshot(value: object | None) -> dict[str, object]:
    return {
        "present": value is not None,
        "values": list(_string_sequence(value)),
    }


def _attr_or_item(value: object, *names: str) -> object | None:
    for name in names:
        if hasattr(value, name):
            return getattr(value, name)
        if isinstance(value, Mapping) and name in value:
            return value[name]
    return None


def _approval_metadata(value: object) -> Mapping[str, object]:
    result = _attr_or_item(value, "result")
    if result is not None:
        metadata = _attr_or_item(result, "metadata")
        return metadata if isinstance(metadata, Mapping) else {}
    metadata = _attr_or_item(value, "metadata")
    return metadata if isinstance(metadata, Mapping) else {}


def _metadata_control_request_id(
    metadata: Mapping[str, object],
    needs_approval: object,
) -> str | None:
    control_request = metadata.get("controlRequest") or metadata.get("control_request")
    if isinstance(control_request, Mapping):
        for key in ("requestRef", "requestId", "request_id", "id"):
            value = control_request.get(key)
            if isinstance(value, str) and value.strip():
                return value
    approval_gate = _attr_or_item(needs_approval, "approval_gate", "approvalGate")
    if approval_gate is not None:
        value = _attr_or_item(approval_gate, "request_id", "requestId")
        if isinstance(value, str) and value.strip():
            return value
        refs = _attr_or_item(approval_gate, "control_refs", "controlRefs")
        if isinstance(refs, Sequence) and not isinstance(refs, str) and refs:
            first = refs[0]
            if isinstance(first, str) and first.strip():
                return first
    return None


def _metadata_risk_level(metadata: Mapping[str, object]) -> ApprovalRiskLevel:
    explicit = metadata.get("riskLevel") or metadata.get("risk_level") or metadata.get("risk")
    if isinstance(explicit, str) and explicit in {"unknown", "low", "medium", "high", "critical"}:
        return explicit  # type: ignore[return-value]
    if metadata.get("dangerous") is True:
        return "high"
    if metadata.get("permissionClass") in {"execute", "net"}:
        return "high"
    if metadata.get("mutatesWorkspace") is True or metadata.get("permissionClass") == "write":
        return "medium"
    return "unknown"


def _extract_command(arguments: Mapping[str, object]) -> str | None:
    for key, value in _walk_mapping(arguments):
        if _compact_key(key) in _COMMAND_KEYS and isinstance(value, str):
            return value
    return None


def _extract_target_path_refs(arguments: Mapping[str, object]) -> tuple[str, ...]:
    refs: list[str] = []
    for key, value in _walk_mapping(arguments):
        if _compact_key(key) not in _PATH_KEYS:
            continue
        if isinstance(value, str):
            refs.append(_safe_ref(value, prefix="target"))
        elif isinstance(value, Sequence) and not isinstance(value, str):
            refs.extend(_safe_ref(str(item), prefix="target") for item in value)
    return tuple(dict.fromkeys(refs))


def _walk_mapping(value: Mapping[str, object]) -> tuple[tuple[str, object], ...]:
    found: list[tuple[str, object]] = []
    for key, nested in value.items():
        key_text = str(key)
        found.append((key_text, nested))
        if isinstance(nested, Mapping):
            found.extend(_walk_mapping(nested))
    return tuple(found)


def _compact_key(value: str) -> str:
    separated = _CAMEL_BOUNDARY_RE.sub("_", value)
    return _NON_ALNUM_RE.sub("", separated).lower()


def _blocked_approval_decision(
    *,
    control_request_id: str,
    request_digest: str,
    now: int | float,
    reason_code: str,
) -> ApprovalDecision:
    return ApprovalDecision(
        status="blocked",
        decision=None,
        approvalDecisionRef=_digest_ref(
            _digest_value(
                {
                    "controlRequestId": control_request_id,
                    "requestDigest": request_digest,
                    "reasonCode": reason_code,
                }
            ),
            prefix="approval",
        ),
        controlRequestId=control_request_id,
        requestDigest=request_digest,
        reasonCodes=(reason_code,),
        decidedAt=now,
    )


def _blocked_resume_decision(
    *,
    control_request_id: str,
    request_digest: str,
    reason_code: str,
    token_record: _ResumeTokenRecord | None = None,
) -> ResumeDecision:
    return ResumeDecision(
        status="blocked",
        controlRequestId=control_request_id,
        requestDigest=request_digest,
        reasonCodes=(reason_code,),
        pendingToolCallRef=token_record.pending_tool_call_ref if token_record else None,
        approvalDecisionRef=token_record.approval_decision_ref if token_record else None,
        resumeTokenDigest=token_record.token_digest if token_record else None,
        resumeTokenRef=token_record.token_ref if token_record else None,
    )


def _resume_token(*, request_digest: str, control_request_id: str, seq: int) -> str:
    _ = request_digest, control_request_id, seq
    return f"resume-token:{secrets.token_urlsafe(32)}"


def _dedupe_refs(refs: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(_safe_ref(ref, prefix="ref") for ref in refs if ref))


def _safe_optional_ref(value: str | None, *, prefix: str) -> str | None:
    if value is None:
        return None
    return _safe_ref(value, prefix=prefix)


def _safe_ref(value: str, *, prefix: str) -> str:
    clean = _sanitize_public_text(value)
    if clean == value and _PUBLIC_REF_RE.fullmatch(clean) and not _PRIVATE_TEXT_RE.search(value):
        return clean
    return _digest_ref(_digest_text(value), prefix=prefix)


def _digest_ref(digest_or_value: str, *, prefix: str) -> str:
    material = digest_or_value
    if _DIGEST_RE.fullmatch(digest_or_value):
        material = digest_or_value.removeprefix("sha256:")
    return f"{prefix}:{hashlib.sha1(material.encode('utf-8')).hexdigest()[:16]}"


def _safe_digest(value: str) -> str:
    return value if _DIGEST_RE.fullmatch(value) else _digest_text(value)


def _digest_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _digest_value(value: object) -> str:
    return _digest_text(_canonical_json(value))


def _canonical_json(value: object) -> str:
    return json.dumps(_json_safe(value), sort_keys=True, separators=(",", ":"))


def _json_safe(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _json_safe(nested)
            for key, nested in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, Sequence) and not isinstance(value, str):
        return [_json_safe(item) for item in value]
    if isinstance(value, bool | int | float) or value is None or isinstance(value, str):
        return value
    return repr(value)


def _sanitize_reason(value: str) -> str:
    return _sanitize_public_text(value) or "[redacted-reason]"


def _sanitize_public_text(value: str) -> str:
    if _SENSITIVE_KEY_RE.search(value):
        value = _SENSITIVE_KEY_RE.sub("[redacted-private]", value)
    value = _PRIVATE_TEXT_RE.sub("[redacted-private]", value)
    return value[:240]
