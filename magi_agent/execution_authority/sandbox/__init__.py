"""Dormant fail-closed OS sandbox contracts and invocation builders."""

from magi_agent.execution_authority.sandbox.base import (
    NetworkMode,
    SandboxBackendSelection,
    SandboxBinding,
    SandboxCapabilityStatus,
    SandboxInvocation,
    SandboxProfile,
    SandboxUnavailable,
    canonical_sandbox_profile_digest,
    select_sandbox_backend,
)

__all__ = [
    "NetworkMode",
    "SandboxBackendSelection",
    "SandboxBinding",
    "SandboxCapabilityStatus",
    "SandboxInvocation",
    "SandboxProfile",
    "SandboxUnavailable",
    "canonical_sandbox_profile_digest",
    "select_sandbox_backend",
]
