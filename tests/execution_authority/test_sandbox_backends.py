from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from magi_agent.execution_authority.sandbox.base import NetworkMode, SandboxUnavailable
from magi_agent.execution_authority.sandbox.linux_bwrap import LinuxBwrapBackend
from magi_agent.execution_authority.sandbox.macos_seatbelt import (
    MacOSSeatbeltBackend,
    encode_seatbelt_literal,
)


def _request(tmp_path: Path) -> SimpleNamespace:
    profile = SimpleNamespace(
        private_workspace_host_path=str(tmp_path / "private-workspace"),
        live_workspace_host_path=str(tmp_path / "live-workspace"),
        workspace_mount="/workspace",
        writable_temp_host_path=str(tmp_path / "private-tmp"),
        writable_temp_mount="/tmp",
        read_only_bindings=(),
        network_mode=NetworkMode.NONE,
        egress_proxy_socket=None,
    )
    return SimpleNamespace(
        profile=profile,
        target=SimpleNamespace(executable_path="/usr/bin/python3"),
        material=SimpleNamespace(
            arguments=("-c", "print('ok')"),
            working_directory="/workspace",
            environment=(
                SimpleNamespace(name="LANG", value="C.UTF-8"),
                SimpleNamespace(name="PATH", value="/usr/bin:/bin"),
            ),
        ),
    )


def test_bwrap_builds_a_default_deny_private_workspace_invocation(tmp_path: Path) -> None:
    request = _request(tmp_path)
    invocation = LinuxBwrapBackend(
        "/usr/bin/bwrap", command_exists=lambda path: path == "/usr/bin/bwrap"
    ).build_invocation(request)

    assert invocation.command[:5] == (
        "/usr/bin/bwrap",
        "--die-with-parent",
        "--new-session",
        "--unshare-all",
        "--clearenv",
    )
    assert request.profile.live_workspace_host_path not in invocation.command
    assert ("--bind", request.profile.private_workspace_host_path, "/workspace") == (
        invocation.command[
            invocation.command.index("--bind") : invocation.command.index("--bind") + 3
        ]
    )
    assert "--share-net" not in invocation.command


def test_seatbelt_builds_a_default_deny_profile(tmp_path: Path) -> None:
    request = _request(tmp_path)
    invocation = MacOSSeatbeltBackend(
        "/usr/bin/sandbox-exec", command_exists=lambda _path: True
    ).build_invocation(request, generated_profile_path=tmp_path / "profile.sb")

    assert invocation.profile_text is not None
    assert invocation.profile_text.startswith("(version 1)\n(deny default)\n")
    assert request.profile.private_workspace_host_path in invocation.profile_text
    assert request.profile.live_workspace_host_path not in invocation.profile_text
    assert "(deny network*)" in invocation.profile_text


@pytest.mark.parametrize("value", ('/tmp/evil"path', "/tmp/evil\npath", "/tmp/*"))
def test_seatbelt_literal_rejects_profile_injection(value: str) -> None:
    with pytest.raises(ValueError, match="seatbelt"):
        encode_seatbelt_literal(value)


def test_backends_fail_closed_when_the_primitive_disappears(tmp_path: Path) -> None:
    request = _request(tmp_path)
    with pytest.raises(SandboxUnavailable):
        LinuxBwrapBackend("/usr/bin/bwrap", command_exists=lambda _path: False).build_invocation(
            request
        )
    with pytest.raises(SandboxUnavailable):
        MacOSSeatbeltBackend(
            "/usr/bin/sandbox-exec", command_exists=lambda _path: False
        ).build_invocation(request, generated_profile_path=tmp_path / "profile.sb")
