"""D4 — USER.md profile authoring + operator-gated SOUL.md path.

Test contract:
A. USER.md profile writes — agent can write profile facts to USER.md via the
   existing D1/D2 path.  Writes are declarative, redacted, bounded, projected.
B. Profile-line deduplication — identical lines are NOT appended twice.
C. SOUL.md agent rejection — any agent write to SOUL.md is rejected by:
     (a) the D1 allowlist (ValueError from _extract_target_file)
     (b) the D2 tool (target_file rejected, falls back to MEMORY.md — not SOUL)
     (c) the OperatorSoulWriter is unreachable from the agent tool/gate path
D. OperatorSoulWriter — separate operator authority; can write SOUL.md when
   operator_enabled=True; is default-off; its gate does NOT interact with
   MAGI_MEMORY_WRITE_ENABLED.
E. Default-off inertness — all gates closed by default.
F. D3 projection still covers USER.md after a profile write.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_provider(tmp_path: Path, *, write_enabled: bool = True):
    from magi_agent.memory.adapters.local_file_writable import (
        LocalFileMemoryConfig,
        LocalFileMemoryProvider,
    )

    config = LocalFileMemoryConfig(
        workspace_root=tmp_path,
        enabled=True,
        write_enabled=write_enabled,
    )
    return LocalFileMemoryProvider(config)


# ---------------------------------------------------------------------------
# A. USER.md profile writes via standard D1/D2 path
# ---------------------------------------------------------------------------


def test_agent_can_write_profile_fact_to_user_md(tmp_path: Path) -> None:
    """The agent writes a profile fact to USER.md via the standard remember() path."""
    provider = _make_provider(tmp_path)

    asyncio.run(provider.remember({
        "body": "User prefers concise answers",
        "kind": "profile",
        "target_file": "USER.md",
    }))

    content = (tmp_path / "USER.md").read_text(encoding="utf-8")
    assert "User prefers concise answers" in content


def test_user_md_write_is_redacted(tmp_path: Path) -> None:
    """Secrets in a USER.md write are redacted before persisting."""
    provider = _make_provider(tmp_path)

    asyncio.run(provider.remember({
        "body": "User token is sk-live-abc12345678 and likes dark mode",
        "kind": "profile",
        "target_file": "USER.md",
    }))

    content = (tmp_path / "USER.md").read_text(encoding="utf-8")
    assert "sk-live-abc12345678" not in content
    assert "dark mode" in content  # non-secret content preserved


def test_user_md_write_bounded_by_max_write_bytes(tmp_path: Path) -> None:
    """USER.md writes are bounded by max_write_bytes exactly like MEMORY.md writes."""
    from magi_agent.memory.adapters.local_file_writable import (
        LocalFileMemoryConfig,
        LocalFileMemoryProvider,
    )
    from magi_agent.memory.contracts import UnsupportedMemoryOperationError

    config = LocalFileMemoryConfig(
        workspace_root=tmp_path,
        enabled=True,
        write_enabled=True,
        max_write_bytes=10,
    )
    provider = LocalFileMemoryProvider(config)

    with pytest.raises((ValueError, UnsupportedMemoryOperationError)):
        asyncio.run(provider.remember({
            "body": "User prefers concise answers over verbose ones",
            "target_file": "USER.md",
        }))


def test_user_md_write_projected_by_d3(tmp_path: Path) -> None:
    """After a USER.md profile write, D3 projection includes the content."""
    from magi_agent.memory.prompt_projection import MemoryPromptProjector

    provider = _make_provider(tmp_path)
    asyncio.run(provider.remember({
        "body": "User timezone is UTC+9",
        "kind": "profile",
        "target_file": "USER.md",
    }))

    projector = MemoryPromptProjector(tmp_path, enabled=True)
    result = projector.project(memory_mode="normal")

    assert result.enabled is True
    assert "UTC+9" in result.snapshot_block
    assert "USER.md" in result.files_loaded


def test_user_md_write_is_declarative_gated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The declarative filter also blocks task-state facts written to USER.md."""
    from magi_agent.memory.adapters.local_file_writable import (
        LocalFileMemoryConfig,
        LocalFileMemoryProvider,
        MAGI_MEMORY_WRITE_ENABLED_ENV,
    )
    from magi_agent.harness.memory_write import MemoryWriteHarness, MemoryWriteHarnessConfig, MemoryWritePolicy, MemoryWriteRequest

    monkeypatch.setenv(MAGI_MEMORY_WRITE_ENABLED_ENV, "1")

    provider_config = LocalFileMemoryConfig(
        workspace_root=tmp_path, enabled=True, write_enabled=True
    )
    provider = LocalFileMemoryProvider(provider_config)

    harness_config = MemoryWriteHarnessConfig(enabled=True, localFakeAdapterEnabled=True)
    harness = MemoryWriteHarness(harness_config, adapter=provider)

    # Task-state: should be blocked even with target_file=USER.md
    request = MemoryWriteRequest(
        providerId="local-file-memory-writable",
        turnId="turn-d4-test",
        operation="remember",
        content="PR #999 merged for user profile",
    )
    policy = MemoryWritePolicy(
        policyRef="policy:d4-test",
        policySnapshotRef="policy:d4-test-snap",
        evidenceRequired=False,
        localFakeSuccessAllowed=True,
    )
    result = asyncio.run(harness.write(request=request, policy=policy))

    assert result.status == "blocked"
    assert not (tmp_path / "USER.md").exists()


