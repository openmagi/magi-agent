"""Task 6.2 — Gated archive-then-write compaction in LocalFileMemoryProvider.

The compactor from Task 6.1 is wired into ``remember()`` behind the env gate
``MAGI_MEMORY_COMPACTION_ENABLED`` (default OFF). When OFF the provider behaves
EXACTLY as today (no compaction; existing ``max_file_bytes`` enforcement
unchanged). When ON and the file is over ``compaction_threshold_bytes``, the
original is archived under ``<workspace>/memory/archive/`` (deterministic
content-hash suffix) and the consolidated text (<= cap) is written back before
the new entry appends.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from magi_agent.memory.adapters.local_file_writable import (
    LocalFileMemoryConfig,
    LocalFileMemoryProvider,
)

MAGI_MEMORY_COMPACTION_ENABLED_ENV = "MAGI_MEMORY_COMPACTION_ENABLED"


def _provider(
    tmp_path: Path,
    *,
    max_file_bytes: int = 4_194_304,
    compaction_threshold_bytes: int | None = None,
) -> LocalFileMemoryProvider:
    kwargs: dict[str, object] = {
        "workspaceRoot": tmp_path,
        "enabled": True,
        "writeEnabled": True,
        "maxFileBytes": max_file_bytes,
        # generous per-append cap so the file-size logic is what's exercised
        "maxWriteBytes": 65_536,
    }
    if compaction_threshold_bytes is not None:
        kwargs["compactionThresholdBytes"] = compaction_threshold_bytes
    return LocalFileMemoryProvider(LocalFileMemoryConfig(**kwargs))


def _archive_dir(tmp_path: Path) -> Path:
    return tmp_path / "memory" / "archive"


# ---------------------------------------------------------------------------
# (a) gate OFF → no compaction even when large (existing behavior preserved)
# ---------------------------------------------------------------------------


def test_gate_off_no_compaction_even_when_large(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv(MAGI_MEMORY_COMPACTION_ENABLED_ENV, raising=False)
    target = tmp_path / "MEMORY.md"
    # Pre-fill the file well over a small threshold with duplicate entries.
    prefill = "".join(f"\n- [note] dup-line\n" for _ in range(20))
    target.write_text(prefill, encoding="utf-8")
    before = target.read_text(encoding="utf-8")

    provider = _provider(
        tmp_path, max_file_bytes=4_194_304, compaction_threshold_bytes=50
    )
    asyncio.run(provider.remember({"body": "new fact", "kind": "note"}))

    after = target.read_text(encoding="utf-8")
    # Gate OFF: original content preserved verbatim, just appended to.
    assert after.startswith(before)
    assert "new fact" in after
    # No archive created.
    assert not _archive_dir(tmp_path).exists()


def test_gate_off_max_file_bytes_still_enforced(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv(MAGI_MEMORY_COMPACTION_ENABLED_ENV, raising=False)
    target = tmp_path / "MEMORY.md"
    target.write_text("- [note] " + ("x" * 200) + "\n", encoding="utf-8")
    provider = _provider(tmp_path, max_file_bytes=210)
    # Append would exceed max_file_bytes; gate-off must still raise as today.
    with pytest.raises(ValueError, match="max_file_bytes"):
        asyncio.run(provider.remember({"body": "overflow", "kind": "note"}))
    assert not _archive_dir(tmp_path).exists()


# ---------------------------------------------------------------------------
# (b) gate ON + over threshold → archive + consolidate + append succeeds
# ---------------------------------------------------------------------------


def test_gate_on_over_threshold_archives_and_consolidates(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv(MAGI_MEMORY_COMPACTION_ENABLED_ENV, "1")
    target = tmp_path / "MEMORY.md"
    # Distinct facts + duplicates, over a small threshold.
    lines = [f"\n- [note] fact-{i:02d}\n" for i in range(10)]
    dups = ["\n- [note] fact-00\n", "\n- [note] fact-01\n"]  # exact dups
    prefill = "".join(lines + dups)
    target.write_text(prefill, encoding="utf-8")

    # Threshold small enough to trigger; max_file_bytes large enough the cap is
    # the threshold, forcing dedup but no unique-fact loss.
    provider = _provider(
        tmp_path, max_file_bytes=4_194_304, compaction_threshold_bytes=120
    )
    asyncio.run(provider.remember({"body": "newest fact", "kind": "note"}))

    after = target.read_text(encoding="utf-8")
    # File is consolidated within the cap.
    assert len(after.encode("utf-8")) <= 4_194_304
    # New entry appended.
    assert "newest fact" in after
    # Duplicates removed: 'fact-00' appears exactly once now.
    assert after.count("- [note] fact-00\n") == 1
    # Newest pre-existing fact retained.
    assert "fact-09" in after

    # Archive created with the ORIGINAL content.
    archive_dir = _archive_dir(tmp_path)
    assert archive_dir.is_dir()
    archives = list(archive_dir.glob("MEMORY.md.*.md"))
    assert len(archives) == 1
    assert archives[0].read_text(encoding="utf-8") == prefill


def test_gate_on_archive_name_is_deterministic(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(MAGI_MEMORY_COMPACTION_ENABLED_ENV, "1")
    target = tmp_path / "MEMORY.md"
    prefill = "".join(f"\n- [note] fact-{i:02d}\n" for i in range(10))
    target.write_text(prefill, encoding="utf-8")
    provider = _provider(
        tmp_path, max_file_bytes=4_194_304, compaction_threshold_bytes=80
    )
    asyncio.run(provider.remember({"body": "x", "kind": "note"}))

    archives = list(_archive_dir(tmp_path).glob("MEMORY.md.*.md"))
    assert len(archives) == 1
    # Suffix is a content hash (hex), not a timestamp — deterministic.
    suffix = archives[0].name[len("MEMORY.md.") : -len(".md")]
    assert suffix
    assert all(c in "0123456789abcdef" for c in suffix)


def test_gate_on_user_md_compacts_independently(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(MAGI_MEMORY_COMPACTION_ENABLED_ENV, "1")
    target = tmp_path / "USER.md"
    prefill = "".join(f"\n- [note] u-{i:02d}\n" for i in range(10))
    target.write_text(prefill, encoding="utf-8")
    provider = _provider(
        tmp_path, max_file_bytes=4_194_304, compaction_threshold_bytes=80
    )
    asyncio.run(provider.remember({"body": "newest user fact", "kind": "note",
                                   "target_file": "USER.md"}))
    after = target.read_text(encoding="utf-8")
    assert "newest user fact" in after
    archives = list(_archive_dir(tmp_path).glob("USER.md.*.md"))
    assert len(archives) == 1


def test_gate_on_redaction_preserved_through_compaction(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv(MAGI_MEMORY_COMPACTION_ENABLED_ENV, "1")
    target = tmp_path / "MEMORY.md"
    prefill = "".join(f"\n- [note] fact-{i:02d}\n" for i in range(10))
    target.write_text(prefill, encoding="utf-8")
    provider = _provider(
        tmp_path, max_file_bytes=4_194_304, compaction_threshold_bytes=80
    )
    # New body carries a secret — must be redacted before write, unchanged.
    asyncio.run(
        provider.remember(
            {"body": "token sk-live-ABCDEFGH12345678 here", "kind": "note"}
        )
    )
    after = target.read_text(encoding="utf-8")
    assert "sk-live-ABCDEFGH12345678" not in after
    assert "[redacted]" in after


# ---------------------------------------------------------------------------
# (c) gate ON + under threshold → no archive, plain append
# ---------------------------------------------------------------------------


def test_gate_on_under_threshold_plain_append(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(MAGI_MEMORY_COMPACTION_ENABLED_ENV, "1")
    target = tmp_path / "MEMORY.md"
    target.write_text("- [note] small\n", encoding="utf-8")
    before = target.read_text(encoding="utf-8")
    provider = _provider(
        tmp_path, max_file_bytes=4_194_304, compaction_threshold_bytes=10_000
    )
    asyncio.run(provider.remember({"body": "another", "kind": "note"}))
    after = target.read_text(encoding="utf-8")
    # Under threshold: plain append, original preserved, no archive.
    assert after.startswith(before)
    assert "another" in after
    assert not _archive_dir(tmp_path).exists()
