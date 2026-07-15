"""Pure Linux bubblewrap invocation construction."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol

from magi_agent.execution_authority.sandbox.base import (
    NetworkMode,
    SandboxInvocation,
    SandboxUnavailable,
)


class _Binding(Protocol):
    host_path: str
    mount_path: str


class _Profile(Protocol):
    private_workspace_host_path: str
    live_workspace_host_path: str
    workspace_mount: str
    writable_temp_host_path: str
    writable_temp_mount: str
    read_only_bindings: Sequence[_Binding]
    network_mode: NetworkMode
    egress_proxy_socket: str | None


class _Variable(Protocol):
    name: str
    value: str


class _Material(Protocol):
    arguments: Sequence[str]
    working_directory: str
    environment: Sequence[_Variable]


class _Target(Protocol):
    executable_path: str


class _Request(Protocol):
    profile: _Profile
    material: _Material
    target: _Target


class LinuxBwrapBackend:
    def __init__(self, primitive: str, *, command_exists: Callable[[str], bool]) -> None:
        self._primitive = primitive
        self._command_exists = command_exists

    def build_invocation(self, request: _Request) -> SandboxInvocation:
        if not self._command_exists(self._primitive):
            raise SandboxUnavailable("bwrap primitive is unavailable")
        profile = request.profile
        command = [
            self._primitive,
            "--die-with-parent",
            "--new-session",
            "--unshare-all",
            "--clearenv",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
        ]
        for binding in profile.read_only_bindings:
            command.extend(("--ro-bind", binding.host_path, binding.mount_path))
        command.extend(
            (
                "--bind",
                profile.private_workspace_host_path,
                profile.workspace_mount,
                "--bind",
                profile.writable_temp_host_path,
                profile.writable_temp_mount,
            )
        )
        if profile.network_mode is NetworkMode.PROXY:
            if profile.egress_proxy_socket is None:
                raise SandboxUnavailable("proxy mode has no egress socket")
            command.extend(("--ro-bind", profile.egress_proxy_socket, profile.egress_proxy_socket))
        for variable in request.material.environment:
            command.extend(("--setenv", variable.name, variable.value))
        command.extend(("--chdir", request.material.working_directory, "--"))
        command.extend((request.target.executable_path, *request.material.arguments))
        return SandboxInvocation(command=tuple(command), profileText=None)


__all__ = ["LinuxBwrapBackend"]
