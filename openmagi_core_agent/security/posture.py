from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


ControlKind = Literal[
    "approval",
    "redaction",
    "scanner",
    "tool_allowlist",
    "terminal_backend_isolation",
    "whole_process_isolation",
    "network_policy",
    "credential_broker",
    "external_allowlist",
]
BoundaryClass = Literal[
    "heuristic",
    "no_os_boundary",
    "terminal_backend_only",
    "whole_process_boundary",
]

_HEURISTIC_KINDS = {"approval", "redaction", "scanner", "tool_allowlist"}
_WHOLE_PROCESS_BOUNDARY_KINDS = {
    "whole_process_isolation",
    "network_policy",
    "credential_broker",
    "external_allowlist",
}
_CONTROL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")
_REASON_CODE_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,79}$")
_CREDENTIAL_SHAPE_RES = (
    re.compile(r"^sk_(?:live|test|proj)_[a-z0-9_]{16,}$"),
    re.compile(r"^gh[pousr]_[a-z0-9_]{20,}$"),
    re.compile(r"^xox[baprs]-[a-z0-9-]{20,}$"),
    re.compile(r"^akia[a-z0-9]{12,}$"),
    re.compile(r"^api[_-]?key[_-][a-z0-9_]{12,}$"),
)


class SecurityControl(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    name: str
    kind: ControlKind
    enforced: bool = False

    @field_validator("name")
    @classmethod
    def _safe_name(cls, value: str) -> str:
        normalized = "-".join(value.strip().lower().split())
        if not normalized:
            raise ValueError("security control name is required")
        if not _CONTROL_NAME_RE.fullmatch(normalized):
            raise ValueError("security control name must be public-safe")
        return normalized

    def boundary_class(self) -> BoundaryClass:
        kind = _safe_control_kind(self.kind)
        if kind in _HEURISTIC_KINDS:
            return "heuristic"
        if kind == "terminal_backend_isolation":
            return "terminal_backend_only"
        if kind in _WHOLE_PROCESS_BOUNDARY_KINDS:
            return "whole_process_boundary"
        return "heuristic"


class SecurityPostureRequest(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    controls: tuple[SecurityControl, ...] = ()
    untrusted_inputs: tuple[str, ...] = Field(default=(), alias="untrustedInputs")
    plugin_loading_enabled: bool = Field(default=False, alias="pluginLoadingEnabled")
    mcp_enabled: bool = Field(default=False, alias="mcpEnabled")


class SecurityPostureDecision(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    boundary_class: BoundaryClass = Field(alias="boundaryClass")
    production_ready: bool = Field(alias="productionReady")
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")
    controls: tuple[SecurityControl, ...] = ()

    def public_projection(self) -> dict[str, object]:
        return {
            "boundaryClass": _public_boundary_class(self.boundary_class),
            "productionReady": self.production_ready is True,
            "reasonCodes": _public_reason_codes(self.reason_codes),
            "controls": [
                {
                    "name": _public_control_name(control.name),
                    "kind": _public_control_kind(control.kind),
                    "enforced": control.enforced is True,
                    "boundaryClass": control.boundary_class(),
                }
                for control in self.controls
            ],
        }


def evaluate_security_posture(request: SecurityPostureRequest) -> SecurityPostureDecision:
    enforced = tuple(control for control in request.controls if control.enforced is True)
    kinds = {control.kind for control in enforced}
    reasons: list[str] = []

    if any(control.kind in _HEURISTIC_KINDS for control in request.controls):
        reasons.append("in_process_controls_are_heuristics")

    whole_process = "whole_process_isolation" in kinds
    terminal_backend = "terminal_backend_isolation" in kinds

    if whole_process:
        missing: list[str] = []
        if "network_policy" not in kinds:
            missing.append("network_policy_missing")
        if "credential_broker" not in kinds:
            missing.append("credential_broker_missing")
        if "external_allowlist" not in kinds:
            missing.append("external_allowlist_missing")
        if missing:
            return SecurityPostureDecision(
                boundaryClass="whole_process_boundary",
                productionReady=False,
                reasonCodes=tuple(dict.fromkeys((*reasons, *missing))),
                controls=request.controls,
            )
        return SecurityPostureDecision(
            boundaryClass="whole_process_boundary",
            productionReady=True,
            reasonCodes=("whole_process_boundary_ready",),
            controls=request.controls,
        )

    if terminal_backend:
        reasons.append("terminal_backend_does_not_confine_agent_process")
        if request.plugin_loading_enabled or request.mcp_enabled:
            reasons.append("whole_process_required_for_plugins_or_mcp")
        if request.untrusted_inputs:
            reasons.append("untrusted_inputs_require_whole_process_isolation")
        return SecurityPostureDecision(
            boundaryClass="terminal_backend_only",
            productionReady=False,
            reasonCodes=tuple(dict.fromkeys(reasons)),
            controls=request.controls,
        )

    if request.untrusted_inputs:
        reasons.append("untrusted_inputs_require_whole_process_isolation")
    reasons.append("no_os_level_isolation")
    return SecurityPostureDecision(
        boundaryClass="no_os_boundary",
        productionReady=False,
        reasonCodes=tuple(dict.fromkeys(reasons)),
        controls=request.controls,
    )


def _public_control_name(name: object) -> str:
    value = str(name).strip().lower()
    normalized = "-".join(value.split())
    if _CONTROL_NAME_RE.fullmatch(normalized) and not _looks_sensitive(normalized):
        return normalized
    return "redacted"


def _public_boundary_class(boundary_class: object) -> str:
    value = str(boundary_class)
    if value in {
        "heuristic",
        "no_os_boundary",
        "terminal_backend_only",
        "whole_process_boundary",
    }:
        return value
    return "invalid"


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


def _public_control_kind(kind: object) -> str:
    value = _safe_control_kind(kind)
    if value is None:
        return "unknown"
    return value


def _safe_control_kind(kind: object) -> ControlKind | None:
    value = str(kind)
    if value in _HEURISTIC_KINDS or value in _WHOLE_PROCESS_BOUNDARY_KINDS:
        return value  # type: ignore[return-value]
    if value == "terminal_backend_isolation":
        return value
    return None


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
    return any(fragment in value for fragment in sensitive_fragments) or any(
        pattern.fullmatch(value) for pattern in _CREDENTIAL_SHAPE_RES
    )
