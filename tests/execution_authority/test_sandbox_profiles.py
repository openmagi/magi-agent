from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from magi_agent.execution_authority.sandbox.base import (
    NetworkMode,
    SandboxBinding,
    SandboxCapabilityStatus,
    SandboxProfile,
    canonical_sandbox_profile_digest,
    select_sandbox_backend,
)


D1 = "sha256:" + "1" * 64


def _profile(tmp_path: Path, **changes: object) -> SandboxProfile:
    values: dict[str, object] = {
        "profileId": "command_default_v1",
        "privateWorkspaceHostPath": str(tmp_path / "private-workspace"),
        "liveWorkspaceHostPath": str(tmp_path / "live-workspace"),
        "workspaceMount": "/workspace",
        "readOnlyBindings": (
            SandboxBinding(hostPath="/usr", mountPath="/usr"),
            SandboxBinding(hostPath="/bin", mountPath="/bin"),
        ),
        "writableTempHostPath": str(tmp_path / "private-tmp"),
        "writableTempMount": "/tmp",
        "networkMode": NetworkMode.NONE,
        "egressProxySocket": None,
        "destinationPolicyDigest": None,
        "environmentAllowlist": ("LANG", "PATH"),
        "credentialRefs": (),
        "timeoutMs": 30_000,
        "stdoutLimitBytes": 1_000_000,
        "stderrLimitBytes": 1_000_000,
        "processLimit": 64,
        "memoryLimitBytes": 512 * 1024 * 1024,
        "cpuTimeLimitMs": 30_000,
        "fileDescriptorLimit": 256,
    }
    values.update(changes)
    return SandboxProfile.model_validate(values)


def test_unsupported_platform_has_no_unsandboxed_backend() -> None:
    result = select_sandbox_backend(
        platform_name="freebsd",
        command_exists=lambda _name: False,
    )
    assert result.status is SandboxCapabilityStatus.UNAVAILABLE
    assert result.backend_id is None
    assert result.reason_codes == ("unsupported_platform",)


@pytest.mark.parametrize(
    ("platform_name", "primitive", "reason"),
    (
        ("linux", "bwrap", "linux_bwrap_missing"),
        ("darwin", "sandbox-exec", "macos_seatbelt_missing"),
    ),
)
def test_supported_platform_without_required_primitive_fails_closed(
    platform_name: str,
    primitive: str,
    reason: str,
) -> None:
    probes: list[str] = []
    result = select_sandbox_backend(
        platform_name=platform_name,
        command_exists=lambda name: probes.append(name) is None and False,
    )
    assert probes == [primitive]
    assert result.status is SandboxCapabilityStatus.UNAVAILABLE
    assert result.backend_id is None
    assert result.reason_codes == (reason,)


def test_matching_platform_selects_only_the_os_primitive() -> None:
    linux = select_sandbox_backend(
        platform_name="linux",
        command_exists=lambda name: name == "bwrap",
    )
    macos = select_sandbox_backend(
        platform_name="darwin",
        command_exists=lambda name: name == "sandbox-exec",
    )
    assert linux.status is SandboxCapabilityStatus.AVAILABLE
    assert linux.backend_id == "linux_bwrap_v1"
    assert macos.status is SandboxCapabilityStatus.AVAILABLE
    assert macos.backend_id == "macos_seatbelt_v1"


def test_profile_is_frozen_default_deny_and_content_addressed(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    assert profile.network_mode is NetworkMode.NONE
    assert "HOME" not in profile.environment_allowlist
    assert profile.credential_refs == ()
    assert profile.profile_digest == canonical_sandbox_profile_digest(profile)
    with pytest.raises(ValidationError):
        profile.timeout_ms = 1  # type: ignore[misc]


@pytest.mark.parametrize(
    ("changes", "message"),
    (
        ({"privateWorkspaceHostPath": "/"}, "private workspace"),
        (
            {
                "privateWorkspaceHostPath": "/private/workspace",
                "liveWorkspaceHostPath": "/private/workspace",
            },
            "live workspace",
        ),
        ({"workspaceMount": "/tmp/workspace"}, "workspaceMount"),
        ({"writableTempMount": "/"}, "writableTempMount"),
        ({"environmentAllowlist": ("*",)}, "environment"),
        (
            {
                "readOnlyBindings": (
                    SandboxBinding(hostPath="/home/me/.ssh", mountPath="/keys"),
                )
            },
            "sensitive",
        ),
    ),
)
def test_profile_rejects_host_escape_and_implicit_sensitive_access(
    tmp_path: Path,
    changes: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        _profile(tmp_path, **changes)


def test_proxy_requires_exact_socket_policy_and_credential_grants(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="proxy"):
        _profile(tmp_path, networkMode=NetworkMode.PROXY)

    profile = _profile(
        tmp_path,
        networkMode=NetworkMode.PROXY,
        egressProxySocket=str(tmp_path / "proxy.sock"),
        destinationPolicyDigest=D1,
        credentialRefs=("credential://tenant/mail",),
    )
    assert profile.egress_proxy_socket == str(tmp_path / "proxy.sock")
    assert profile.destination_policy_digest == D1


def test_profile_rejects_duplicate_mounts_and_host_aliases(tmp_path: Path) -> None:
    binding = SandboxBinding(hostPath="/usr", mountPath="/usr")
    with pytest.raises(ValidationError, match="unique"):
        _profile(tmp_path, readOnlyBindings=(binding, binding))