# ---------------------------------------------------------------------------
# B. Profile-line deduplication
# ---------------------------------------------------------------------------


def test_identical_profile_line_not_appended_twice(tmp_path: Path) -> None:
    """Writing the same profile fact twice to USER.md does not duplicate the line."""
    from magi_agent.memory.adapters.local_file_writable import (
        LocalFileMemoryProvider,
        LocalFileMemoryConfig,
    )

    config = LocalFileMemoryConfig(
        workspace_root=tmp_path,
        enabled=True,
        write_enabled=True,
    )
    provider = LocalFileMemoryProvider(config)

    fact = "User prefers dark mode"
    asyncio.run(provider.remember({
        "body": fact,
        "kind": "profile",
        "target_file": "USER.md",
    }))
    asyncio.run(provider.remember({
        "body": fact,
        "kind": "profile",
        "target_file": "USER.md",
    }))

    content = (tmp_path / "USER.md").read_text(encoding="utf-8")
    # The fact body should appear only once
    count = content.count(fact)
    assert count == 1, f"Expected deduplicated USER.md but found {count} occurrences of {fact!r}"


def test_different_profile_lines_both_appended(tmp_path: Path) -> None:
    """Two distinct profile facts are both present in USER.md."""
    provider = _make_provider(tmp_path)

    asyncio.run(provider.remember({
        "body": "User prefers dark mode",
        "kind": "profile",
        "target_file": "USER.md",
    }))
    asyncio.run(provider.remember({
        "body": "User timezone is UTC+9",
        "kind": "profile",
        "target_file": "USER.md",
    }))

    content = (tmp_path / "USER.md").read_text(encoding="utf-8")
    assert "User prefers dark mode" in content
    assert "User timezone is UTC+9" in content


def test_dedup_only_applies_to_user_md_not_memory_md(tmp_path: Path) -> None:
    """MEMORY.md does NOT get profile deduplication — only USER.md does."""
    provider = _make_provider(tmp_path)

    fact = "User prefers dark mode"
    asyncio.run(provider.remember({
        "body": fact,
        "kind": "note",
        "target_file": "MEMORY.md",
    }))
    asyncio.run(provider.remember({
        "body": fact,
        "kind": "note",
        "target_file": "MEMORY.md",
    }))

    content = (tmp_path / "MEMORY.md").read_text(encoding="utf-8")
    # MEMORY.md allows duplicates (not profile-deduped)
    count = content.count(fact)
    assert count == 2, (
        f"MEMORY.md should allow duplicate writes but found {count} occurrences"
    )


# ---------------------------------------------------------------------------
# C. SOUL.md agent rejection
# ---------------------------------------------------------------------------


