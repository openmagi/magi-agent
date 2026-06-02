from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
import json
import re
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator


BackgroundTaskCompletionStatus = Literal["completed", "failed", "cancelled"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,220}$")


class BackgroundTaskProjectionAuthorityFlags(BaseModel):
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


class BackgroundTaskCompletionProjection(BaseModel):
    model_config = _MODEL_CONFIG

    completion_ref: str = Field(alias="completionRef")
    session_ref: str = Field(alias="sessionRef")
    task_ref: str = Field(alias="taskRef")
    completion_status: BackgroundTaskCompletionStatus = Field(alias="completionStatus")
    content_digest: str = Field(alias="contentDigest")
    summary_digest: str = Field(alias="summaryDigest")
    output_refs: tuple[str, ...] = Field(default=(), alias="outputRefs")
    authority_flags: BackgroundTaskProjectionAuthorityFlags = Field(
        default_factory=BackgroundTaskProjectionAuthorityFlags,
        alias="authorityFlags",
    )

    @field_validator("completion_ref", "session_ref", "task_ref")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        return _safe_ref(value)

    @field_validator("output_refs")
    @classmethod
    def _validate_output_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_safe_ref(item) for item in value)

    @field_validator("content_digest", "summary_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return _safe_digest(value)

    def public_projection(self) -> dict[str, object]:
        return {
            "completionRef": self.completion_ref,
            "sessionRef": self.session_ref,
            "taskRef": self.task_ref,
            "completionStatus": self.completion_status,
            "contentDigest": self.content_digest,
            "summaryDigest": self.summary_digest,
            "outputRefs": self.output_refs,
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


def build_background_task_completion_projection(
    *,
    sessionRef: str,
    taskRef: str,
    completionStatus: BackgroundTaskCompletionStatus,
    contentDigest: str,
    outputRefs: tuple[str, ...] = (),
    summary: str = "",
) -> BackgroundTaskCompletionProjection:
    content_digest = _safe_digest(contentDigest)
    summary_digest = _digest(summary)
    completion_material: Mapping[str, object] = {
        "sessionRef": sessionRef,
        "taskRef": taskRef,
        "completionStatus": completionStatus,
        "contentDigest": content_digest,
        "summaryDigest": summary_digest,
        "outputRefs": outputRefs,
    }
    return BackgroundTaskCompletionProjection(
        completionRef="completion:background-task:" + _digest(completion_material),
        sessionRef=sessionRef,
        taskRef=taskRef,
        completionStatus=completionStatus,
        contentDigest=content_digest,
        summaryDigest=summary_digest,
        outputRefs=outputRefs,
    )


def _safe_digest(value: str) -> str:
    if not _DIGEST_RE.fullmatch(value):
        raise ValueError("digest must be sha256")
    return value


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
    "BackgroundTaskCompletionProjection",
    "BackgroundTaskProjectionAuthorityFlags",
    "build_background_task_completion_projection",
]
