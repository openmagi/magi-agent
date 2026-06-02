from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


Transport = Literal[
    "messaging",
    "http",
    "plugin_http",
    "webhook",
    "editor",
    "local_http",
    "local_ipc",
]
Action = Literal["dispatch_work", "resolve_approval", "relay_output"]

_NETWORK_TRANSPORTS = {"messaging", "http", "plugin_http", "webhook"}
_LOCAL_TRANSPORTS = {"editor", "local_http", "local_ipc"}
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
_SURFACE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")
_REASON_CODE_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,79}$")
_CREDENTIAL_SHAPE_RES = (
    re.compile(r"\b" + "sk" + r"-[a-z0-9_-]{8,}\b", re.IGNORECASE),
    re.compile(r"\b" + "gh" + r"p_[a-z0-9_]{12,}\b", re.IGNORECASE),
    re.compile(r"\bxox[baprs]-[a-z0-9-]{8,}\b", re.IGNORECASE),
)


class ExternalSurfaceRequest(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    surface: str
    transport: Transport
    caller_id: str = Field(alias="callerId")
    action: Action
    session_id: str | None = Field(default=None, alias="sessionId")
    bind_host: str = Field(default="127.0.0.1", alias="bindHost")

    @field_validator("surface", "caller_id", "bind_host")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("external surface refs must be non-empty")
        return normalized


class ExternalSurfacePolicy(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    enabled: bool = False
    allowed_callers: tuple[str, ...] = Field(default=(), alias="allowedCallers")
    allowed_actions: tuple[Action, ...] = Field(
        default=("dispatch_work", "resolve_approval", "relay_output"),
        alias="allowedActions",
    )
    local_os_boundary: bool = Field(default=False, alias="localOsBoundary")


class ExternalSurfaceDecision(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    allowed: bool
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")
    request: ExternalSurfaceRequest

    def public_projection(self) -> dict[str, object]:
        surface = _public_surface(self.request.surface)
        transport = _public_transport(self.request.transport)
        action = _public_action(self.request.action)
        reason_codes = _public_reason_codes(self.reason_codes)
        allowed = (
            self.allowed is True
            and surface != "redacted"
            and transport != "unknown"
            and action != "unknown"
            and bool(reason_codes)
            and "redacted" not in reason_codes
        )
        return {
            "surface": surface,
            "transport": transport,
            "action": action,
            "allowed": allowed,
            "reasonCodes": reason_codes,
            "sessionIdAuthorized": False,
        }


def evaluate_external_surface(
    request: ExternalSurfaceRequest,
    policy: ExternalSurfacePolicy,
) -> ExternalSurfaceDecision:
    if policy.enabled is not True:
        return ExternalSurfaceDecision(
            allowed=False,
            reasonCodes=("external_surface_policy_disabled",),
            request=request,
        )
    if _public_surface(request.surface) == "redacted":
        return ExternalSurfaceDecision(
            allowed=False,
            reasonCodes=("invalid_surface",),
            request=request,
        )
    transport = _safe_transport(request.transport)
    if transport is None:
        return ExternalSurfaceDecision(
            allowed=False,
            reasonCodes=("invalid_transport",),
            request=request,
        )
    action = _safe_action(request.action)
    if action is None:
        return ExternalSurfaceDecision(
            allowed=False,
            reasonCodes=("invalid_action",),
            request=request,
        )
    allowed_actions = {
        allowed_action
        for raw_action in policy.allowed_actions
        if (allowed_action := _safe_action(raw_action)) is not None
    }
    if action not in allowed_actions:
        return ExternalSurfaceDecision(
            allowed=False,
            reasonCodes=("action_not_allowed_for_surface",),
            request=request,
        )
    allowed_callers = policy.allowed_callers if isinstance(policy.allowed_callers, tuple) else ()
    if transport in _NETWORK_TRANSPORTS and not allowed_callers:
        return ExternalSurfaceDecision(
            allowed=False,
            reasonCodes=("network_surface_requires_allowlist",),
            request=request,
        )
    if transport in _NETWORK_TRANSPORTS and request.caller_id not in set(allowed_callers):
        reasons = ["caller_not_allowlisted"]
        if request.session_id:
            reasons.append("session_id_is_not_authorization")
        return ExternalSurfaceDecision(
            allowed=False,
            reasonCodes=tuple(reasons),
            request=request,
        )
    if transport in _LOCAL_TRANSPORTS:
        if str(request.bind_host).casefold() not in _LOOPBACK_HOSTS:
            return ExternalSurfaceDecision(
                allowed=False,
                reasonCodes=("local_surface_bound_to_non_loopback",),
                request=request,
            )
        if policy.local_os_boundary is not True:
            return ExternalSurfaceDecision(
                allowed=False,
                reasonCodes=("local_surface_requires_os_boundary",),
                request=request,
            )
    return ExternalSurfaceDecision(
        allowed=True,
        reasonCodes=("caller_allowlisted",),
        request=request,
    )


def _public_surface(surface: object) -> str:
    value = str(surface).strip().lower()
    normalized = "-".join(value.split())
    if _SURFACE_RE.fullmatch(normalized) and not _looks_sensitive(normalized):
        return normalized
    return "redacted"


def _public_transport(transport: object) -> str:
    value = _safe_transport(transport)
    if value is not None:
        return value
    return "unknown"


def _public_action(action: object) -> str:
    value = _safe_action(action)
    if value is not None:
        return value
    return "unknown"


def _public_reason_codes(reason_codes: object) -> list[str]:
    if not isinstance(reason_codes, tuple):
        return ["redacted"]
    public: list[str] = []
    for reason_code in reason_codes:
        value = str(reason_code)
        if _REASON_CODE_RE.fullmatch(value) and not _looks_sensitive(value):
            public.append(value)
        else:
            public.append("redacted")
    return list(dict.fromkeys(public))


def _looks_sensitive(value: str) -> bool:
    sensitive_fragments = (
        "api-key",
        "apikey",
        "auth",
        "bearer",
        "cookie",
        "credential",
        "private",
        "secret",
        "session",
        "sk" + "-",
        "token",
    )
    if any(fragment in value for fragment in sensitive_fragments):
        return True
    return any(pattern.search(value) for pattern in _CREDENTIAL_SHAPE_RES)


def _safe_transport(transport: object) -> Transport | None:
    value = str(transport)
    if value in _NETWORK_TRANSPORTS or value in _LOCAL_TRANSPORTS:
        return value  # type: ignore[return-value]
    return None


def _safe_action(action: object) -> Action | None:
    value = str(action)
    if value in {"dispatch_work", "resolve_approval", "relay_output"}:
        return value  # type: ignore[return-value]
    return None
