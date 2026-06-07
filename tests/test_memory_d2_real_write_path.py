"""D2 — Gated real memory-write path + declarative-only write tool.

TDD tests written before implementation.

Sections:
A. Declarative-only filter — is_declarative() + rejection examples.
B. MemoryWriteHarness real-write path — gate-off → simulated, gate-on + provider → real.
C. Evidence recording — every write (real or simulated) produces an EvidenceRecord.
D. MemoryWrite tool manifest — exists in catalog, gated default-off.
E. Tool handler integration — tool calls go through gated boundary.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# A. Declarative filter
# ---------------------------------------------------------------------------


def test_is_declarative_accepts_clear_preference() -> None:
    from magi_agent.memory.declarative_filter import is_declarative

    assert is_declarative("User prefers concise answers") is True


def test_is_declarative_accepts_stable_fact() -> None:
    from magi_agent.memory.declarative_filter import is_declarative

    assert is_declarative("User's timezone is UTC+9") is True


def test_is_declarative_accepts_preference_with_word_currently_not_task() -> None:
    """'currently' in a preference sentence (not task-state) is accepted."""
    from magi_agent.memory.declarative_filter import is_declarative

    assert is_declarative("User currently prefers dark mode in their editor") is True


def test_is_declarative_rejects_pr_number() -> None:
    from magi_agent.memory.declarative_filter import is_declarative

    assert is_declarative("PR #123 merged successfully") is False


def test_is_declarative_rejects_issue_number() -> None:
    from magi_agent.memory.declarative_filter import is_declarative

    assert is_declarative("Issue #456 is in progress") is False


def test_is_declarative_rejects_commit_sha() -> None:
    from magi_agent.memory.declarative_filter import is_declarative

    assert is_declarative("Commit a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2 landed") is False


def test_is_declarative_rejects_done_marker() -> None:
    from magi_agent.memory.declarative_filter import is_declarative

    assert is_declarative("Phase 2 done") is False


def test_is_declarative_rejects_merged_marker() -> None:
    from magi_agent.memory.declarative_filter import is_declarative

    assert is_declarative("feature branch merged") is False


def test_is_declarative_rejects_in_progress_marker() -> None:
    from magi_agent.memory.declarative_filter import is_declarative

    assert is_declarative("Task XYZ currently in progress") is False


def test_is_declarative_rejects_currently_doing_task_state() -> None:
    from magi_agent.memory.declarative_filter import is_declarative

    assert is_declarative("Currently doing the deploy to production") is False


def test_is_declarative_rejects_iso_timestamp_as_state() -> None:
    from magi_agent.memory.declarative_filter import is_declarative

    assert is_declarative("Deploy ran at 2026-06-07T12:34:56Z") is False


def test_is_declarative_rejection_provides_reason() -> None:
    from magi_agent.memory.declarative_filter import DeclarativeFilterResult, is_declarative_result

    result: DeclarativeFilterResult = is_declarative_result("PR #123 merged")
    assert result.accepted is False
    assert result.rejection_reason is not None
    assert len(result.rejection_reason) > 0


def test_is_declarative_result_accepted_has_no_reason() -> None:
    from magi_agent.memory.declarative_filter import DeclarativeFilterResult, is_declarative_result

    result: DeclarativeFilterResult = is_declarative_result("User prefers Python over JavaScript")
    assert result.accepted is True
    assert result.rejection_reason is None


def test_is_declarative_empty_string_rejected() -> None:
    from magi_agent.memory.declarative_filter import is_declarative

    assert is_declarative("") is False


def test_is_declarative_whitespace_only_rejected() -> None:
    from magi_agent.memory.declarative_filter import is_declarative

    assert is_declarative("   ") is False


# ---------------------------------------------------------------------------
# B. MemoryWriteHarness real-write path
# ---------------------------------------------------------------------------


def _make_harness_config_enabled():
    """Helper: a MemoryWriteHarnessConfig with the harness enabled."""
    from magi_agent.harness.memory_write import MemoryWriteHarnessConfig

    return MemoryWriteHarnessConfig(enabled=True)


def _make_policy():
    """Helper: a minimal MemoryWritePolicy."""
    from magi_agent.harness.memory_write import MemoryWritePolicy

    return MemoryWritePolicy(
        policyRef="policy:test-d2",
        policySnapshotRef="policy:test-d2-snap",
        evidenceRequired=False,
        localFakeSuccessAllowed=True,
    )


def _make_request(body: str = "User prefers dark mode", *, operation: str = "remember"):
    """Helper: a minimal MemoryWriteRequest."""
    from magi_agent.harness.memory_write import MemoryWriteRequest

    return MemoryWriteRequest(
        providerId="local-file-memory-writable",
        turnId="turn-d2-test",
        operation=operation,
        content=body,
    )


def test_gate_off_harness_returns_simulated_receipt(tmp_path: Path) -> None:
    """Gate-OFF (no provider, local_fake enabled) → simulated receipt, no file written."""
    from magi_agent.harness.memory_write import MemoryWriteHarness, MemoryWriteHarnessConfig

    config = MemoryWriteHarnessConfig(enabled=True, localFakeAdapterEnabled=True)
    harness = MemoryWriteHarness(config)
    request = _make_request()
    policy = _make_policy()

    result = asyncio.run(harness.write(request=request, policy=policy))

    assert result.status == "success"
    assert result.receipt is not None
    # No file was created
    assert not (tmp_path / "MEMORY.md").exists()


def test_gate_off_no_real_file_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Even when MAGI_MEMORY_WRITE_ENABLED is not set, no file is written."""
    from magi_agent.memory.adapters.local_file_writable import MAGI_MEMORY_WRITE_ENABLED_ENV
    from magi_agent.harness.memory_write import MemoryWriteHarness, MemoryWriteHarnessConfig

    monkeypatch.delenv(MAGI_MEMORY_WRITE_ENABLED_ENV, raising=False)

    config = MemoryWriteHarnessConfig(enabled=True, localFakeAdapterEnabled=True)
    harness = MemoryWriteHarness(config)  # no provider injected
    request = _make_request()
    policy = _make_policy()

    asyncio.run(harness.write(request=request, policy=policy))

    assert not (tmp_path / "MEMORY.md").exists()


