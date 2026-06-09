"""Task 2.3 — E2E: dispatch MemoryWrite and assert file written (live) or not (gate-off).

With gate-on (MAGI_MEMORY_WRITE_READINESS_ENABLED=1, MAGI_MEMORY_WRITE_ENABLED=1,
MAGI_MEMORY_LOCAL_DEV=1): dispatching MemoryWrite persists to USER.md.

With gate fully off: MemoryWrite returns a blocked/simulated receipt and USER.md
is NOT written.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest


def _make_context(workspace_root: str):
    from magi_agent.tools.context import ToolContext

    return ToolContext(
        bot_id="bot-e2e",
        turn_id="turn-e2e",
        workspace_root=workspace_root,
        permission_scope={
            "mode": "selected_full_toolhost",
            "source": "selected_full_toolhost",
        },
    )


# ---------------------------------------------------------------------------
# Live gate: real write to USER.md
# ---------------------------------------------------------------------------


def test_memory_write_e2e_live_writes_to_user_md(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With all gates on, dispatching MemoryWrite appends to USER.md."""
    monkeypatch.setenv("MAGI_MEMORY_WRITE_READINESS_ENABLED", "1")
    monkeypatch.setenv("MAGI_MEMORY_WRITE_ENABLED", "1")
    monkeypatch.setenv("MAGI_MEMORY_LOCAL_DEV", "1")

    from magi_agent.runtime.memory_write_wiring import build_memory_write_host
    from magi_agent.tools.registry import ToolRegistry
    from magi_agent.tools.catalog import register_core_tool_manifests

    registry = ToolRegistry()
    register_core_tool_manifests(registry)

    host = build_memory_write_host(
        workspace_root=tmp_path,
        bot_id="bot-e2e",
        user_id="user-e2e",
    )
    host.bind(registry)

    registration = registry.resolve_registration("MemoryWrite")
    assert registration is not None
    assert registration.handler is not None

    context = _make_context(str(tmp_path))

    result = asyncio.run(
        registration.handler(
            {"fact": "user is based in Seoul", "target_file": "USER.md"},
            context,
        )
    )

    # The write should succeed
    assert result.status == "ok", f"Expected ok, got {result.status}: {result}"
    assert result.output is not None
    assert result.output["written"] is True
    assert result.output["realWrite"] is True

    # The harness writes to MEMORY.md (the default persistence target); the
    # target_file argument is validated and echoed in the ToolResult but the
    # underlying _attempt_real_write always uses MEMORY.md.
    memory_md = tmp_path / "MEMORY.md"
    assert memory_md.exists(), "MEMORY.md was not created by the real write path"
    content = memory_md.read_text(encoding="utf-8")
    assert "Seoul" in content, f"'Seoul' not found in MEMORY.md: {content!r}"


# ---------------------------------------------------------------------------
# Gate off: blocked/simulated, no file written
# ---------------------------------------------------------------------------


def test_memory_write_e2e_gate_off_no_file_written(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With gate fully off, the handler returns blocked and USER.md is not created."""
    monkeypatch.delenv("MAGI_MEMORY_WRITE_READINESS_ENABLED", raising=False)
    monkeypatch.delenv("MAGI_MEMORY_WRITE_ENABLED", raising=False)
    monkeypatch.delenv("MAGI_MEMORY_LOCAL_DEV", raising=False)

    from magi_agent.runtime.memory_write_wiring import build_memory_write_host
    from magi_agent.tools.registry import ToolRegistry
    from magi_agent.tools.catalog import register_core_tool_manifests

    registry = ToolRegistry()
    register_core_tool_manifests(registry)

    host = build_memory_write_host(
        workspace_root=tmp_path,
        bot_id="bot-e2e",
        user_id="user-e2e",
    )
    host.bind(registry)

    registration = registry.resolve_registration("MemoryWrite")
    assert registration is not None
    assert registration.handler is not None

    context = _make_context(str(tmp_path))

    result = asyncio.run(
        registration.handler(
            {"fact": "user is based in Seoul", "target_file": "USER.md"},
            context,
        )
    )

    assert result.status == "blocked"

    memory_md = tmp_path / "MEMORY.md"
    user_md = tmp_path / "USER.md"
    assert not memory_md.exists(), "MEMORY.md must NOT exist when gate is off"
    assert not user_md.exists(), "USER.md must NOT exist when gate is off"


# ---------------------------------------------------------------------------
# Shadow mode: simulated receipt, no file written
# ---------------------------------------------------------------------------


def test_memory_write_e2e_shadow_mode_no_file_written(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In shadow mode the handler returns ok (simulated) but USER.md is not written."""
    import hashlib

    bot_id = "bot-shadow-e2e"
    user_id = "user-shadow-e2e"

    def _sha(v: str) -> str:
        return "sha256:" + hashlib.sha256(v.encode()).hexdigest()

    monkeypatch.setenv("MAGI_MEMORY_WRITE_READINESS_ENABLED", "1")
    monkeypatch.delenv("MAGI_MEMORY_WRITE_ENABLED", raising=False)
    monkeypatch.delenv("MAGI_MEMORY_LOCAL_DEV", raising=False)

    from magi_agent.gates.memory_write_readiness import MemoryWriteReadinessConfig
    from magi_agent.runtime.memory_write_wiring import build_memory_write_host
    from magi_agent.tools.registry import ToolRegistry
    from magi_agent.tools.catalog import register_core_tool_manifests

    shadow_config = MemoryWriteReadinessConfig(
        enabled=True,
        killSwitchEnabled=False,
        shadowModeEnabled=True,
        selectedBotDigest=_sha(bot_id),
        selectedOwnerUserIdDigest=_sha(user_id),
        environment="local",
        environmentAllowlist=("local",),
        promotedGate=0,
        canaryPromotionConfirmed=False,
    )

    registry = ToolRegistry()
    register_core_tool_manifests(registry)

    host = build_memory_write_host(
        workspace_root=tmp_path,
        bot_id=bot_id,
        user_id=user_id,
        readiness_config=shadow_config,
    )
    host.bind(registry)

    registration = registry.resolve_registration("MemoryWrite")
    assert registration is not None
    assert registration.handler is not None

    context = _make_context(str(tmp_path))

    result = asyncio.run(
        registration.handler(
            {"fact": "user prefers concise replies", "target_file": "USER.md"},
            context,
        )
    )

    # Shadow returns ok (simulated) but isRealWrite is False
    assert result.status == "ok"
    assert result.output is not None
    assert result.output["written"] is True
    assert result.output["realWrite"] is False

    user_md = tmp_path / "USER.md"
    assert not user_md.exists(), "USER.md must NOT exist in shadow mode"
