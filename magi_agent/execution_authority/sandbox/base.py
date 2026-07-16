"""Frozen sandbox profiles and fail-closed OS capability selection."""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from pathlib import PurePath
import re
from typing import Literal, Self

from pydantic import Field, ValidationInfo, field_validator, model_validator

from magi_agent.execution_authority.contracts import _AuthorityContractModel
from magi_agent.ops.safety import canonical_digest, require_digest


_ENVIRONMENT_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_SENSITIVE_PATH_PARTS = frozenset(
    {
        ".aws",
        ".git",
        ".gnupg",
        ".kube",
        ".ssh",
        "credentials",
        "secrets",
    }
)


class SandboxUnavailable(RuntimeError):
    """Raised before launch when the required OS primitive is not available."""


class SandboxInvocation(_AuthorityContractModel):
    """Pure launch description; constructing it never starts a process."""

    command: tuple[str, ...]
    profile_text: str | None = Field(default=None, alias="profileText")


class NetworkMode(StrEnum):
    NONE = "none"
    PROXY = "proxy"


class SandboxCapabilityStatus(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class SandboxBinding(_AuthorityContractModel):
    host_path: str = Field(alias="hostPath", min_length=1)
    mount_path: str = Field(alias="mountPath", min_length=1)

    @field_validator("host_path", "mount_path", mode="before")
    @classmethod
    def _require_exact_absolute_path(cls, value: object, info: ValidationInfo) -> object:
        if type(value) is not str or not value.startswith("/"):
            raise ValueError(f"{info.field_name} must be an exact absolute path")
        if "\x00" in value or "\n" in value or "\r" in value:
            raise ValueError(f"{info.field_name} contains an unsafe character")
        normalized = str(PurePath(value))
        if normalized != value.rstrip("/") and value != "/":
            raise ValueError(f"{info.field_name} must be lexically normalized")
        return value


class SandboxProfile(_AuthorityContractModel):
    profile_id: str = Field(alias="profileId", min_length=1)
    private_workspace_host_path: str = Field(alias="privateWorkspaceHostPath", min_length=1)
    live_workspace_host_path: str = Field(alias="liveWorkspaceHostPath", min_length=1)
    workspace_mount: Literal["/workspace"] = Field(alias="workspaceMount")
    read_only_bindings: tuple[SandboxBinding, ...] = Field(alias="readOnlyBindings")
    writable_temp_host_path: str = Field(alias="writableTempHostPath", min_length=1)
    writable_temp_mount: Literal["/tmp"] = Field(alias="writableTempMount")
    network_mode: NetworkMode = Field(alias="networkMode")
    egress_proxy_socket: str | None = Field(default=None, alias="egressProxySocket")
    destination_policy_digest: str | None = Field(
        default=None,
        alias="destinationPolicyDigest",
    )
    environment_allowlist: tuple[str, ...] = Field(alias="environmentAllowlist")
    credential_refs: tuple[str, ...] = Field(alias="credentialRefs")
    timeout_ms: int = Field(alias="timeoutMs", gt=0, strict=True)
    stdout_limit_bytes: int = Field(alias="stdoutLimitBytes", gt=0, strict=True)
    stderr_limit_bytes: int = Field(alias="stderrLimitBytes", gt=0, strict=True)
    process_limit: int = Field(alias="processLimit", gt=0, strict=True)
    memory_limit_bytes: int = Field(alias="memoryLimitBytes", gt=0, strict=True)
    cpu_time_limit_ms: int = Field(alias="cpuTimeLimitMs", gt=0, strict=True)
    file_descriptor_limit: int = Field(alias="fileDescriptorLimit", gt=0, strict=True)
    profile_digest: str | None = Field(default=None, alias="profileDigest")

    @field_validator(
        "private_workspace_host_path",
        "live_workspace_host_path",
        "writable_temp_host_path",
        "egress_proxy_socket",
        mode="before",
    )
    @classmethod
    def _require_exact_absolute_host_path(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> object:
        if value is None:
            return None
        if type(value) is not str or not value.startswith("/"):
            raise ValueError(f"{info.field_name} must be an exact absolute path")
        if "\x00" in value or "\n" in value or "\r" in value:
            raise ValueError(f"{info.field_name} contains an unsafe character")
        if str(PurePath(value)) != value.rstrip("/") and value != "/":
            raise ValueError(f"{info.field_name} must be lexically normalized")
        return value

    @field_validator(
        "read_only_bindings",
        "environment_allowlist",
        "credential_refs",
        mode="before",
    )
    @classmethod
    def _require_ordered_collections(cls, value: object, info: ValidationInfo) -> object:
        if type(value) not in (list, tuple):
            raise ValueError(f"{info.field_name} must use an ordered list or tuple")
        return value

    @field_validator("destination_policy_digest", "profile_digest")
    @classmethod
    def _validate_optional_digest(cls, value: str | None) -> str | None:
        return None if value is None else require_digest(value)

    @model_validator(mode="after")
    def _validate_default_deny_profile(self) -> Self:
        private_path = PurePath(self.private_workspace_host_path)
        live_path = PurePath(self.live_workspace_host_path)
        temp_path = PurePath(self.writable_temp_host_path)
        if str(private_path) == "/":
            raise ValueError("private workspace host path cannot be writable root")
        if private_path == live_path or private_path.is_relative_to(live_path):
            raise ValueError("private workspace must not alias the live workspace")
        if live_path.is_relative_to(private_path):
            raise ValueError("private workspace must not contain the live workspace")
        if str(temp_path) == "/":
            raise ValueError("writable temp host path cannot be root")
        if temp_path in {private_path, live_path}:
            raise ValueError("writable temp must not alias a workspace")

        binding_keys = tuple(
            (binding.host_path, binding.mount_path) for binding in self.read_only_bindings
        )
        if len(binding_keys) != len(set(binding_keys)):
            raise ValueError("readOnlyBindings must be unique")
        host_paths = tuple(key[0] for key in binding_keys)
        mount_paths = tuple(key[1] for key in binding_keys)
        if len(host_paths) != len(set(host_paths)) or len(mount_paths) != len(set(mount_paths)):
            raise ValueError("sandbox binding host and mount paths must be unique")
        for binding in self.read_only_bindings:
            host_path = PurePath(binding.host_path)
            mount_path = PurePath(binding.mount_path)
            if str(host_path) == "/" or str(mount_path) == "/":
                raise ValueError("read-only root binding would expose the whole host")
            if any(part.casefold() in _SENSITIVE_PATH_PARTS for part in host_path.parts):
                raise ValueError("sensitive host roots require a dedicated credential channel")
            if host_path == live_path or live_path.is_relative_to(host_path):
                raise ValueError("live workspace must never appear in readOnlyBindings")
            if mount_path in {PurePath(self.workspace_mount), PurePath(self.writable_temp_mount)}:
                raise ValueError("readOnlyBindings cannot shadow writable sandbox mounts")

        if len(self.environment_allowlist) != len(set(self.environment_allowlist)):
            raise ValueError("environment allowlist entries must be unique")
        if self.environment_allowlist != tuple(sorted(self.environment_allowlist)):
            raise ValueError("environment allowlist must be canonically sorted")
        if any(not _ENVIRONMENT_NAME_RE.fullmatch(name) for name in self.environment_allowlist):
            raise ValueError("environment allowlist rejects wildcards and invalid names")
        if len(self.credential_refs) != len(set(self.credential_refs)):
            raise ValueError("credentialRefs must be unique")
        if self.credential_refs != tuple(sorted(self.credential_refs)):
            raise ValueError("credentialRefs must be canonically sorted")
        if any(not ref.startswith("credential://") for ref in self.credential_refs):
            raise ValueError("credentialRefs must use credential:// identities")

        if self.network_mode is NetworkMode.PROXY:
            if self.egress_proxy_socket is None or self.destination_policy_digest is None:
                raise ValueError("proxy mode requires an exact socket and destination policy")
        elif self.egress_proxy_socket is not None or self.destination_policy_digest is not None:
            raise ValueError("network none cannot carry proxy authority")

        expected = _profile_digest(self)
        if self.profile_digest is not None and self.profile_digest != expected:
            raise ValueError("profileDigest does not match the exact sandbox profile")
        object.__setattr__(self, "profile_digest", expected)
        return self


class SandboxBackendSelection(_AuthorityContractModel):
    status: SandboxCapabilityStatus
    backend_id: Literal["linux_bwrap_v1", "macos_seatbelt_v1"] | None = Field(
        alias="backendId"
    )
    primitive: str | None = None
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")

    @model_validator(mode="after")
    def _validate_selection(self) -> Self:
        available = self.status is SandboxCapabilityStatus.AVAILABLE
        if available != (self.backend_id is not None and self.primitive is not None):
            raise ValueError("available sandbox selection requires backend and primitive")
        if available and self.reason_codes:
            raise ValueError("available sandbox selection cannot carry failure reasons")
        if not available and not self.reason_codes:
            raise ValueError("unavailable sandbox selection requires a reason")
        return self


def _profile_digest(profile: SandboxProfile) -> str:
    payload = profile.model_dump(
        by_alias=True,
        mode="json",
        exclude={"profile_digest"},
    )
    return canonical_digest(payload)


def canonical_sandbox_profile_digest(profile: SandboxProfile) -> str:
    if type(profile) is not SandboxProfile:
        raise TypeError("profile must be an exact SandboxProfile")
    validated = SandboxProfile.model_validate(profile)
    return _profile_digest(validated)


def select_sandbox_backend(
    *,
    platform_name: str,
    command_exists: Callable[[str], bool],
) -> SandboxBackendSelection:
    if type(platform_name) is not str:
        raise TypeError("platform_name must be an exact string")
    platform = platform_name.casefold()
    if platform.startswith("linux"):
        if command_exists("bwrap"):
            return SandboxBackendSelection(
                status=SandboxCapabilityStatus.AVAILABLE,
                backendId="linux_bwrap_v1",
                primitive="bwrap",
                reasonCodes=(),
            )
        reason = "linux_bwrap_missing"
    elif platform in {"darwin", "macos"}:
        if command_exists("sandbox-exec"):
            return SandboxBackendSelection(
                status=SandboxCapabilityStatus.AVAILABLE,
                backendId="macos_seatbelt_v1",
                primitive="sandbox-exec",
                reasonCodes=(),
            )
        reason = "macos_seatbelt_missing"
    else:
        reason = "unsupported_platform"
    return SandboxBackendSelection(
        status=SandboxCapabilityStatus.UNAVAILABLE,
        backendId=None,
        primitive=None,
        reasonCodes=(reason,),
    )