def test_agent_cannot_write_soul_md_via_provider(tmp_path: Path) -> None:
    """D1 allowlist: writing SOUL.md directly via provider raises ValueError."""
    provider = _make_provider(tmp_path)

    with pytest.raises(ValueError, match="unknown write target"):
        asyncio.run(provider.remember({
            "body": "new identity paragraph",
            "target_file": "SOUL.md",
        }))


def test_soul_md_not_in_allowed_write_files() -> None:
    """SOUL.md must NOT appear in _ALLOWED_WRITE_FILES."""
    from magi_agent.memory.adapters.local_file_writable import _ALLOWED_WRITE_FILES

    assert "SOUL.md" not in _ALLOWED_WRITE_FILES


def test_agent_tool_cannot_write_soul_md(tmp_path: Path) -> None:
    """D2 tool: target_file=SOUL.md is sanitized to MEMORY.md, not written as SOUL.md."""
    from magi_agent.harness.memory_write_tool import (
        MemoryWriteToolHostConfig,
        MemoryWriteToolHost,
    )
    from magi_agent.tools.registry import ToolRegistry
    from magi_agent.tools.catalog import register_core_tool_manifests
    from magi_agent.tools.context import ToolContext
    from magi_agent.memory.adapters.local_file_writable import (
        LocalFileMemoryConfig,
        LocalFileMemoryProvider,
        MAGI_MEMORY_WRITE_ENABLED_ENV,
    )

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
            {"fact": "User prefers dark mode", "target_file": "SOUL.md"},
            ctx,
        )

    result = asyncio.run(run())

    # The tool must either block or succeed — but SOUL.md must NEVER be created
    assert not (tmp_path / "SOUL.md").exists(), (
        "SOUL.md must not be created by the agent tool under any circumstances"
    )


def test_magi_memory_write_enabled_does_not_open_soul_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MAGI_MEMORY_WRITE_ENABLED=1 does NOT enable SOUL.md writes."""
    from magi_agent.memory.adapters.local_file_writable import (
        LocalFileMemoryProvider,
        LocalFileMemoryConfig,
        MAGI_MEMORY_WRITE_ENABLED_ENV,
    )

    monkeypatch.setenv(MAGI_MEMORY_WRITE_ENABLED_ENV, "1")
    provider = LocalFileMemoryProvider(
        LocalFileMemoryConfig(workspace_root=tmp_path, enabled=True)
    )

    with pytest.raises(ValueError, match="unknown write target"):
        asyncio.run(provider.remember({
            "body": "injected soul content",
            "target_file": "SOUL.md",
        }))

    assert not (tmp_path / "SOUL.md").exists()


# ---------------------------------------------------------------------------
# D. OperatorSoulWriter — separate authority, unreachable from agent path
# ---------------------------------------------------------------------------


def test_operator_soul_writer_exists_and_is_importable() -> None:
    """OperatorSoulWriter must be importable from the adapters package."""
    from magi_agent.memory.adapters.operator_soul_writer import OperatorSoulWriter  # noqa: F401


def test_operator_soul_writer_default_off(tmp_path: Path) -> None:
    """OperatorSoulWriter is default-off: operator_enabled=False → raises."""
    from magi_agent.memory.adapters.operator_soul_writer import (
        OperatorSoulWriter,
        OperatorSoulWriterConfig,
        OperatorSoulWriteDisabledError,
    )

    config = OperatorSoulWriterConfig(workspace_root=tmp_path)
    writer = OperatorSoulWriter(config)

    with pytest.raises(OperatorSoulWriteDisabledError):
        asyncio.run(writer.write_soul("new persona content"))


def test_operator_soul_writer_enabled_writes_soul_md(tmp_path: Path) -> None:
    """OperatorSoulWriter with operator_enabled=True writes SOUL.md."""
    from magi_agent.memory.adapters.operator_soul_writer import (
        OperatorSoulWriter,
        OperatorSoulWriterConfig,
    )

    config = OperatorSoulWriterConfig(
        workspace_root=tmp_path,
        operator_enabled=True,
    )
    writer = OperatorSoulWriter(config)

    asyncio.run(writer.write_soul("You are Magi, an autonomous agent."))

    soul_path = tmp_path / "SOUL.md"
    assert soul_path.exists()
    content = soul_path.read_text(encoding="utf-8")
    assert "Magi" in content


def test_operator_soul_writer_env_gate_separate_from_agent_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MAGI_MEMORY_WRITE_ENABLED=1 does NOT open the operator SOUL gate."""
    from magi_agent.memory.adapters.local_file_writable import MAGI_MEMORY_WRITE_ENABLED_ENV
    from magi_agent.memory.adapters.operator_soul_writer import (
        OperatorSoulWriter,
        OperatorSoulWriterConfig,
        OperatorSoulWriteDisabledError,
    )

    monkeypatch.setenv(MAGI_MEMORY_WRITE_ENABLED_ENV, "1")

    # Only MAGI_MEMORY_WRITE_ENABLED is set — operator gate remains closed
    config = OperatorSoulWriterConfig(workspace_root=tmp_path)  # operator_enabled=False
    writer = OperatorSoulWriter(config)

    with pytest.raises(OperatorSoulWriteDisabledError):
        asyncio.run(writer.write_soul("should not write"))

    assert not (tmp_path / "SOUL.md").exists()


