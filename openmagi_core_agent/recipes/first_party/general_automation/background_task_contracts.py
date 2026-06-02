from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
import json
import re
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator


BackgroundTaskResumeIntent = Literal["resume", "status"]
BackgroundTaskResumeStatus = Literal[
    "approval_required",
    "approval_recorded",
    "metadata_recorded",
]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,220}$")


class BackgroundTaskResumeAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    long_running_function_tool_attached: Literal[False] = Field(
        default=False,
        alias="longRunningFunctionToolAttached",
    )
    session_service_attached: Literal[False] = Field(
        default=False,
        alias="sessionServiceAttached",
    )
    background_runner_invoked: Literal[False] = Field(
        default=False,
        alias="backgroundRunnerInvoked",
    )
    workspace_mutated: Literal[False] = Field(default=False, alias="workspaceMutated")
    channel_delivery_performed: Literal[False] = Field(
        default=False,
        alias="channelDeliveryPerformed",
    )
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set, values
        return cls()

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = update, deep
        return type(self)()


class BackgroundTaskResumeRequest(BaseModel):
    model_config = _MODEL_CONFIG

    session_ref: str = Field(alias="sessionRef")
    task_ref: str = Field(alias="taskRef")
    checkpoint_ref: str = Field(alias="checkpointRef")
    resume_intent: BackgroundTaskResumeIntent = Field(alias="resumeIntent")
    approval_ref: str | None = Field(default=None, alias="approvalRef")

    @field_validator("session_ref", "task_ref", "checkpoint_ref", "approval_ref")
    @classmethod
    def _validate_ref(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _safe_ref(value)


class BackgroundTaskResumeDecision(BaseModel):
    model_config = _MODEL_CONFIG

    status: BackgroundTaskResumeStatus
    session_ref: str = Field(alias="sessionRef")
    task_ref: str = Field(alias="taskRef")
    checkpoint_ref: str = Field(alias="checkpointRef")
    resume_intent: BackgroundTaskResumeIntent = Field(alias="resumeIntent")
    resume_ref: str = Field(alias="resumeRef")
    approval_ref: str | None = Field(default=None, alias="approvalRef")
    execution_allowed: Literal[False] = Field(default=False, alias="executionAllowed")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    adk_tool: Mapping[str, object] = Field(alias="adkTool")
    authority_flags: BackgroundTaskResumeAuthorityFlags = Field(
        default_factory=BackgroundTaskResumeAuthorityFlags,
        alias="authorityFlags",
    )

    @field_validator("session_ref", "task_ref", "checkpoint_ref", "resume_ref", "approval_ref")
    @classmethod
    def _validate_ref(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _safe_ref(value)

    def public_projection(self) -> dict[str, object]:
        return {
            "status": self.status,
            "sessionRef": self.session_ref,
            "taskRef": self.task_ref,
            "checkpointRef": self.checkpoint_ref,
            "resumeIntent": self.resume_intent,
            "resumeRef": self.resume_ref,
            "approvalRef": self.approval_ref,
            "executionAllowed": self.execution_allowed,
            "reasonCodes": self.reason_codes,
            "adkTool": dict(self.adk_tool),
            "adkBoundary": {
                "longRunningFunctionTool": "LongRunningFunctionTool",
                "functionToolName": "BackgroundTaskResume",
                "sessionService": "SessionService",
                "sessionRefsOnly": True,
            },
            "authorityFlags": self.authority_flags.model_dump(
                by_alias=True,
                mode="json",
            ),
        }


def classify_background_task_resume_request(
    request: BackgroundTaskResumeRequest,
) -> BackgroundTaskResumeDecision:
    resume_ref = _resume_ref(request)
    if request.resume_intent == "resume" and request.approval_ref is None:
        return _decision(
            "approval_required",
            request=request,
            resume_ref=resume_ref,
            reason_codes=("background_task_resume_approval_required",),
        )
    if request.resume_intent == "resume":
        return _decision(
            "approval_recorded",
            request=request,
            resume_ref=resume_ref,
            reason_codes=("background_task_resume_approval_recorded",),
        )
    return _decision(
        "metadata_recorded",
        request=request,
        resume_ref=resume_ref,
        reason_codes=("background_task_session_status_metadata_only",),
    )


def background_task_long_running_tool_metadata() -> dict[str, object]:
    return {
        "name": "BackgroundTaskResume",
        "adkToolType": "LongRunningFunctionTool",
        "enabledByDefault": False,
        "handlerAttached": False,
        "longRunningFunctionToolAttached": False,
        "sessionServiceAttached": False,
        "backgroundRunnerAttached": False,
        "description": "Represent resumable background task metadata without runner dispatch.",
        "inputSchema": {
            "type": "object",
            "required": [
                "sessionRef",
                "taskRef",
                "checkpointRef",
                "resumeIntent",
            ],
            "additionalProperties": False,
            "properties": {
                "sessionRef": {"type": "string"},
                "taskRef": {"type": "string"},
                "checkpointRef": {"type": "string"},
                "resumeIntent": {"type": "string", "enum": ["resume", "status"]},
                "approvalRef": {"type": "string"},
            },
        },
    }


def _decision(
    status: BackgroundTaskResumeStatus,
    *,
    request: BackgroundTaskResumeRequest,
    resume_ref: str,
    reason_codes: tuple[str, ...],
) -> BackgroundTaskResumeDecision:
    return BackgroundTaskResumeDecision(
        status=status,
        sessionRef=request.session_ref,
        taskRef=request.task_ref,
        checkpointRef=request.checkpoint_ref,
        resumeIntent=request.resume_intent,
        resumeRef=resume_ref,
        approvalRef=request.approval_ref,
        reasonCodes=reason_codes,
        adkTool=background_task_long_running_tool_metadata(),
    )


def _resume_ref(request: BackgroundTaskResumeRequest) -> str:
    return "resume:background-task:" + _digest(
        {
            "sessionRef": request.session_ref,
            "taskRef": request.task_ref,
            "checkpointRef": request.checkpoint_ref,
            "resumeIntent": request.resume_intent,
            "approvalRef": request.approval_ref,
        }
    )


def _safe_ref(value: str) -> str:
    if not value or not _REF_RE.fullmatch(value):
        raise ValueError("ref must be a safe public reference")
    return value


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=repr,
    ).encode("utf-8")
    return f"sha256:{sha256(encoded).hexdigest()}"


__all__ = [
    "BackgroundTaskResumeAuthorityFlags",
    "BackgroundTaskResumeDecision",
    "BackgroundTaskResumeRequest",
    "background_task_long_running_tool_metadata",
    "classify_background_task_resume_request",
]
