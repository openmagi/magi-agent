"""Tests for MemorySnapshotCache — session-scoped frozen snapshot cache.

Contract:
  (a) Computes once and reuses across calls for the same session key.
  (b) Gate off (env unset) → returns "".
  (c) memory_mode="incognito" → returns "".
  (d) After invalidate, a changed MEMORY.md is picked up on the next get.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from magi_agent.runtime.memory_snapshot_cache import MemorySnapshotCache


MEMORY_PROJECTION_ENV = "MAGI_MEMORY_PROJECTION_ENABLED"


# ---------------------------------------------------------------------------
# (a) Computes once and reuses
# ---------------------------------------------------------------------------


def test_get_calls_compute_once_and_caches(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """get() calls _compute only once for the same session key."""
    (tmp_path / "MEMORY.md").write_text("# Memory\nSome content.", encoding="utf-8")
    monkeypatch.setenv(MEMORY_PROJECTION_ENV, "1")

    cache = MemorySnapshotCache(workspace_root=tmp_path)

    compute_calls: list[str] = []
    original_compute = cache._compute

    def counting_compute(*, memory_mode: str = "normal") -> str:
        compute_calls.append(memory_mode)
        return original_compute(memory_mode=memory_mode)

    cache._compute = counting_compute  # type: ignore[method-assign]

    block1 = cache.get("session-abc", memory_mode="normal")
    block2 = cache.get("session-abc", memory_mode="normal")

    assert block1 == block2
    assert len(compute_calls) == 1, f"Expected 1 compute call, got {len(compute_calls)}"


def test_different_sessions_compute_independently(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Different session keys each get their own compute call."""
    (tmp_path / "MEMORY.md").write_text("# Memory\nSome content.", encoding="utf-8")
    monkeypatch.setenv(MEMORY_PROJECTION_ENV, "1")

    cache = MemorySnapshotCache(workspace_root=tmp_path)

    compute_calls: list[str] = []
    original_compute = cache._compute

    def counting_compute(*, memory_mode: str = "normal") -> str:
        compute_calls.append(memory_mode)
        return original_compute(memory_mode=memory_mode)

    cache._compute = counting_compute  # type: ignore[method-assign]

    cache.get("session-x", memory_mode="normal")
    cache.get("session-y", memory_mode="normal")

    # Two different sessions → two compute calls
    assert len(compute_calls) == 2


# ---------------------------------------------------------------------------
# (b) Gate off → returns ""
# ---------------------------------------------------------------------------


def test_gate_off_returns_empty_string(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """When MAGI_MEMORY_PROJECTION_ENABLED is unset, get() returns ''."""
    (tmp_path / "MEMORY.md").write_text("# Memory\nSome content.", encoding="utf-8")
    monkeypatch.delenv(MEMORY_PROJECTION_ENV, raising=False)

    cache = MemorySnapshotCache(workspace_root=tmp_path)
    result = cache.get("session-abc", memory_mode="normal")

    assert result == ""


# ---------------------------------------------------------------------------
# (c) memory_mode="incognito" → returns ""
# ---------------------------------------------------------------------------


def test_incognito_mode_returns_empty_string(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """memory_mode='incognito' returns '' even when gate is on."""
    (tmp_path / "MEMORY.md").write_text("# Memory\nSome content.", encoding="utf-8")
    monkeypatch.setenv(MEMORY_PROJECTION_ENV, "1")

    cache = MemorySnapshotCache(workspace_root=tmp_path)
    result = cache.get("session-abc", memory_mode="incognito")

    assert result == ""


def test_incognito_mode_does_not_compute_snapshot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Incognito must bypass projection before workspace path validation."""
    monkeypatch.setenv(MEMORY_PROJECTION_ENV, "1")

    cache = MemorySnapshotCache(workspace_root=tmp_path)

    def fail_compute(*, memory_mode: str = "normal") -> str:
        raise AssertionError("incognito mode should not compute memory snapshots")

    cache._compute = fail_compute  # type: ignore[method-assign]

    assert cache.get("session-abc", memory_mode="incognito") == ""


# ---------------------------------------------------------------------------
# (d) After invalidate, changed MEMORY.md is picked up
# ---------------------------------------------------------------------------


def test_invalidate_clears_cache_and_picks_up_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """After invalidate(), the next get() re-reads MEMORY.md."""
    mem_file = tmp_path / "MEMORY.md"
    mem_file.write_text("# Memory\nOriginal content.", encoding="utf-8")
    monkeypatch.setenv(MEMORY_PROJECTION_ENV, "1")

    cache = MemorySnapshotCache(workspace_root=tmp_path)

    block1 = cache.get("session-abc", memory_mode="normal")
    assert "Original content" in block1

    # Update the file
    mem_file.write_text("# Memory\nUpdated content.", encoding="utf-8")

    # Without invalidate — should still return cached value
    block2 = cache.get("session-abc", memory_mode="normal")
    assert block2 == block1, "Expected cached value before invalidate"

    # Invalidate and re-fetch
    cache.invalidate("session-abc")
    block3 = cache.get("session-abc", memory_mode="normal")

    assert "Updated content" in block3
    assert block3 != block1


def test_invalidate_only_drops_matching_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """invalidate('A') does not clear the cache for session 'B'."""
    (tmp_path / "MEMORY.md").write_text("# Memory\nContent.", encoding="utf-8")
    monkeypatch.setenv(MEMORY_PROJECTION_ENV, "1")

    cache = MemorySnapshotCache(workspace_root=tmp_path)

    compute_calls: list[str] = []
    original_compute = cache._compute

    def counting_compute(*, memory_mode: str = "normal") -> str:
        compute_calls.append(memory_mode)
        return original_compute(memory_mode=memory_mode)

    cache._compute = counting_compute  # type: ignore[method-assign]

    cache.get("session-a", memory_mode="normal")
    cache.get("session-b", memory_mode="normal")
    assert len(compute_calls) == 2

    # Invalidate only session-a
    cache.invalidate("session-a")

    # session-b still cached — no new compute
    cache.get("session-b", memory_mode="normal")
    assert len(compute_calls) == 2, "session-b should still be cached"

    # session-a re-computes
    cache.get("session-a", memory_mode="normal")
    assert len(compute_calls) == 3, "session-a should recompute after invalidate"
