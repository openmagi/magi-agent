"""Pure macOS seatbelt profile and invocation construction."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

from magi_agent.execution_authority.sandbox.base import (
    NetworkMode,
    SandboxInvocation,
    SandboxUnavailable,
)


def encode_seatbelt_literal(value: str) -> str:
    if type(value) is not str or any(character in value for character in ('"', "\n", "\r", "*")):
        raise ValueError("unsafe seatbelt literal")
    return f'"{value}"'


class _Profile(Protocol):
    private_workspace_host_path: str
    live_workspace_host_path: str
    writable_temp_host_path: str
    network_mode: NetworkMode


class _Material(Protocol):
    arguments: Sequence[str]


class _Target(Protocol):
    executable_path: str


class _Request(Protocol):
    profile: _Profile
    material: _Material
    target: _Target


class MacOSSeatbeltBackend:
    def __init__(self, primitive: str, *, command_exists: Callable[[str], bool]) -> None:
        self._primitive = primitive
        self._command_exists = command_exists

    def build_invocation(
        self,
        request: _Request,
        *,
        generated_profile_path: Path,
    ) -> SandboxInvocation:
        if not self._command_exists(self._primitive):
            raise SandboxUnavailable("sandbox-exec primitive is unavailable")
        profile = request.profile
        rules = [
            "(version 1)",
            "(deny default)",
            "(allow process-exec)",
            "(allow process-fork)",
            f"(allow file-read* (subpath {encode_seatbelt_literal(profile.private_workspace_host_path)}))",
            f"(allow file-write* (subpath {encode_seatbelt_literal(profile.private_workspace_host_path)}))",
            f"(allow file-read* file-write* (subpath {encode_seatbelt_literal(profile.writable_temp_host_path)}))",
        ]
        if profile.network_mode is NetworkMode.NONE:
            rules.append("(deny network*)")
        else:
            rules.append("(allow network-outbound (remote unix-socket))")
        profile_text = "\n".join(rules) + "\n"
        command = (
            self._primitive,
            "-f",
            str(generated_profile_path),
            request.target.executable_path,
            *request.material.arguments,
        )
        return SandboxInvocation(command=command, profileText=profile_text)


__all__ = ["MacOSSeatbeltBackend", "encode_seatbelt_literal"]