def test_operator_soul_writer_env_gate_works(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MAGI_SOUL_WRITE_ENABLED=1 opens the operator gate."""
    from magi_agent.memory.adapters.operator_soul_writer import (
        OperatorSoulWriter,
        OperatorSoulWriterConfig,
        MAGI_SOUL_WRITE_ENABLED_ENV,
    )

    monkeypatch.setenv(MAGI_SOUL_WRITE_ENABLED_ENV, "1")

    config = OperatorSoulWriterConfig(workspace_root=tmp_path)
    writer = OperatorSoulWriter(config)

    asyncio.run(writer.write_soul("Operator-authored soul content"))

    content = (tmp_path / "SOUL.md").read_text(encoding="utf-8")
    assert "Operator-authored soul content" in content


def test_operator_soul_writer_redacts_secrets(tmp_path: Path) -> None:
    """Secrets are redacted even in operator SOUL writes."""
    from magi_agent.memory.adapters.operator_soul_writer import (
        OperatorSoulWriter,
        OperatorSoulWriterConfig,
    )

    config = OperatorSoulWriterConfig(workspace_root=tmp_path, operator_enabled=True)
    writer = OperatorSoulWriter(config)

    asyncio.run(writer.write_soul(
        "You are Magi. Your token is sk-live-abc12345678. Be helpful."
    ))

    content = (tmp_path / "SOUL.md").read_text(encoding="utf-8")
    assert "sk-live-abc12345678" not in content
    assert "Magi" in content


def test_operator_soul_writer_bounded_by_max_bytes(tmp_path: Path) -> None:
    """OperatorSoulWriter enforces a byte cap."""
    from magi_agent.memory.adapters.operator_soul_writer import (
        OperatorSoulWriter,
        OperatorSoulWriterConfig,
    )

    config = OperatorSoulWriterConfig(
        workspace_root=tmp_path,
        operator_enabled=True,
        max_write_bytes=20,
    )
    writer = OperatorSoulWriter(config)

    with pytest.raises(ValueError, match="exceeds"):
        asyncio.run(writer.write_soul("This body is much longer than twenty bytes."))

    assert not (tmp_path / "SOUL.md").exists()


def test_operator_soul_writer_is_not_local_file_memory_provider(tmp_path: Path) -> None:
    """OperatorSoulWriter is a distinct class from LocalFileMemoryProvider."""
    from magi_agent.memory.adapters.operator_soul_writer import OperatorSoulWriter
    from magi_agent.memory.adapters.local_file_writable import LocalFileMemoryProvider

    assert not issubclass(OperatorSoulWriter, LocalFileMemoryProvider)


def test_harness_attempt_real_write_cannot_use_operator_soul_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_attempt_real_write only accepts LocalFileMemoryProvider — not OperatorSoulWriter."""
    from magi_agent.memory.adapters.local_file_writable import MAGI_MEMORY_WRITE_ENABLED_ENV
    from magi_agent.memory.adapters.operator_soul_writer import (
        OperatorSoulWriter,
        OperatorSoulWriterConfig,
    )
    from magi_agent.harness.memory_write import MemoryWriteHarness, MemoryWriteHarnessConfig, MemoryWritePolicy, MemoryWriteRequest

    monkeypatch.setenv(MAGI_MEMORY_WRITE_ENABLED_ENV, "1")

    # Inject an OperatorSoulWriter as the "adapter" — it must be ignored by the harness
    config = OperatorSoulWriterConfig(workspace_root=tmp_path, operator_enabled=True)
    soul_writer = OperatorSoulWriter(config)

    harness_config = MemoryWriteHarnessConfig(enabled=True, localFakeAdapterEnabled=True)
    harness = MemoryWriteHarness(harness_config, adapter=soul_writer)  # type: ignore[arg-type]

    request = MemoryWriteRequest(
        providerId="test",
        turnId="turn-test",
        operation="remember",
        content="User prefers dark mode",
    )
    policy = MemoryWritePolicy(
        policyRef="policy:test",
        policySnapshotRef="policy:test-snap",
        evidenceRequired=False,
        localFakeSuccessAllowed=True,
    )

    result = asyncio.run(harness.write(request=request, policy=policy))

    # No real write must happen via soul writer — simulated or blocked
    assert not (tmp_path / "SOUL.md").exists()
    assert not (tmp_path / "MEMORY.md").exists()
    # The result status can be success (simulated) or blocked — either is fine,
    # as long as SOUL.md was never created
    assert result.status in {"success", "blocked", "disabled"}
    if result.status == "success":
        assert result.evidence_record is not None
        assert result.evidence_record.is_real_write is False


# ---------------------------------------------------------------------------
# E. Default-off inertness
# ---------------------------------------------------------------------------


def test_operator_soul_writer_default_off_no_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without any gate, OperatorSoulWriter is inert."""
    from magi_agent.memory.adapters.operator_soul_writer import (
        OperatorSoulWriter,
        OperatorSoulWriterConfig,
        OperatorSoulWriteDisabledError,
        MAGI_SOUL_WRITE_ENABLED_ENV,
    )

    monkeypatch.delenv(MAGI_SOUL_WRITE_ENABLED_ENV, raising=False)

    config = OperatorSoulWriterConfig(workspace_root=tmp_path)
    writer = OperatorSoulWriter(config)

    with pytest.raises(OperatorSoulWriteDisabledError):
        asyncio.run(writer.write_soul("test"))

    assert not (tmp_path / "SOUL.md").exists()


# ---------------------------------------------------------------------------
# F. D3 projection still covers USER.md after D4
# ---------------------------------------------------------------------------


def test_d3_projection_includes_user_md_profile_after_d4_write(tmp_path: Path) -> None:
    """D3 projection reflects USER.md content written through the D4-refined path."""
    from magi_agent.memory.prompt_projection import MemoryPromptProjector

    (tmp_path / "USER.md").write_text(
        "# User Profile\n\n- [profile] User prefers Korean for discussions\n",
        encoding="utf-8",
    )

    projector = MemoryPromptProjector(tmp_path, enabled=True)
    result = projector.project(memory_mode="normal")

    assert result.enabled is True
    assert "Korean" in result.snapshot_block
    assert "USER.md" in result.files_loaded


def test_d3_projection_with_both_memory_and_user_md(tmp_path: Path) -> None:
    """D3 projection loads both MEMORY.md and USER.md independently."""
    from magi_agent.memory.prompt_projection import MemoryPromptProjector

    (tmp_path / "MEMORY.md").write_text(
        "# Memory\n\n- [note] Project uses TypeScript\n",
        encoding="utf-8",
    )
    (tmp_path / "USER.md").write_text(
        "# User Profile\n\n- [profile] User prefers Python\n",
        encoding="utf-8",
    )

    projector = MemoryPromptProjector(tmp_path, enabled=True)
    result = projector.project(memory_mode="normal")

    assert "TypeScript" in result.snapshot_block
    assert "Python" in result.snapshot_block
    assert "MEMORY.md" in result.files_loaded
    assert "USER.md" in result.files_loaded
