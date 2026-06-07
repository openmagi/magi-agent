"""D1 — Gated writable memory contract + LocalFileMemoryProvider.

TDD tests written before implementation.  All tests must pass after D1 lands.
Tests are grouped into four categories:

A. Invariant: read-only default still raises on supports_write=True.
B. Gated tier: allows bounded write when explicitly authorized.
C. LocalFileMemoryProvider: read path works; gated write appends to disk.
D. Gate-off inertness: provider defaults to read-only when write is not enabled.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from magi_agent.memory.contracts import (
    MemoryProviderCapabilities,
    RecallRequest,
    UnsupportedMemoryOperationError,
)
from magi_agent.memory.policy import MemoryPolicy


# ---------------------------------------------------------------------------
# A. Invariant: read-only default still raises on supports_write=True
# ---------------------------------------------------------------------------


def test_default_capabilities_raise_on_supports_write_true() -> None:
    """The existing invariant must be byte-identical after D1."""
    with pytest.raises(ValueError, match="read-only"):
        MemoryProviderCapabilities(
            provider_id="test-provider",
            storage_model="file",
            supports_write=True,
        )


def test_default_capabilities_raise_on_nonzero_max_write_bytes() -> None:
    """max_write_bytes != 0 with default tier raises unchanged."""
    with pytest.raises(ValueError):
        MemoryProviderCapabilities(
            provider_id="test-provider",
            storage_model="file",
            max_write_bytes=1024,
        )


def test_default_capabilities_raise_on_delete_support() -> None:
    """supports_delete != 'none' still raises."""
    with pytest.raises(ValueError, match="delete"):
        MemoryProviderCapabilities(
            provider_id="test-provider",
            storage_model="file",
            supports_delete="soft",
        )


def test_default_tier_capabilities_are_read_only() -> None:
    """Default construction — no write_tier kwarg — stays fully read-only."""
    caps = MemoryProviderCapabilities(
        provider_id="hipocampus-qmd-readonly",
        storage_model="file",
        supports_search=True,
        supports_export=True,
    )
    assert caps.supports_write is False
    assert caps.max_write_bytes == 0
    assert caps.supports_delete == "none"


# ---------------------------------------------------------------------------
# B. Gated tier: allows bounded write when explicitly authorized
# ---------------------------------------------------------------------------


def test_gated_write_tier_allows_supports_write_true_with_bounded_bytes() -> None:
    """write_tier='gated_write' unlocks supports_write + positive max_write_bytes."""
    caps = MemoryProviderCapabilities(
        provider_id="local-file-writable",
        storage_model="file",
        supports_write=True,
        max_write_bytes=65_536,
        write_tier="gated_write",
    )
    assert caps.supports_write is True
    assert caps.max_write_bytes == 65_536
    assert caps.write_tier == "gated_write"


def test_gated_write_tier_still_forbids_delete() -> None:
    """Even with gated_write, supports_delete must remain 'none'."""
    with pytest.raises(ValueError, match="delete"):
        MemoryProviderCapabilities(
            provider_id="local-file-writable",
            storage_model="file",
            supports_write=True,
            max_write_bytes=4_096,
            write_tier="gated_write",
            supports_delete="soft",
        )


def test_gated_write_tier_requires_positive_max_write_bytes() -> None:
    """gated_write with max_write_bytes=0 is incoherent and should raise."""
    with pytest.raises(ValueError, match="max_write_bytes"):
        MemoryProviderCapabilities(
            provider_id="local-file-writable",
            storage_model="file",
            supports_write=True,
            max_write_bytes=0,
            write_tier="gated_write",
        )


def test_read_only_tier_still_rejects_supports_write_true_even_if_explicitly_set() -> None:
    """write_tier='read_only' is still the default; supports_write=True must raise."""
    with pytest.raises(ValueError, match="read-only"):
        MemoryProviderCapabilities(
            provider_id="test-provider",
            storage_model="file",
            supports_write=True,
            write_tier="read_only",
        )


# ---------------------------------------------------------------------------
# C. LocalFileMemoryProvider — read path
# ---------------------------------------------------------------------------


def _write_local_memory_fixtures(root: Path) -> None:
    (root / "MEMORY.md").write_text(
        "# Memory\n\nUser prefers dark mode. Budget is 5000.\n",
        encoding="utf-8",
    )
    (root / "USER.md").write_text(
        "# User Profile\n\nName: Alice. Role: developer.\n",
        encoding="utf-8",
    )


def test_local_file_provider_read_path_loads_memory_md(tmp_path: Path) -> None:
    """recall() on gate-off provider reads MEMORY.md without writing."""
    from magi_agent.memory.adapters.local_file_writable import (
        LocalFileMemoryProvider,
        LocalFileMemoryConfig,
    )

    _write_local_memory_fixtures(tmp_path)
    config = LocalFileMemoryConfig(workspace_root=tmp_path, enabled=True)
    provider = LocalFileMemoryProvider(config)

    result = asyncio.run(
        provider.recall(
            RecallRequest(
                scope={"tenantId": "t1", "botId": "b1"},
                query="dark mode",
                purpose="answer_user",
            ),
            policy=MemoryPolicy(memory_mode="normal", source_authority="long_term_allowed"),
        )
    )

    assert result.recall_allowed is True
    assert result.write_allowed is False
    assert result.prompt_projection_allowed is False
    assert any("dark mode" in record.body for record in result.records)


def test_local_file_provider_read_path_loads_user_md(tmp_path: Path) -> None:
    """recall() also surfaces USER.md entries."""
    from magi_agent.memory.adapters.local_file_writable import (
        LocalFileMemoryProvider,
        LocalFileMemoryConfig,
    )

    _write_local_memory_fixtures(tmp_path)
    config = LocalFileMemoryConfig(workspace_root=tmp_path, enabled=True)
    provider = LocalFileMemoryProvider(config)

    result = asyncio.run(
        provider.recall(
            RecallRequest(
                scope={"tenantId": "t1", "botId": "b1"},
                query="Alice developer",
                purpose="answer_user",
            ),
            policy=MemoryPolicy(memory_mode="normal", source_authority="long_term_allowed"),
        )
    )

    assert any("Alice" in record.body or "developer" in record.body for record in result.records)


def test_local_file_provider_returns_empty_when_disabled(tmp_path: Path) -> None:
    """When enabled=False the provider returns an empty recall result."""
    from magi_agent.memory.adapters.local_file_writable import (
        LocalFileMemoryProvider,
        LocalFileMemoryConfig,
    )

    _write_local_memory_fixtures(tmp_path)
    config = LocalFileMemoryConfig(workspace_root=tmp_path, enabled=False)
    provider = LocalFileMemoryProvider(config)

    result = asyncio.run(
        provider.recall(
            RecallRequest(
                scope={"tenantId": "t1", "botId": "b1"},
                query="anything",
                purpose="answer_user",
            ),
            policy=MemoryPolicy(memory_mode="normal", source_authority="long_term_allowed"),
        )
    )

    assert result.records == ()
    assert "adapter_disabled" in result.reason_codes


# ---------------------------------------------------------------------------
# C. LocalFileMemoryProvider — gated write path (append / update)
# ---------------------------------------------------------------------------


def test_local_file_provider_gate_off_remember_raises(tmp_path: Path) -> None:
    """When write is NOT enabled, remember() raises UnsupportedMemoryOperationError."""
    from magi_agent.memory.adapters.local_file_writable import (
        LocalFileMemoryProvider,
        LocalFileMemoryConfig,
    )

    config = LocalFileMemoryConfig(workspace_root=tmp_path, enabled=True, write_enabled=False)
    provider = LocalFileMemoryProvider(config)

    with pytest.raises(UnsupportedMemoryOperationError):
        asyncio.run(provider.remember({"body": "should not be written"}))


def test_local_file_provider_gated_write_appends_to_memory_md(tmp_path: Path) -> None:
    """With write_enabled=True and MAGI_MEMORY_WRITE_ENABLED, remember() appends."""
    from magi_agent.memory.adapters.local_file_writable import (
        LocalFileMemoryProvider,
        LocalFileMemoryConfig,
    )

    config = LocalFileMemoryConfig(workspace_root=tmp_path, enabled=True, write_enabled=True)
    provider = LocalFileMemoryProvider(config)

    asyncio.run(provider.remember({
        "body": "User prefers Vim keybindings.",
        "kind": "preference",
        "scope": "user",
        "target_file": "MEMORY.md",
    }))

    memory_path = tmp_path / "MEMORY.md"
    assert memory_path.exists()
    content = memory_path.read_text(encoding="utf-8")
    assert "Vim keybindings" in content


def test_local_file_provider_gated_write_appends_to_user_md(tmp_path: Path) -> None:
    """With write_enabled=True, remember() with target_file=USER.md writes USER.md."""
    from magi_agent.memory.adapters.local_file_writable import (
        LocalFileMemoryProvider,
        LocalFileMemoryConfig,
    )

    config = LocalFileMemoryConfig(workspace_root=tmp_path, enabled=True, write_enabled=True)
    provider = LocalFileMemoryProvider(config)

    asyncio.run(provider.remember({
        "body": "User is based in Seoul.",
        "kind": "fact",
        "scope": "user",
        "target_file": "USER.md",
    }))

    user_path = tmp_path / "USER.md"
    assert user_path.exists()
    content = user_path.read_text(encoding="utf-8")
    assert "Seoul" in content


def test_local_file_provider_gated_write_is_retrievable(tmp_path: Path) -> None:
    """After a gated write, recall() returns the appended entry."""
    from magi_agent.memory.adapters.local_file_writable import (
        LocalFileMemoryProvider,
        LocalFileMemoryConfig,
    )

    config = LocalFileMemoryConfig(workspace_root=tmp_path, enabled=True, write_enabled=True)
    provider = LocalFileMemoryProvider(config)

    asyncio.run(provider.remember({
        "body": "Favorite tool is ripgrep.",
        "kind": "preference",
        "target_file": "MEMORY.md",
    }))

    result = asyncio.run(
        provider.recall(
            RecallRequest(
                scope={"tenantId": "t1", "botId": "b1"},
                query="ripgrep",
                purpose="answer_user",
            ),
            policy=MemoryPolicy(memory_mode="normal", source_authority="long_term_allowed"),
        )
    )

    assert any("ripgrep" in record.body for record in result.records)


def test_local_file_provider_write_enforces_max_write_bytes(tmp_path: Path) -> None:
    """remember() rejects payloads exceeding max_write_bytes."""
    from magi_agent.memory.adapters.local_file_writable import (
        LocalFileMemoryProvider,
        LocalFileMemoryConfig,
    )

    config = LocalFileMemoryConfig(
        workspace_root=tmp_path,
        enabled=True,
        write_enabled=True,
        max_write_bytes=10,  # tiny cap
    )
    provider = LocalFileMemoryProvider(config)

    with pytest.raises((ValueError, UnsupportedMemoryOperationError)):
        asyncio.run(provider.remember({
            "body": "This body is definitely longer than ten bytes.",
            "kind": "note",
        }))


def test_local_file_provider_write_redacts_secrets_before_persisting(tmp_path: Path) -> None:
    """Secrets in the body must be redacted before writing to disk."""
    from magi_agent.memory.adapters.local_file_writable import (
        LocalFileMemoryProvider,
        LocalFileMemoryConfig,
    )

    config = LocalFileMemoryConfig(workspace_root=tmp_path, enabled=True, write_enabled=True)
    provider = LocalFileMemoryProvider(config)

    asyncio.run(provider.remember({
        "body": "API key is sk-live-supersecretkey12345 and token is ghp_ABCDEFGHIJ0123456789",
        "kind": "note",
        "target_file": "MEMORY.md",
    }))

    content = (tmp_path / "MEMORY.md").read_text(encoding="utf-8")
    assert "sk-live-supersecretkey12345" not in content
    assert "ghp_ABCDEFGHIJ0123456789" not in content


def test_local_file_provider_delete_always_raises(tmp_path: Path) -> None:
    """delete() raises regardless of write_enabled — no destructive operations."""
    from magi_agent.memory.adapters.local_file_writable import (
        LocalFileMemoryProvider,
        LocalFileMemoryConfig,
    )

    config = LocalFileMemoryConfig(workspace_root=tmp_path, enabled=True, write_enabled=True)
    provider = LocalFileMemoryProvider(config)

    with pytest.raises(UnsupportedMemoryOperationError):
        asyncio.run(provider.delete("some-record-id"))


def test_local_file_provider_capabilities_declare_gated_write_when_enabled(
    tmp_path: Path,
) -> None:
    """capabilities() returns write_tier='gated_write' when write is enabled."""
    from magi_agent.memory.adapters.local_file_writable import (
        LocalFileMemoryProvider,
        LocalFileMemoryConfig,
    )

    config = LocalFileMemoryConfig(workspace_root=tmp_path, enabled=True, write_enabled=True)
    provider = LocalFileMemoryProvider(config)

    caps = provider.capabilities()
    assert caps.supports_write is True
    assert caps.write_tier == "gated_write"
    assert caps.max_write_bytes > 0


def test_local_file_provider_capabilities_are_read_only_when_write_disabled(
    tmp_path: Path,
) -> None:
    """When write_enabled=False, capabilities() reports supports_write=False."""
    from magi_agent.memory.adapters.local_file_writable import (
        LocalFileMemoryProvider,
        LocalFileMemoryConfig,
    )

    config = LocalFileMemoryConfig(workspace_root=tmp_path, enabled=True, write_enabled=False)
    provider = LocalFileMemoryProvider(config)

    caps = provider.capabilities()
    assert caps.supports_write is False
    assert caps.write_tier == "read_only"
    assert caps.max_write_bytes == 0


# ---------------------------------------------------------------------------
# D. Gate-off via env: MAGI_MEMORY_WRITE_ENABLED not set → write inert
# ---------------------------------------------------------------------------


def test_local_file_provider_env_gate_off_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without MAGI_MEMORY_WRITE_ENABLED=1, write is inert even if not explicitly set."""
    from magi_agent.memory.adapters.local_file_writable import (
        LocalFileMemoryProvider,
        LocalFileMemoryConfig,
        MAGI_MEMORY_WRITE_ENABLED_ENV,
    )

    monkeypatch.delenv(MAGI_MEMORY_WRITE_ENABLED_ENV, raising=False)
    # Config does not set write_enabled — default should be False
    config = LocalFileMemoryConfig(workspace_root=tmp_path, enabled=True)
    provider = LocalFileMemoryProvider(config)

    with pytest.raises(UnsupportedMemoryOperationError):
        asyncio.run(provider.remember({"body": "should be blocked"}))


def test_local_file_provider_env_gate_on_enables_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With MAGI_MEMORY_WRITE_ENABLED=1, write is live (env-driven gate)."""
    from magi_agent.memory.adapters.local_file_writable import (
        LocalFileMemoryProvider,
        LocalFileMemoryConfig,
        MAGI_MEMORY_WRITE_ENABLED_ENV,
    )

    monkeypatch.setenv(MAGI_MEMORY_WRITE_ENABLED_ENV, "1")
    config = LocalFileMemoryConfig(workspace_root=tmp_path, enabled=True)
    provider = LocalFileMemoryProvider(config)

    asyncio.run(provider.remember({
        "body": "Env-gated write should land.",
        "target_file": "MEMORY.md",
    }))

    content = (tmp_path / "MEMORY.md").read_text(encoding="utf-8")
    assert "Env-gated write should land" in content
