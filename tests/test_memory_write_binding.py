"""Task 2.2 — Bind the host into the runtime registry.

Tests that ``OpenMagiRuntime`` always binds a handler for ``MemoryWrite``
and that the enablement follows the gate state.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest


def _make_config():
    from magi_agent.config.models import BuildInfo, RuntimeConfig

    return RuntimeConfig(
        bot_id="bot-test",
        user_id="user-test",
        gateway_token="gateway-token",
        api_proxy_url="http://api-proxy.local",
        chat_proxy_url="http://chat-proxy.local",
        redis_url="redis://redis.local:6379/0",
        model="gpt-5.2",
        build=BuildInfo(version="0.1.0-test", build_sha="sha-test"),
    )


# ---------------------------------------------------------------------------
# Gate fully off → MemoryWrite is always bound (handler non-None) but disabled
# ---------------------------------------------------------------------------


def test_memory_write_handler_always_bound_after_runtime_init(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MemoryWrite handler must be non-None regardless of gate state.

    The tool is bound but its handler returns 'blocked' when the host config
    is disabled.  This verifies the binding itself, not the enablement.
    """
    monkeypatch.delenv("MAGI_MEMORY_WRITE_READINESS_ENABLED", raising=False)
    monkeypatch.delenv("MAGI_MEMORY_LOCAL_DEV", raising=False)

    from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime

    runtime = OpenMagiRuntime(config=_make_config())
    registration = runtime.tool_registry.resolve_registration("MemoryWrite")

    assert registration is not None
    assert registration.handler is not None


def test_memory_write_handler_returns_blocked_when_gate_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the gate is off, dispatching MemoryWrite returns a blocked ToolResult."""
    monkeypatch.delenv("MAGI_MEMORY_WRITE_READINESS_ENABLED", raising=False)
    monkeypatch.delenv("MAGI_MEMORY_LOCAL_DEV", raising=False)

    from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime
    from magi_agent.tools.context import ToolContext

    runtime = OpenMagiRuntime(config=_make_config())

    context = ToolContext(
        bot_id="bot-test",
        turn_id="turn-test",
        workspace_root="/tmp/magi-test",
        permission_scope={
            "mode": "selected_full_toolhost",
            "source": "selected_full_toolhost",
        },
    )
    registration = runtime.tool_registry.resolve_registration("MemoryWrite")
    assert registration is not None
    assert registration.handler is not None

    result = asyncio.run(
        registration.handler(
            {"fact": "user prefers dark mode", "target_file": "USER.md"},
            context,
        )
    )

    assert result.status == "blocked"


# ---------------------------------------------------------------------------
# Gate on (local-dev) → MemoryWrite is enabled in the registry
# ---------------------------------------------------------------------------


def test_memory_write_enabled_in_registry_when_local_dev_gate_on(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With local-dev env set, the registry must list MemoryWrite as enabled."""
    monkeypatch.setenv("MAGI_MEMORY_WRITE_READINESS_ENABLED", "1")
    monkeypatch.setenv("MAGI_MEMORY_WRITE_ENABLED", "1")
    monkeypatch.setenv("MAGI_MEMORY_LOCAL_DEV", "1")

    from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime

    runtime = OpenMagiRuntime(config=_make_config())

    assert runtime.tool_registry.is_enabled("MemoryWrite") is True
    registration = runtime.tool_registry.resolve_registration("MemoryWrite")
    assert registration is not None
    assert registration.handler is not None
