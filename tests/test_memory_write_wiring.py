"""Task 2.1 — Gate-aware write-host factory.

Tests for ``magi_agent.runtime.memory_write_wiring.build_memory_write_host``.

  (a) No readiness env → host.config.enabled is False
  (b) Shadow mode env → enabled True, provider None
  (c) Live mode (local-dev short-circuit) → enabled True, provider is LocalFileMemoryProvider
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest


def _sha256_text_digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _make_shadow_config(*, bot_id: str, user_id: str):
    """Build a MemoryWriteReadinessConfig that resolves to 'shadow'."""
    from magi_agent.gates.memory_write_readiness import MemoryWriteReadinessConfig

    return MemoryWriteReadinessConfig(
        enabled=True,
        killSwitchEnabled=False,
        shadowModeEnabled=True,
        selectedBotDigest=_sha256_text_digest(bot_id),
        selectedOwnerUserIdDigest=_sha256_text_digest(user_id),
        environment="local",
        environmentAllowlist=("local",),
        promotedGate=0,
        canaryPromotionConfirmed=False,
    )


def _make_live_config(*, bot_id: str, user_id: str):
    """Build a MemoryWriteReadinessConfig that resolves to 'live'."""
    from magi_agent.gates.memory_write_readiness import MemoryWriteReadinessConfig

    return MemoryWriteReadinessConfig(
        enabled=True,
        killSwitchEnabled=False,
        shadowModeEnabled=True,
        selectedBotDigest=_sha256_text_digest(bot_id),
        selectedOwnerUserIdDigest=_sha256_text_digest(user_id),
        environment="local",
        environmentAllowlist=("local",),
        promotedGate=5,
        canaryPromotionConfirmed=True,
    )


# ---------------------------------------------------------------------------
# (a) No readiness env → host.config.enabled is False
# ---------------------------------------------------------------------------


def test_build_memory_write_host_disabled_when_no_readiness_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When no readiness env is set, the returned host must be disabled."""
    monkeypatch.delenv("MAGI_MEMORY_WRITE_READINESS_ENABLED", raising=False)
    monkeypatch.delenv("MAGI_MEMORY_LOCAL_DEV", raising=False)

    from magi_agent.runtime.memory_write_wiring import build_memory_write_host

    host = build_memory_write_host(
        workspace_root=tmp_path,
        bot_id="bot-test",
        user_id="user-test",
    )

    assert host.config.enabled is False
    assert host.provider is None


# ---------------------------------------------------------------------------
# (b) Shadow mode env → enabled True, provider None
# ---------------------------------------------------------------------------


def test_build_memory_write_host_shadow_mode_enabled_no_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Shadow mode: host.config.enabled=True but provider is None."""
    bot_id = "bot-shadow"
    user_id = "user-shadow"

    monkeypatch.setenv("MAGI_MEMORY_WRITE_READINESS_ENABLED", "1")
    monkeypatch.delenv("MAGI_MEMORY_LOCAL_DEV", raising=False)

    from magi_agent.runtime.memory_write_wiring import build_memory_write_host

    shadow_config = _make_shadow_config(bot_id=bot_id, user_id=user_id)
    host = build_memory_write_host(
        workspace_root=tmp_path,
        bot_id=bot_id,
        user_id=user_id,
        readiness_config=shadow_config,
    )

    assert host.config.enabled is True
    assert host.provider is None


# ---------------------------------------------------------------------------
# (c) Live mode (local-dev short-circuit) → enabled True, provider is LocalFileMemoryProvider
# ---------------------------------------------------------------------------


def test_build_memory_write_host_live_mode_has_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Live mode: host.config.enabled=True and provider is a LocalFileMemoryProvider."""
    from magi_agent.memory.adapters.local_file_writable import LocalFileMemoryProvider
    from magi_agent.runtime.memory_write_wiring import build_memory_write_host

    monkeypatch.setenv("MAGI_MEMORY_WRITE_READINESS_ENABLED", "1")
    monkeypatch.setenv("MAGI_MEMORY_WRITE_ENABLED", "1")
    monkeypatch.setenv("MAGI_MEMORY_LOCAL_DEV", "1")

    host = build_memory_write_host(
        workspace_root=tmp_path,
        bot_id="bot-live",
        user_id="user-live",
    )

    assert host.config.enabled is True
    assert isinstance(host.provider, LocalFileMemoryProvider)
    # The provider's write gate must also be active
    assert host.provider._write_active is True


def test_build_memory_write_host_live_config_no_local_dev_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Live via canary-promoted config (no local-dev env) also yields a provider."""
    from magi_agent.memory.adapters.local_file_writable import LocalFileMemoryProvider
    from magi_agent.runtime.memory_write_wiring import build_memory_write_host

    bot_id = "bot-canary"
    user_id = "user-canary"

    monkeypatch.setenv("MAGI_MEMORY_WRITE_READINESS_ENABLED", "1")
    monkeypatch.setenv("MAGI_MEMORY_WRITE_ENABLED", "1")
    monkeypatch.delenv("MAGI_MEMORY_LOCAL_DEV", raising=False)

    live_config = _make_live_config(bot_id=bot_id, user_id=user_id)
    host = build_memory_write_host(
        workspace_root=tmp_path,
        bot_id=bot_id,
        user_id=user_id,
        readiness_config=live_config,
    )

    assert host.config.enabled is True
    assert isinstance(host.provider, LocalFileMemoryProvider)
