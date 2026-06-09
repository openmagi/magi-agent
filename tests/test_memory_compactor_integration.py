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


# ---------------------------------------------------------------------------
# Fix 1 — reserve headroom for the triggering entry (all-unique near-cap)
# ---------------------------------------------------------------------------


def test_gate_on_all_unique_near_cap_new_fact_succeeds(
    tmp_path: Path, monkeypatch
) -> None:
    """Gate ON, all-unique entries near cap: remember() a brand-new fact must
    SUCCEED (not raise ValueError). The new fact must appear, the original must
    be archived, and the final file must be <= max_file_bytes.

    Before the fix, consolidate() was called with max_bytes=max_file_bytes, which
    filled the entire cap with existing entries, leaving no room for the incoming
    entry — the subsequent max_file_bytes guard then raised ValueError and the
    new fact was LOST.
    """
    monkeypatch.setenv(MAGI_MEMORY_COMPACTION_ENABLED_ENV, "1")
    target = tmp_path / "MEMORY.md"

    # 11 unique entries render (via _render) to exactly 264 bytes.
    # Setting max_file_bytes=264 means consolidate(original, max_bytes=264)
    # keeps all 264 bytes of unique entries, leaving ZERO headroom for the
    # incoming entry (36 bytes). Without the fix: 264 + 36 > 264 → ValueError.
    # With the fix: consolidate(original, max_bytes=264-36=228) drops the
    # oldest entries to make room, then the append succeeds at ≤264 bytes.
    max_file_bytes = 264

    # Build all-unique entries: 11 × "- [note] unique-factXX\n" → 264 bytes rendered.
    lines = "".join(f"\n- [note] unique-fact{i:02d}\n" for i in range(11))
    target.write_text(lines, encoding="utf-8")
    pre_size = len(target.read_bytes())
    assert pre_size == max_file_bytes, "pre-fill must exactly fill the cap"

    # Set threshold so the compaction branch triggers.
    threshold = pre_size - 10

    provider = _provider(
        tmp_path,
        max_file_bytes=max_file_bytes,
        compaction_threshold_bytes=threshold,
    )

    new_body = "brand-new-unique-fact-xyz"
    # Before the fix this raised ValueError; after the fix it must succeed.
    asyncio.run(provider.remember({"body": new_body, "kind": "note"}))

    after = target.read_text(encoding="utf-8")
    after_bytes = len(after.encode("utf-8"))

    # The new fact must be present.
    assert new_body in after, "new fact must be appended"
    # The file must respect the hard cap.
    assert after_bytes <= max_file_bytes, (
        f"file size {after_bytes} exceeds max_file_bytes {max_file_bytes}"
    )
    # The original was archived.
    archives = list(_archive_dir(tmp_path).glob("MEMORY.md.*.md"))
    assert len(archives) == 1, "original must be archived before compaction"


# ---------------------------------------------------------------------------
# Fix 2 — post-compaction dedup: same fact must NOT be appended twice
# ---------------------------------------------------------------------------


def test_gate_on_post_compaction_dedup_prevents_duplicate(
    tmp_path: Path, monkeypatch
) -> None:
    """Gate ON, USER.md compacted, then remember() a fact already in the
    compacted file → it must NOT be duplicated.

    Before the fix, _render joined entries with single '\\n', breaking the
    provider's dedup check (which tests 'if entry in existing' where entry
    has a LEADING '\\n'). After compaction the first entry in the file had no
    leading '\\n', so the substring match failed and the duplicate slipped through.
    """
    monkeypatch.setenv(MAGI_MEMORY_COMPACTION_ENABLED_ENV, "1")
    target = tmp_path / "USER.md"

    # Pre-fill with entries + a dup to trigger compaction.
    prefill = "".join(f"\n- [note] u-{i:02d}\n" for i in range(10))
    # Add duplicate so compaction actually fires (changes file).
    prefill += "\n- [note] u-00\n"
    target.write_text(prefill, encoding="utf-8")

    max_file_bytes = 4_194_304
    # Low threshold so compaction triggers immediately.
    provider = _provider(
        tmp_path,
        max_file_bytes=max_file_bytes,
        compaction_threshold_bytes=50,
    )

    # First remember() triggers compaction and appends the new fact.
    asyncio.run(
        provider.remember({"body": "u-00", "kind": "note", "target_file": "USER.md"})
    )

    after = target.read_text(encoding="utf-8")
    # "u-00" already existed; must appear exactly once (dedup should catch it).
    occurrences = after.count("- [note] u-00\n")
    assert occurrences == 1, (
        f"'u-00' must appear exactly once after compaction+dedup, found {occurrences}"
    )
