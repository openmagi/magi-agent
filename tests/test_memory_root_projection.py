"""PR-B — ROOT.md (+ recent daily) surfaced into the projected memory snapshot.

The 5-level compaction tree synthesizes ``memory/ROOT.md``; this test proves the
projection path now reads it (and the freshest raw daily files) so the tree's
output actually reaches the model on the next session.

  1. ROOT.md content appears in the snapshot when projection gate ON
  2. recent daily files appear too (newest-first, bounded)
  3. gate OFF => no projection at all (ROOT not leaked)
  4. absent ROOT.md => degrades to MEMORY.md/USER.md-only (no crash)
"""
from __future__ import annotations

from pathlib import Path

import pytest

from magi_agent.memory.prompt_projection import (
    MAGI_MEMORY_PROJECTION_ENABLED_ENV,
    _recent_daily_rel_paths,
    project_memory_snapshot,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_root_md_surfaced_when_gate_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(MAGI_MEMORY_PROJECTION_ENABLED_ENV, "1")
    _write(tmp_path / "MEMORY.md", "- known fact alpha")
    _write(
        tmp_path / "memory" / "ROOT.md",
        "# Memory Root (synthesized)\n\n## Active Context\nROOT_SENTINEL_PHRASE here\n",
    )

    result = project_memory_snapshot(workspace_root=tmp_path)
    assert result.enabled is True
    assert "ROOT_SENTINEL_PHRASE" in result.snapshot_block
    assert "known fact alpha" in result.snapshot_block
    assert "memory/ROOT.md" in result.files_loaded


def test_recent_daily_surfaced_newest_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(MAGI_MEMORY_PROJECTION_ENABLED_ENV, "1")
    _write(tmp_path / "memory" / "daily" / "2026-06-06.md", "OLD_DAY content")
    _write(tmp_path / "memory" / "daily" / "2026-06-07.md", "MID_DAY content")
    _write(tmp_path / "memory" / "daily" / "2026-06-08.md", "NEW_DAY content")

    # helper returns the 2 newest, newest-first
    rels = _recent_daily_rel_paths(tmp_path)
    assert rels == ["memory/daily/2026-06-08.md", "memory/daily/2026-06-07.md"]

    result = project_memory_snapshot(workspace_root=tmp_path)
    assert "NEW_DAY content" in result.snapshot_block
    assert "MID_DAY content" in result.snapshot_block
    # the 3rd-oldest is beyond the recent window
    assert "OLD_DAY content" not in result.snapshot_block


def test_gate_off_does_not_leak_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(MAGI_MEMORY_PROJECTION_ENABLED_ENV, raising=False)
    _write(tmp_path / "memory" / "ROOT.md", "ROOT_SENTINEL_PHRASE")

    result = project_memory_snapshot(workspace_root=tmp_path)
    assert result.enabled is False
    assert result.snapshot_block == ""


def test_absent_root_degrades_to_memory_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(MAGI_MEMORY_PROJECTION_ENABLED_ENV, "1")
    _write(tmp_path / "MEMORY.md", "- only fact")

    result = project_memory_snapshot(workspace_root=tmp_path)
    assert result.enabled is True
    assert "only fact" in result.snapshot_block
    assert "memory/ROOT.md" not in result.files_loaded


def test_recent_daily_empty_when_no_dir(tmp_path: Path) -> None:
    assert _recent_daily_rel_paths(tmp_path) == []


def test_oversized_root_does_not_evict_curated_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Budget priority: a ROOT.md large enough to fill the whole budget must
    NOT starve the curated MEMORY.md / USER.md — those are budgeted first."""
    monkeypatch.setenv(MAGI_MEMORY_PROJECTION_ENABLED_ENV, "1")
    _write(tmp_path / "MEMORY.md", "MEMORY_SENTINEL curated fact alpha")
    _write(tmp_path / "USER.md", "USER_SENTINEL curated profile beta")
    # ROOT.md bigger than the whole snapshot budget (8 KiB) so, if it led the
    # snapshot, it would consume the entire budget and evict the curated files.
    _write(
        tmp_path / "memory" / "ROOT.md",
        "ROOT_FILLER line of synthesized history\n" * 1000,
    )

    result = project_memory_snapshot(workspace_root=tmp_path)
    assert result.enabled is True
    # Curated files survive in full despite the oversized ROOT.
    assert "MEMORY_SENTINEL" in result.snapshot_block
    assert "USER_SENTINEL" in result.snapshot_block
    assert "MEMORY.md" in result.files_loaded
    assert "USER.md" in result.files_loaded
    # The block still respects the byte budget (ROOT shrank, not the curated).
    assert result.bytes_used <= result.bytes_budget