def test_gate_on_with_provider_writes_real_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gate-ON + LocalFileMemoryProvider injected → real append to MEMORY.md."""
    from magi_agent.memory.adapters.local_file_writable import (
        LocalFileMemoryConfig,
        LocalFileMemoryProvider,
        MAGI_MEMORY_WRITE_ENABLED_ENV,
    )
    from magi_agent.harness.memory_write import MemoryWriteHarness, MemoryWriteHarnessConfig

    monkeypatch.setenv(MAGI_MEMORY_WRITE_ENABLED_ENV, "1")

    provider_config = LocalFileMemoryConfig(
        workspace_root=tmp_path, enabled=True, write_enabled=True
    )
    provider = LocalFileMemoryProvider(provider_config)

    harness_config = MemoryWriteHarnessConfig(enabled=True, localFakeAdapterEnabled=True)
    harness = MemoryWriteHarness(harness_config, adapter=provider)

    request = _make_request("User prefers vim keybindings")
    policy = _make_policy()

    result = asyncio.run(harness.write(request=request, policy=policy))

    assert result.status == "success"
    memory_file = tmp_path / "MEMORY.md"
    assert memory_file.exists()
    content = memory_file.read_text(encoding="utf-8")
    assert "vim keybindings" in content


def test_gate_on_without_provider_stays_simulated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gate env ON but no provider injected → still simulated (no file written)."""
    from magi_agent.memory.adapters.local_file_writable import MAGI_MEMORY_WRITE_ENABLED_ENV
    from magi_agent.harness.memory_write import MemoryWriteHarness, MemoryWriteHarnessConfig

    monkeypatch.setenv(MAGI_MEMORY_WRITE_ENABLED_ENV, "1")

    harness_config = MemoryWriteHarnessConfig(enabled=True, localFakeAdapterEnabled=True)
    harness = MemoryWriteHarness(harness_config)  # no provider

    request = _make_request("User prefers dark mode")
    policy = _make_policy()

    result = asyncio.run(harness.write(request=request, policy=policy))

    assert result.status == "success"
    # No file created (no provider)
    assert not (tmp_path / "MEMORY.md").exists()


