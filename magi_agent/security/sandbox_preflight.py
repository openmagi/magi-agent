from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt


Backend = Literal[
    "local",
    "ssh",
    "docker_terminal",
    "whole_process_container",
    "cloud_microvm",
]
Mode = Literal["cli", "gateway", "worker"]
BoundaryClass = Literal[
    "no_os_boundary",
    "terminal_backend_only",
    "whole_process_boundary",
]

_TERMINAL_BACKENDS = {"ssh", "docker_terminal"}
_WHOLE_PROCESS_BACKENDS = {"whole_process_container", "cloud_microvm"}
_ALLOWED_RESOURCE_LIMITS = ("cpu", "memoryMb", "pids")
_ALLOWED_MOUNT_PREFIXES = (
    "/tmp/openmagi-artifacts",
    "/workspace",
)
_PRIVATE_MOUNT_PREFIXES = (
    "/dev",
    "/etc",
    "/home",
    "/proc",
    "/private",
    "/root",
    "/run/secrets",
    "/sys",
    "/Users",
    "/var/lib",
    "/var/run",
    "/Volumes",
)
_PUBLIC_REASON_CODES = {
    "cpu_limit_required",
    "invalid_backend",
    "invalid_mode",
    "local_backend_has_no_os_isolation",
    "memory_limit_required",
    "network_default_deny_required",
    "pids_limit_required",
    "private_mount_denied",
    "root_mount_denied",
    "sandbox_must_run_non_root",
    "sandbox_preflight_ready",
    "terminal_backend_does_not_confine_agent_process",
    "unapproved_mount_path",
    "unsupported_resource_limit_key",
    "untrusted_inputs_require_whole_process_isolation",
    "whole_process_required_for_plugins_or_mcp",
}
_SENSITIVE_REASON_FRAGMENTS = (
    "api_key",
    "auth",
    "cookie",
    "credential",
    "private",
    "secret",
    "session",
    "token",
)


