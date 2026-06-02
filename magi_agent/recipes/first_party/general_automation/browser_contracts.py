from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator


BrowserMode = Literal["inspect", "act"]
BrowserAction = Literal[
    "open",
    "snapshot",
    "scrape",
    "click",
    "fill",
    "download",
    "submit",
]
BrowserBoundaryStatus = Literal["allowed", "blocked", "approval_required", "approved"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_INSPECT_ACTIONS: frozenset[BrowserAction] = frozenset({"open", "snapshot", "scrape"})
_APPROVAL_ACTIONS: frozenset[BrowserAction] = frozenset(
    {"click", "fill", "download", "submit"}
)
_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,180}$")


class BrowserBoundaryAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    browser_worker_session_started: Literal[False] = Field(
        default=False,
        alias="browserWorkerSessionStarted",
    )
    browser_action_performed: Literal[False] = Field(
        default=False,
        alias="browserActionPerformed",
    )
    external_form_submitted: Literal[False] = Field(
        default=False,
        alias="externalFormSubmitted",
    )
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


class BrowserBoundaryRequest(BaseModel):
    model_config = _MODEL_CONFIG

    mode: BrowserMode
    action: BrowserAction
    approval_ref: str | None = Field(default=None, alias="approvalRef")

    @field_validator("approval_ref")
    @classmethod
    def _validate_approval_ref(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _safe_ref(value)


class BrowserBoundaryDecision(BaseModel):
    model_config = _MODEL_CONFIG

    status: BrowserBoundaryStatus
    mode: BrowserMode
    action: BrowserAction
    approval_ref: str | None = Field(default=None, alias="approvalRef")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    adk_tool: Mapping[str, object] = Field(alias="adkTool")
    authority_flags: BrowserBoundaryAuthorityFlags = Field(
        default_factory=BrowserBoundaryAuthorityFlags,
        alias="authorityFlags",
    )

    @field_validator("approval_ref")
    @classmethod
    def _validate_approval_ref(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _safe_ref(value)

    def public_projection(self) -> dict[str, object]:
        return {
            "status": self.status,
            "mode": self.mode,
            "action": self.action,
            "approvalRef": self.approval_ref,
            "reasonCodes": self.reason_codes,
            "adkTool": dict(self.adk_tool),
            "adkBoundary": {
                "functionTool": "BrowserAction",
                "functionToolHandlerAttached": False,
            },
            "authorityFlags": self.authority_flags.model_dump(
                by_alias=True,
                mode="json",
            ),
        }


def classify_browser_boundary_request(
    request: BrowserBoundaryRequest,
) -> BrowserBoundaryDecision:
    if request.mode == "inspect":
        if request.action in _INSPECT_ACTIONS:
            return _decision("allowed", request=request)
        return _decision(
            "blocked",
            request=request,
            reason_codes=("inspect_mode_action_not_allowed",),
        )

    if request.action in _APPROVAL_ACTIONS and request.approval_ref is None:
        return _decision(
            "approval_required",
            request=request,
            reason_codes=("browser_action_requires_approval",),
        )
    if request.action in _APPROVAL_ACTIONS:
        return _decision("approved", request=request)
    return _decision("allowed", request=request)


def browser_action_function_tool_metadata() -> dict[str, object]:
    return {
        "name": "BrowserAction",
        "adkToolType": "FunctionTool",
        "enabledByDefault": False,
        "handlerAttached": False,
        "browserWorkerSessionStarted": False,
        "description": "Represent browser inspect/action intent without attaching a worker.",
        "inputSchema": {
            "type": "object",
            "required": ["mode", "action"],
            "additionalProperties": False,
            "properties": {
                "mode": {"type": "string", "enum": ["inspect", "act"]},
                "action": {
                    "type": "string",
                    "enum": [
                        "open",
                        "snapshot",
                        "scrape",
                        "click",
                        "fill",
                        "download",
                        "submit",
                    ],
                },
                "approvalRef": {"type": "string"},
            },
        },
    }


def _decision(
    status: BrowserBoundaryStatus,
    *,
    request: BrowserBoundaryRequest,
    reason_codes: tuple[str, ...] = (),
) -> BrowserBoundaryDecision:
    return BrowserBoundaryDecision(
        status=status,
        mode=request.mode,
        action=request.action,
        approvalRef=request.approval_ref,
        reasonCodes=reason_codes,
        adkTool=browser_action_function_tool_metadata(),
    )


def _safe_ref(value: str) -> str:
    if not _REF_RE.fullmatch(value):
        raise ValueError("ref must be a safe public reference")
    return value


__all__ = [
    "BrowserBoundaryAuthorityFlags",
    "BrowserBoundaryDecision",
    "BrowserBoundaryRequest",
    "browser_action_function_tool_metadata",
    "classify_browser_boundary_request",
]
