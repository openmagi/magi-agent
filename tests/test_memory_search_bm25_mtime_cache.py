"""H-27 — ``PyBM25Backend.reindex`` skips re-tokenisation when the
workspace tree has not changed since the last successful reindex.

``hipocampus_readonly._local_search_results`` calls
``backend.reindex(workspace_root)`` before every search, so without a
cache the backend re-globs + re-reads + re-tokenises the whole
``memory/`` tree on every recall — O(corpus) per turn for zero
benefit. H-27 caches the built index keyed by ``(root, max(mtime),
file_count)`` so a no-op reindex returns immediately.

The contract this module locks:

1. Second call with no file change does NOT re-tokenise.
2. A file *write* (mtime bump) invalidates the cache and re-tokenises.
3. A file *add* (count delta, even if no existing mtime changed)
   invalidates the cache and re-tokenises.
4. A file *delete* (count delta) invalidates the cache and re-tokenises.
5. Results returned by ``search`` are identical to the un-cached
   baseline for every cached/uncached path.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from magi_agent.memory.search import PyBM25Backend


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def memory_root(tmp_path: Path) -> Path:
    _write(tmp_path, "memory/daily/2026-06-01.md", "kubernetes deploy rollout")
    _write(tmp_path, "memory/weekly/2026-W23.md", "stripe billing reconciliation")
    _write(tmp_path, "MEMORY.md", "top level memory digest")
    return tmp_path


def _patch_tokenizer(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Wrap the module-level ``_tokenize`` so tests can count calls."""

    from magi_agent.memory.search import bm25 as bm25_mod

    calls: list[str] = []
    real_tokenize = bm25_mod._tokenize

    def counted(text: str) -> list[str]:
        calls.append(text[:24])
        return real_tokenize(text)

    monkeypatch.setattr(bm25_mod, "_tokenize", counted)
    return calls


def test_second_reindex_no_change_is_no_op(
    memory_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = PyBM25Backend()
    calls = _patch_tokenizer(monkeypatch)
    backend.reindex(memory_root)
    first_count = len(calls)
    assert first_count > 0  # baseline: it did tokenise the corpus
    backend.reindex(memory_root)
    assert len(calls) == first_count, (
        "no-change reindex must skip re-tokenisation (H-27 cache miss)"
    )


def test_mtime_bump_invalidates_cache(
    memory_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = PyBM25Backend()
    calls = _patch_tokenizer(monkeypatch)
    backend.reindex(memory_root)
    first_count = len(calls)
    # Bump mtime in the future so the resolution of the host FS does not eat
    # the change (some filesystems have 1s granularity).
    target = memory_root / "MEMORY.md"
    new_mtime = target.stat().st_mtime + 5.0
    target.write_text("digest now mentions kafka too", encoding="utf-8")
    import os
    os.utime(target, (new_mtime, new_mtime))
    backend.reindex(memory_root)
    assert len(calls) > first_count, "mtime change must invalidate the cache"


def test_file_add_invalidates_cache(
    memory_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = PyBM25Backend()
    calls = _patch_tokenizer(monkeypatch)
    backend.reindex(memory_root)
    first_count = len(calls)
    _write(memory_root, "memory/daily/2026-06-03.md", "new file new tokens")
    backend.reindex(memory_root)
    assert len(calls) > first_count, "file addition must invalidate the cache"


def test_file_delete_invalidates_cache(
    memory_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = PyBM25Backend()
    calls = _patch_tokenizer(monkeypatch)
    backend.reindex(memory_root)
    first_count = len(calls)
    (memory_root / "memory/daily/2026-06-01.md").unlink()
    backend.reindex(memory_root)
    assert len(calls) > first_count, "file deletion must invalidate the cache"


def test_search_results_unchanged_when_cache_hits(memory_root: Path) -> None:
    """Sanity: a cache-hit reindex returns identical search results to
    the uncached path."""

    a = PyBM25Backend()
    a.reindex(memory_root)
    a.reindex(memory_root)  # cache hit
    fresh = PyBM25Backend()
    fresh.reindex(memory_root)
    q = "kubernetes"
    assert [hit.path for hit in a.search(q, k=5)] == [
        hit.path for hit in fresh.search(q, k=5)
    ]


def test_different_root_does_not_alias(
    tmp_path: Path, memory_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second ``reindex`` with a *different* root must re-tokenise — the
    cache signature is per-root."""

    other = tmp_path / "other_workspace"
    other.mkdir()
    _write(other, "MEMORY.md", "completely different content")
    backend = PyBM25Backend()
    calls = _patch_tokenizer(monkeypatch)
    backend.reindex(memory_root)
    first_count = len(calls)
    backend.reindex(other)
    assert len(calls) > first_count, "different root must not hit the cache"