class SandboxPreflightRequest(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    backend: Backend
    mode: Mode
    untrusted_inputs: tuple[str, ...] = Field(default=(), alias="untrustedInputs")
    plugin_loading_enabled: StrictBool = Field(default=False, alias="pluginLoadingEnabled")
    mcp_enabled: StrictBool = Field(default=False, alias="mcpEnabled")
    non_root: StrictBool = Field(default=False, alias="nonRoot")
    network_default_deny: StrictBool = Field(default=False, alias="networkDefaultDeny")
    mounted_paths: tuple[str, ...] = Field(default=(), alias="mountedPaths")
    resource_limits: dict[str, StrictInt] = Field(
        default_factory=dict,
        alias="resourceLimits",
    )


class SandboxPreflightReport(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    ready: bool
    boundary_class: BoundaryClass = Field(alias="boundaryClass")
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")
    request: SandboxPreflightRequest

    def public_projection(self) -> dict[str, object]:
        boundary_class = _public_boundary_class(self.boundary_class)
        reason_codes = _public_reason_codes(self.reason_codes)
        backend = _public_backend(getattr(self.request, "backend", "unknown"))
        mode = _public_mode(getattr(self.request, "mode", "unknown"))
        resource_limits = _public_resource_limits(
            getattr(self.request, "resource_limits", {}),
        )
        ready = (
            self.ready is True
            and boundary_class == "whole_process_boundary"
            and bool(reason_codes)
            and "redacted" not in reason_codes
            and reason_codes == ["sandbox_preflight_ready"]
            and backend in _WHOLE_PROCESS_BACKENDS
            and mode != "unknown"
            and _request_has_ready_prerequisites(self.request, resource_limits)
        )
        return {
            "ready": ready,
            "boundaryClass": boundary_class,
            "reasonCodes": reason_codes,
            "backend": backend,
            "mode": mode,
            "resourceLimits": resource_limits,
        }


def evaluate_sandbox_preflight(
    request: SandboxPreflightRequest,
) -> SandboxPreflightReport:
    backend = _safe_backend(request.backend)
    if backend is None:
        return SandboxPreflightReport(
            ready=False,
            boundaryClass="no_os_boundary",
            reasonCodes=("invalid_backend",),
            request=request,
        )
    mode = _safe_mode(request.mode)
    if mode is None:
        return SandboxPreflightReport(
            ready=False,
            boundaryClass="no_os_boundary",
            reasonCodes=("invalid_mode",),
            request=request,
        )
    if backend == "local":
        reasons = ["local_backend_has_no_os_isolation"]
        if request.untrusted_inputs:
            reasons.append("untrusted_inputs_require_whole_process_isolation")
        return SandboxPreflightReport(
            ready=False,
            boundaryClass="no_os_boundary",
            reasonCodes=tuple(reasons),
            request=request,
        )

    if backend in _TERMINAL_BACKENDS:
        reasons = ["terminal_backend_does_not_confine_agent_process"]
        if request.plugin_loading_enabled or request.mcp_enabled:
            reasons.append("whole_process_required_for_plugins_or_mcp")
        if request.untrusted_inputs:
            reasons.append("untrusted_inputs_require_whole_process_isolation")
        return SandboxPreflightReport(
            ready=False,
            boundaryClass="terminal_backend_only",
            reasonCodes=tuple(dict.fromkeys(reasons)),
            request=request,
        )

    if backend not in _WHOLE_PROCESS_BACKENDS:
        return SandboxPreflightReport(
            ready=False,
            boundaryClass="no_os_boundary",
            reasonCodes=("invalid_backend",),
            request=request,
        )

    reasons: list[str] = []
    if request.non_root is not True:
        reasons.append("sandbox_must_run_non_root")
    if request.network_default_deny is not True:
        reasons.append("network_default_deny_required")
    if _has_root_mount(request.mounted_paths):
        reasons.append("root_mount_denied")
    if _has_private_mount(request.mounted_paths):
        reasons.append("private_mount_denied")
    if _has_unapproved_mount(request.mounted_paths):
        reasons.append("unapproved_mount_path")
    if _unsupported_resource_limit_keys(request.resource_limits):
        reasons.append("unsupported_resource_limit_key")
    if _positive_int_limit(request.resource_limits, "cpu") is None:
        reasons.append("cpu_limit_required")
    if _positive_int_limit(request.resource_limits, "memoryMb") is None:
        reasons.append("memory_limit_required")
    if _positive_int_limit(request.resource_limits, "pids") is None:
        reasons.append("pids_limit_required")

    if reasons:
        return SandboxPreflightReport(
            ready=False,
            boundaryClass="whole_process_boundary",
            reasonCodes=tuple(reasons),
            request=request,
        )

    return SandboxPreflightReport(
        ready=True,
        boundaryClass="whole_process_boundary",
        reasonCodes=("sandbox_preflight_ready",),
        request=request,
    )


def _public_resource_limits(resource_limits: object) -> dict[str, int]:
    if not isinstance(resource_limits, dict):
        return {}
    public: dict[str, int] = {}
    for key in _ALLOWED_RESOURCE_LIMITS:
        value = _positive_int_limit(resource_limits, key)
        if value is not None:
            public[key] = value
    return public


def _positive_int_limit(resource_limits: object, key: str) -> int | None:
    if not isinstance(resource_limits, dict):
        return None
    value = resource_limits.get(key)
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def _unsupported_resource_limit_keys(resource_limits: object) -> bool:
    if not isinstance(resource_limits, dict):
        return True
    return any(key not in _ALLOWED_RESOURCE_LIMITS for key in resource_limits)


def _request_has_ready_prerequisites(
    request: SandboxPreflightRequest,
    resource_limits: dict[str, int],
) -> bool:
    return (
        getattr(request, "non_root", False) is True
        and getattr(request, "network_default_deny", False) is True
        and not _has_root_mount(getattr(request, "mounted_paths", ()))
        and not _has_private_mount(getattr(request, "mounted_paths", ()))
        and not _has_unapproved_mount(getattr(request, "mounted_paths", ()))
        and not _unsupported_resource_limit_keys(
            getattr(request, "resource_limits", {}),
        )
        and all(key in resource_limits for key in _ALLOWED_RESOURCE_LIMITS)
    )


def _has_root_mount(mounted_paths: object) -> bool:
    if not isinstance(mounted_paths, tuple):
        return True
    return any(_normalize_mount_path(mounted_path) == "/" for mounted_path in mounted_paths)


def _has_private_mount(mounted_paths: object) -> bool:
    if not isinstance(mounted_paths, tuple):
        return True
    for mounted_path in mounted_paths:
        value = _normalize_mount_path(mounted_path)
        if value is None:
            return True
        comparable = value.casefold()
        if any(
            comparable == private_prefix.casefold()
            or comparable.startswith(f"{private_prefix.casefold()}/")
            for private_prefix in _PRIVATE_MOUNT_PREFIXES
        ):
            return True
    return False


def _has_unapproved_mount(mounted_paths: object) -> bool:
    if not isinstance(mounted_paths, tuple):
        return True
    for mounted_path in mounted_paths:
        value = _normalize_mount_path(mounted_path)
        if value is None:
            return True
        if value == "/" or _mount_is_private(value):
            continue
        if not any(
            value == allowed_prefix or value.startswith(f"{allowed_prefix}/")
            for allowed_prefix in _ALLOWED_MOUNT_PREFIXES
        ):
            return True
    return False


def _mount_is_private(mounted_path: str) -> bool:
    comparable = mounted_path.casefold()
    return any(
        comparable == private_prefix.casefold()
        or comparable.startswith(f"{private_prefix.casefold()}/")
        for private_prefix in _PRIVATE_MOUNT_PREFIXES
    )


def _normalize_mount_path(mounted_path: object) -> str | None:
    value = str(mounted_path).strip()
    if not value.startswith("/") or "\x00" in value:
        return None
    if ".." in value.split("/"):
        return None
    normalized = re.sub(r"/+", "/", value).rstrip("/")
    if not normalized:
        return "/"
    return normalized


def _public_reason_codes(reason_codes: object) -> list[str]:
    if not isinstance(reason_codes, tuple):
        return ["redacted"]
    public: list[str] = []
    for reason_code in reason_codes:
        value = str(reason_code)
        if value in _PUBLIC_REASON_CODES and not _looks_sensitive(value):
            public.append(value)
        else:
            public.append("redacted")
    return list(dict.fromkeys(public))


def _public_backend(backend: object) -> str:
    value = _safe_backend(backend)
    if value is not None:
        return value
    return "unknown"


def _public_mode(mode: object) -> str:
    value = _safe_mode(mode)
    if value is not None:
        return value
    return "unknown"


def _public_boundary_class(boundary_class: object) -> str:
    value = str(boundary_class)
    if value in {
        "no_os_boundary",
        "terminal_backend_only",
        "whole_process_boundary",
    }:
        return value
    return "unknown"


def _safe_backend(backend: object) -> Backend | None:
    value = str(backend)
    if value in {"local", "ssh", "docker_terminal", "whole_process_container", "cloud_microvm"}:
        return value  # type: ignore[return-value]
    return None


def _safe_mode(mode: object) -> Mode | None:
    value = str(mode)
    if value in {"cli", "gateway", "worker"}:
        return value  # type: ignore[return-value]
    return None


def _looks_sensitive(value: str) -> bool:
    return any(fragment in value for fragment in _SENSITIVE_REASON_FRAGMENTS)