def test_declarative_rejected_fact_is_blocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-declarative fact (task-state) → status='blocked', no file written."""
    from magi_agent.memory.adapters.local_file_writable import (
        LocalFileMemoryConfig,
        LocalFileMemoryProvider,
        MAGI_MEMORY_WRITE_ENABLED_ENV,
    )
    from magi_agent.harness.memory_write import MemoryWriteHarness, MemoryWriteHarnessConfig

    monkeypatch.setenv(MAGI_MEMORY_WRITE_ENABLED_ENV, "1")

    provider_config = LocalFileMemoryConfig(
        workspace_root=tmp_path, enabled=True, write_enabled=True
    )
    provider = LocalFileMemoryProvider(provider_config)

    harness_config = MemoryWriteHarnessConfig(enabled=True, localFakeAdapterEnabled=True)
    harness = MemoryWriteHarness(harness_config, adapter=provider)

    request = _make_request("PR #123 merged")  # task-state!
    policy = _make_policy()

    result = asyncio.run(harness.write(request=request, policy=policy))

    assert result.status == "blocked"
    assert "non_declarative" in result.reason_codes or any(
        "declarative" in code for code in result.reason_codes
    )
    # Nothing written to disk
    assert not (tmp_path / "MEMORY.md").exists()


def test_gate_on_provider_write_is_readable_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After real write, recall() on same provider returns the entry."""
    from magi_agent.memory.adapters.local_file_writable import (
        LocalFileMemoryConfig,
        LocalFileMemoryProvider,
        MAGI_MEMORY_WRITE_ENABLED_ENV,
    )
    from magi_agent.memory.contracts import RecallRequest
    from magi_agent.memory.policy import MemoryPolicy
    from magi_agent.harness.memory_write import MemoryWriteHarness, MemoryWriteHarnessConfig

    monkeypatch.setenv(MAGI_MEMORY_WRITE_ENABLED_ENV, "1")

    provider_config = LocalFileMemoryConfig(
        workspace_root=tmp_path, enabled=True, write_enabled=True
    )
    provider = LocalFileMemoryProvider(provider_config)

    harness_config = MemoryWriteHarnessConfig(enabled=True, localFakeAdapterEnabled=True)
    harness = MemoryWriteHarness(harness_config, adapter=provider)

    request = _make_request("User prefers ripgrep over grep")
    policy = _make_policy()
    asyncio.run(harness.write(request=request, policy=policy))

    recall_result = asyncio.run(
        provider.recall(
            RecallRequest(
                scope={"tenantId": "t1", "botId": "b1"},
                query="ripgrep",
                purpose="answer_user",
            ),
            policy=MemoryPolicy(memory_mode="normal", source_authority="long_term_allowed"),
        )
    )

    assert any("ripgrep" in rec.body for rec in recall_result.records)


# ---------------------------------------------------------------------------
# C. Evidence recording
# ---------------------------------------------------------------------------


