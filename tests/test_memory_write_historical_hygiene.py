"""Memory-write historical-hygiene fix (drift root fix).

An in-flight user question stored as a present-tense, undated "fact" in
MEMORY.md/USER.md was re-answered every session on raw FileRead paths (the
prompt-projection continuity framing is not wired on the governed serving
path).  These tests lock the deterministic guarantees that make the memory
FILE itself carry historical framing on EVERY read path:

  * every appended entry is date-stamped ``- [kind YYYY-MM-DD] body``;
  * the file leads with an idempotent ``magi:memory-log`` header;
  * USER.md dedup is date-insensitive (legacy undated OR any dated bracket
    with the same kind+body blocks a re-write);
  * the compactor round-trips dated entries + header without corruption.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import magi_agent.memory.adapters.local_file_writable as lfw
from magi_agent.memory.adapters.local_file_writable import (
    _MEMORY_LOG_HEADER,
    _MEMORY_LOG_MARKER,
    LocalFileMemoryConfig,
    LocalFileMemoryProvider,
)


def _provider(tmp_path: Path) -> LocalFileMemoryProvider:
    config = LocalFileMemoryConfig(
        workspace_root=tmp_path, enabled=True, write_enabled=True
    )
    return LocalFileMemoryProvider(config)


def _freeze(monkeypatch: pytest.MonkeyPatch, date: str) -> None:
    monkeypatch.setattr(lfw, "_utc_today", lambda: date)


# ---------------------------------------------------------------------------
# U1: date-stamped entries
# ---------------------------------------------------------------------------


def test_appended_entry_carries_utc_date_stamp_memory_md(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze(monkeypatch, "2026-07-16")
    provider = _provider(tmp_path)

    asyncio.run(
        provider.remember(
            {"body": "User prefers dark mode.", "kind": "preference", "target_file": "MEMORY.md"}
        )
    )

    content = (tmp_path / "MEMORY.md").read_text(encoding="utf-8")
    assert "- [preference 2026-07-16] User prefers dark mode." in content


def test_appended_entry_carries_utc_date_stamp_user_md(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze(monkeypatch, "2026-07-16")
    provider = _provider(tmp_path)

    asyncio.run(
        provider.remember(
            {"body": "User is based in Seoul.", "kind": "fact", "target_file": "USER.md"}
        )
    )

    content = (tmp_path / "USER.md").read_text(encoding="utf-8")
    assert "- [fact 2026-07-16] User is based in Seoul." in content


# ---------------------------------------------------------------------------
# U2: idempotent historical-records header
# ---------------------------------------------------------------------------


def test_header_created_on_first_write_and_not_duplicated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze(monkeypatch, "2026-07-16")
    provider = _provider(tmp_path)
    target = tmp_path / "MEMORY.md"

    asyncio.run(provider.remember({"body": "first", "kind": "note", "target_file": "MEMORY.md"}))
    first = target.read_text(encoding="utf-8")
    assert first.startswith(_MEMORY_LOG_HEADER)
    assert first.count(_MEMORY_LOG_MARKER) == 1

    asyncio.run(provider.remember({"body": "second", "kind": "note", "target_file": "MEMORY.md"}))
    second = target.read_text(encoding="utf-8")
    assert second.count(_MEMORY_LOG_MARKER) == 1
    assert "- [note 2026-07-16] first" in second
    assert "- [note 2026-07-16] second" in second


def test_header_added_to_existing_legacy_file_lacking_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze(monkeypatch, "2026-07-16")
    provider = _provider(tmp_path)
    target = tmp_path / "MEMORY.md"
    target.write_text("- [note] legacy line preserved\n", encoding="utf-8")

    asyncio.run(provider.remember({"body": "new fact", "kind": "note", "target_file": "MEMORY.md"}))

    content = target.read_text(encoding="utf-8")
    assert content.startswith(_MEMORY_LOG_HEADER)
    assert content.count(_MEMORY_LOG_MARKER) == 1
    # Legacy content survives.
    assert "- [note] legacy line preserved" in content
    assert "- [note 2026-07-16] new fact" in content


# ---------------------------------------------------------------------------
# U1: date-insensitive USER.md dedup
# ---------------------------------------------------------------------------


def test_legacy_undated_entry_blocks_dated_rewrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze(monkeypatch, "2026-07-16")
    provider = _provider(tmp_path)
    target = tmp_path / "USER.md"
    target.write_text("- [fact] User is based in Seoul.\n", encoding="utf-8")

    asyncio.run(
        provider.remember(
            {"body": "User is based in Seoul.", "kind": "fact", "target_file": "USER.md"}
        )
    )

    content = target.read_text(encoding="utf-8")
    assert content.count("User is based in Seoul.") == 1
    # No dated copy was appended.
    assert "- [fact 2026-07-16]" not in content


def test_dated_entry_blocks_rewrite_on_later_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze(monkeypatch, "2026-07-16")
    provider = _provider(tmp_path)
    target = tmp_path / "USER.md"

    asyncio.run(
        provider.remember(
            {"body": "User enjoys hiking.", "kind": "fact", "target_file": "USER.md"}
        )
    )
    # A later day, same kind+body, must be deduped (no daily accumulation).
    _freeze(monkeypatch, "2026-07-20")
    asyncio.run(
        provider.remember(
            {"body": "User enjoys hiking.", "kind": "fact", "target_file": "USER.md"}
        )
    )

    content = target.read_text(encoding="utf-8")
    assert content.count("User enjoys hiking.") == 1
    assert "- [fact 2026-07-20]" not in content
    assert "- [fact 2026-07-16] User enjoys hiking." in content


def test_dedup_is_kind_sensitive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A same-body entry under a DIFFERENT kind is not a duplicate."""
    _freeze(monkeypatch, "2026-07-16")
    provider = _provider(tmp_path)
    target = tmp_path / "USER.md"

    asyncio.run(
        provider.remember({"body": "Seoul.", "kind": "fact", "target_file": "USER.md"})
    )
    asyncio.run(
        provider.remember({"body": "Seoul.", "kind": "note", "target_file": "USER.md"})
    )

    content = target.read_text(encoding="utf-8")
    assert "- [fact 2026-07-16] Seoul." in content
    assert "- [note 2026-07-16] Seoul." in content


# ---------------------------------------------------------------------------
# U1/U2: compactor round-trip with dated entries + header
# ---------------------------------------------------------------------------


def test_compactor_roundtrip_preserves_header_and_dedups_dated_entries() -> None:
    from magi_agent.memory.compactor import consolidate

    text = (
        _MEMORY_LOG_HEADER
        + "\n- [note 2026-07-16] alpha"
        + "\n- [note 2026-07-16] alpha"  # exact dated duplicate
        + "\n- [fact 2026-07-15] beta\n"
    )
    result = consolidate(text, max_bytes=10_000)

    # Header line survives the round trip.
    assert _MEMORY_LOG_MARKER in result.text
    # Exact-line dedup still works with dated brackets.
    assert result.text.count("- [note 2026-07-16] alpha") == 1
    assert "- [fact 2026-07-15] beta" in result.text
    assert result.dropped_count == 1


def test_compactor_split_entries_parses_dated_brackets() -> None:
    from magi_agent.memory.compactor import _split_entries

    text = (
        _MEMORY_LOG_HEADER
        + "\n- [note 2026-07-16] alpha\n- [fact 2026-07-15] beta\n"
    )
    entries = _split_entries(text)
    assert "- [note 2026-07-16] alpha" in entries
    assert "- [fact 2026-07-15] beta" in entries