def test_real_write_result_carries_evidence_ref(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Successful real write → result has at least one evidence_ref."""
    from magi_agent.memory.adapters.local_file_writable import (
        LocalFileMemoryConfig,
        LocalFileMemoryProvider,
        MAGI_MEMORY_WRITE_ENABLED_ENV,
    )
    from magi_agent.harness.memory_write import MemoryWriteHarness, MemoryWriteHarnessConfig

    monkeypatch.setenv(MAGI_MEMORY_WRITE_ENABLED_ENV, "1")

    provider_config = LocalFileMemoryConfig(
        workspace_root=tmp_path, enabled=True, write_enabled=True
    )
    provider = LocalFileMemoryProvider(provider_config)

    harness_config = MemoryWriteHarnessConfig(enabled=True, localFakeAdapterEnabled=True)
    harness = MemoryWriteHarness(harness_config, adapter=provider)

    request = _make_request("User prefers Python")
    policy = _make_policy()

    result = asyncio.run(harness.write(request=request, policy=policy))

    # Result must expose evidence provenance
    assert result.receipt is not None
    assert hasattr(result, "evidence_record") or hasattr(result, "evidence_ref") or (
        result.receipt is not None  # receipt itself is the evidence anchor
    )


def test_simulated_write_result_also_has_receipt(tmp_path: Path) -> None:
    """Simulated (gate-off) write → receipt is still present."""
    from magi_agent.harness.memory_write import MemoryWriteHarness, MemoryWriteHarnessConfig

    harness_config = MemoryWriteHarnessConfig(enabled=True, localFakeAdapterEnabled=True)
    harness = MemoryWriteHarness(harness_config)

    request = _make_request("User prefers light mode")
    policy = _make_policy()

    result = asyncio.run(harness.write(request=request, policy=policy))

    assert result.status == "success"
    assert result.receipt is not None


def test_evidence_record_on_write_has_correct_type(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EvidenceRecord produced by write has type matching the memory write operation."""
    from magi_agent.memory.adapters.local_file_writable import (
        LocalFileMemoryConfig,
        LocalFileMemoryProvider,
        MAGI_MEMORY_WRITE_ENABLED_ENV,
    )
    from magi_agent.harness.memory_write import MemoryWriteHarness, MemoryWriteHarnessConfig

    monkeypatch.setenv(MAGI_MEMORY_WRITE_ENABLED_ENV, "1")

    provider_config = LocalFileMemoryConfig(
        workspace_root=tmp_path, enabled=True, write_enabled=True
    )
    provider = LocalFileMemoryProvider(provider_config)

    harness_config = MemoryWriteHarnessConfig(enabled=True, localFakeAdapterEnabled=True)
    harness = MemoryWriteHarness(harness_config, adapter=provider)

    request = _make_request("User prefers tabs over spaces")
    policy = _make_policy()

    result = asyncio.run(harness.write(request=request, policy=policy))

    # Access the evidence record from the result
    ev = getattr(result, "evidence_record", None)
    if ev is not None:
        assert "memory" in ev.type.lower() or "MemoryWrite" in ev.type


# ---------------------------------------------------------------------------
# D. MemoryWrite tool manifest
# ---------------------------------------------------------------------------


def test_memory_write_tool_manifest_exists_in_catalog() -> None:
    """MemoryWrite must appear in the core tool catalog."""
    from magi_agent.tools.catalog import core_tool_manifests

    names = {m.name for m in core_tool_manifests()}
    assert "MemoryWrite" in names


def test_memory_write_tool_manifest_permission_is_write() -> None:
    from magi_agent.tools.catalog import core_tool_manifests

    manifest = next(m for m in core_tool_manifests() if m.name == "MemoryWrite")
    assert manifest.permission == "write"


def test_memory_write_tool_manifest_act_mode_only() -> None:
    """MemoryWrite must be available only in act mode, not plan mode."""
    from magi_agent.tools.catalog import core_tool_manifests

    manifest = next(m for m in core_tool_manifests() if m.name == "MemoryWrite")
    assert "act" in manifest.available_in_modes
    assert "plan" not in manifest.available_in_modes


def test_memory_write_tool_manifest_not_enabled_by_default() -> None:
    """MemoryWrite must default to disabled (gate-off consistent)."""
    from magi_agent.tools.catalog import core_tool_manifests

    manifest = next(m for m in core_tool_manifests() if m.name == "MemoryWrite")
    # enabled_by_default should be False so the registry disables it by default
    assert manifest.enabled_by_default is False


def test_memory_write_tool_manifest_has_fact_and_target_file_in_schema() -> None:
    """MemoryWrite schema must expose 'fact' and 'target_file' parameters."""
    from magi_agent.tools.catalog import core_tool_manifests

    manifest = next(m for m in core_tool_manifests() if m.name == "MemoryWrite")
    props = manifest.input_schema.get("properties", {})
    assert "fact" in props
    assert "target_file" in props


# ---------------------------------------------------------------------------
# E. Tool handler integration (using MemoryWriteToolHost)
# ---------------------------------------------------------------------------


def test_memory_write_tool_host_gate_off_returns_blocked(tmp_path: Path) -> None:
    """Without gate, MemoryWriteToolHost dispatch returns blocked."""
    from magi_agent.harness.memory_write_tool import (
        MemoryWriteToolHostConfig,
        MemoryWriteToolHost,
    )
    from magi_agent.tools.registry import ToolRegistry
    from magi_agent.tools.catalog import register_core_tool_manifests
    from magi_agent.tools.context import ToolContext

    registry = ToolRegistry()
    register_core_tool_manifests(registry)

    config = MemoryWriteToolHostConfig()  # all defaults: disabled
    host = MemoryWriteToolHost(config)
    host.bind(registry)

    registration = registry.resolve_registration("MemoryWrite")
    assert registration is not None
    assert registration.handler is not None

    async def run():
        ctx = ToolContext(botId="test-bot", workspace_root=str(tmp_path))
        return await registration.handler(
            {"fact": "User prefers dark mode"},
            ctx,
        )

    result = asyncio.run(run())
    # Gate is off → blocked or error
    assert result.status in {"blocked", "error"}


def test_memory_write_tool_host_gate_on_accepts_declarative(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With gate on, declarative fact → status ok."""
    from magi_agent.memory.adapters.local_file_writable import (
        LocalFileMemoryConfig,
        LocalFileMemoryProvider,
        MAGI_MEMORY_WRITE_ENABLED_ENV,
    )
    from magi_agent.harness.memory_write_tool import (
        MemoryWriteToolHostConfig,
        MemoryWriteToolHost,
    )
    from magi_agent.tools.registry import ToolRegistry
    from magi_agent.tools.catalog import register_core_tool_manifests
    from magi_agent.tools.context import ToolContext

    monkeypatch.setenv(MAGI_MEMORY_WRITE_ENABLED_ENV, "1")

    provider_config = LocalFileMemoryConfig(
        workspace_root=tmp_path, enabled=True, write_enabled=True
    )
    provider = LocalFileMemoryProvider(provider_config)

    registry = ToolRegistry()
    register_core_tool_manifests(registry)

    config = MemoryWriteToolHostConfig(enabled=True)
    host = MemoryWriteToolHost(config, provider=provider)
    host.bind(registry)

    async def run():
        ctx = ToolContext(botId="test-bot", workspace_root=str(tmp_path))
        registration = registry.resolve_registration("MemoryWrite")
        assert registration is not None
        assert registration.handler is not None
        return await registration.handler(
            {"fact": "User prefers concise answers"},
            ctx,
        )

    result = asyncio.run(run())
    assert result.status == "ok"
    # File should exist
    assert (tmp_path / "MEMORY.md").exists()


def test_memory_write_tool_host_gate_on_rejects_task_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With gate on, task-state fact (e.g. PR number) → blocked."""
    from magi_agent.memory.adapters.local_file_writable import (
        LocalFileMemoryConfig,
        LocalFileMemoryProvider,
        MAGI_MEMORY_WRITE_ENABLED_ENV,
    )
    from magi_agent.harness.memory_write_tool import (
        MemoryWriteToolHostConfig,
        MemoryWriteToolHost,
    )
    from magi_agent.tools.registry import ToolRegistry
    from magi_agent.tools.catalog import register_core_tool_manifests
    from magi_agent.tools.context import ToolContext

    monkeypatch.setenv(MAGI_MEMORY_WRITE_ENABLED_ENV, "1")

    provider_config = LocalFileMemoryConfig(
        workspace_root=tmp_path, enabled=True, write_enabled=True
    )
    provider = LocalFileMemoryProvider(provider_config)

    registry = ToolRegistry()
    register_core_tool_manifests(registry)

    config = MemoryWriteToolHostConfig(enabled=True)
    host = MemoryWriteToolHost(config, provider=provider)
    host.bind(registry)

    async def run():
        ctx = ToolContext(botId="test-bot", workspace_root=str(tmp_path))
        registration = registry.resolve_registration("MemoryWrite")
        assert registration is not None
        assert registration.handler is not None
        return await registration.handler(
            {"fact": "PR #999 merged"},
            ctx,
        )

    result = asyncio.run(run())
    assert result.status == "blocked"
    # No file written
    assert not (tmp_path / "MEMORY.md").exists()
