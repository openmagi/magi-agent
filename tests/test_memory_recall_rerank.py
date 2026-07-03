"""PR3 — manifest + optional cheap-model semantic re-rank over BM25 recall.

This adds an OPTIONAL layer on top of the existing BM25 per-turn recall
(``build_cli_memory_recall_block``):

  1. MANIFEST: ``build_memory_manifest(memory_dir)`` scans memory files'
     frontmatter (name / description / type) plus mtime, newest-first, capped,
     with a staleness marker for entries older than one day.
  2. RE-RANK: an optional cheap-model selector reorders the BM25 candidate hits
     in-context.  Gated by ``MAGI_MEMORY_RECALL_RERANK_ENABLED`` (default OFF).

GOVERNANCE INVARIANT
--------------------
Default OFF.  When the flag is off — OR the selector errors / has no key — the
recall block is BYTE-IDENTICAL to the pre-PR3 BM25 order.  Re-rank never raises
into the turn loop (fail-open).
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from magi_agent.cli.memory_manifest import (
    MemoryManifestEntry,
    build_memory_manifest,
)
from magi_agent.cli.memory_recall_block import build_cli_memory_recall_block
from magi_agent.cli.memory_recall_rerank import (
    MAGI_MEMORY_RECALL_RERANK_ENABLED_ENV,
)
from magi_agent.memory.search.base import SearchHit


def _write(root: Path, rel: str, text: str, *, mtime: float | None = None) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def _on_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_MEMORY_ENABLED", "1")
    monkeypatch.setenv("MAGI_MEMORY_RECALL_ENABLED", "1")
    monkeypatch.setenv("MAGI_MEMORY_PREFER_LOCAL_SEARCH", "1")
    monkeypatch.setenv("MAGI_MEMORY_PREFER_QMD", "0")


# ---------------------------------------------------------------------------
# (a) MANIFEST: extracts description + type + mtime correctly
# ---------------------------------------------------------------------------


def test_manifest_extracts_frontmatter_description_type_and_mtime(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    fixed_mtime = time.time()
    _write(
        memory_dir,
        "daily/2026-06-01.md",
        "---\n"
        "name: billing-rollout\n"
        "description: decision to adopt zebraquux for billing\n"
        "type: decision\n"
        "---\n"
        "body text here\n",
        mtime=fixed_mtime,
    )

    manifest = build_memory_manifest(memory_dir)

    assert len(manifest) == 1
    entry = manifest[0]
    assert isinstance(entry, MemoryManifestEntry)
    assert entry.name == "billing-rollout"
    assert entry.description == "decision to adopt zebraquux for billing"
    assert entry.type == "decision"
    assert entry.path == "daily/2026-06-01.md"
    assert entry.mtime == pytest.approx(fixed_mtime, abs=1.0)
    # Fresh file (just-now mtime) is NOT stale.
    assert entry.stale is False


def test_manifest_sorted_newest_first_and_marks_stale(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    now = time.time()
    two_days_ago = now - 2 * 24 * 3600
    _write(
        memory_dir,
        "daily/old.md",
        "---\ndescription: old note\ntype: note\n---\nold",
        mtime=two_days_ago,
    )
    _write(
        memory_dir,
        "daily/new.md",
        "---\ndescription: new note\ntype: note\n---\nnew",
        mtime=now,
    )

    manifest = build_memory_manifest(memory_dir)

    assert [e.path for e in manifest] == ["daily/new.md", "daily/old.md"]
    # The >1-day-old entry is marked stale; the fresh one is not.
    by_path = {e.path: e for e in manifest}
    assert by_path["daily/old.md"].stale is True
    assert by_path["daily/new.md"].stale is False


def test_manifest_caps_entries(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    for i in range(250):
        _write(memory_dir, f"daily/note-{i:03d}.md", f"---\ndescription: n{i}\n---\nx")
    manifest = build_memory_manifest(memory_dir, cap=200)
    assert len(manifest) == 200


def test_manifest_never_includes_soul(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    _write(memory_dir, "SOUL.md", "---\ndescription: secret soul\n---\nx")
    _write(memory_dir, "daily/2026-06-01.md", "---\ndescription: ok\n---\nx")
    manifest = build_memory_manifest(memory_dir)
    assert all("SOUL.md" not in e.path for e in manifest)


def test_manifest_missing_dir_returns_empty(tmp_path: Path) -> None:
    assert build_memory_manifest(tmp_path / "does-not-exist") == []


# ---------------------------------------------------------------------------
# (b) RE-RANK ON reorders candidates per the (fake) selector
# ---------------------------------------------------------------------------


def test_rerank_on_reorders_candidates_per_selector(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _on_env(monkeypatch)
    monkeypatch.setenv("MAGI_MEMORY_RECALL_RERANK_ENABLED", "1")
    # Two matching daily files; BM25 will rank one above the other.  The fake
    # selector flips the order, and the emitted block must reflect the flip.
    _write(
        tmp_path,
        "memory/daily/2026-06-01.md",
        "zebraquux zebraquux zebraquux alpha doc top by bm25 " + ("pad " * 5),
    )
    _write(
        tmp_path,
        "memory/daily/2026-06-02.md",
        "zebraquux beta doc " + ("pad " * 5),
    )

    import magi_agent.cli.memory_recall_block as rb

    captured: dict[str, object] = {}

    def _fake_rerank(*, hits, query, memory_dir, config, env=None):  # noqa: ANN001
        captured["query"] = query
        captured["n"] = len(hits)
        # Reverse the BM25 order deterministically.
        return list(reversed(list(hits)))

    monkeypatch.setattr(rb, "rerank_hits", _fake_rerank)

    block = build_cli_memory_recall_block(
        workspace_root=str(tmp_path), query="zebraquux", memory_mode="normal"
    )
    assert block
    assert captured["query"] == "zebraquux"
    assert captured["n"] >= 2
    # After reversal, the "beta" doc (BM25-second) leads the block.
    assert block.index("beta doc") < block.index("alpha doc")


# ---------------------------------------------------------------------------
# (c) RE-RANK OFF and selector-failure both yield the exact current BM25 block
# ---------------------------------------------------------------------------


def test_rerank_off_is_byte_identical_to_bm25(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _on_env(monkeypatch)
    _write(
        tmp_path,
        "memory/daily/2026-06-01.md",
        "zebraquux zebraquux alpha doc " + ("pad " * 5),
    )
    _write(tmp_path, "memory/daily/2026-06-02.md", "zebraquux beta doc " + ("pad " * 5))

    baseline = build_cli_memory_recall_block(
        workspace_root=str(tmp_path), query="zebraquux", memory_mode="normal"
    )
    assert baseline

    # Flag OFF (delete it explicitly) must reproduce the baseline byte-for-byte.
    monkeypatch.delenv("MAGI_MEMORY_RECALL_RERANK_ENABLED", raising=False)
    off = build_cli_memory_recall_block(
        workspace_root=str(tmp_path), query="zebraquux", memory_mode="normal"
    )
    assert off == baseline


def test_rerank_selector_failure_falls_back_to_bm25_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _on_env(monkeypatch)
    _write(
        tmp_path,
        "memory/daily/2026-06-01.md",
        "zebraquux zebraquux alpha doc " + ("pad " * 5),
    )
    _write(tmp_path, "memory/daily/2026-06-02.md", "zebraquux beta doc " + ("pad " * 5))

    # Capture the OFF baseline first.
    monkeypatch.delenv("MAGI_MEMORY_RECALL_RERANK_ENABLED", raising=False)
    baseline = build_cli_memory_recall_block(
        workspace_root=str(tmp_path), query="zebraquux", memory_mode="normal"
    )
    assert baseline

    # Now turn the flag ON but make the selector blow up: fail-open must return
    # the BM25 order, byte-identical to the OFF baseline.
    monkeypatch.setenv("MAGI_MEMORY_RECALL_RERANK_ENABLED", "1")
    import magi_agent.cli.memory_recall_block as rb

    def _boom(*, hits, query, memory_dir, config, env=None):  # noqa: ANN001
        raise RuntimeError("selector boom")

    monkeypatch.setattr(rb, "rerank_hits", _boom)

    with_failure = build_cli_memory_recall_block(
        workspace_root=str(tmp_path), query="zebraquux", memory_mode="normal"
    )
    assert with_failure == baseline


def test_rerank_module_returns_bm25_order_when_flag_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ``rerank_hits`` entry point itself is a no-op (identity) when the flag
    is off OR no model is resolvable — it returns the SAME list object order."""
    from magi_agent.cli.memory_recall_rerank import rerank_hits
    from magi_agent.memory.config import resolve_memory_config

    monkeypatch.delenv("MAGI_MEMORY_RECALL_RERANK_ENABLED", raising=False)
    hits = [
        SearchHit(path="memory/daily/a.md", content="a", score=3.0),
        SearchHit(path="memory/daily/b.md", content="b", score=2.0),
    ]
    out = rerank_hits(
        hits=hits,
        query="anything",
        memory_dir=tmp_path / "memory",
        config=resolve_memory_config(),
    )
    assert [h.path for h in out] == [h.path for h in hits]


# ---------------------------------------------------------------------------
# (d) Stale entry gets the staleness <system-reminder> note
# ---------------------------------------------------------------------------


def test_stale_pick_gets_staleness_reminder_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _on_env(monkeypatch)
    monkeypatch.setenv("MAGI_MEMORY_RECALL_RERANK_ENABLED", "1")
    old = time.time() - 5 * 24 * 3600
    _write(
        tmp_path,
        "memory/daily/2026-06-01.md",
        "zebraquux stale decision note " + ("pad " * 5),
        mtime=old,
    )

    import magi_agent.cli.memory_recall_block as rb

    # Identity reranker (keeps the one stale hit).
    monkeypatch.setattr(
        rb, "rerank_hits", lambda *, hits, query, memory_dir, config, env=None: list(hits)
    )

    block = build_cli_memory_recall_block(
        workspace_root=str(tmp_path), query="zebraquux", memory_mode="normal"
    )
    assert block
    assert "zebraquux" in block
    # A staleness system-reminder is appended for the stale pick.
    assert "<system-reminder>" in block
    assert "stale" in block.lower()


def test_fresh_pick_has_no_staleness_reminder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _on_env(monkeypatch)
    monkeypatch.setenv("MAGI_MEMORY_RECALL_RERANK_ENABLED", "1")
    _write(
        tmp_path,
        "memory/daily/2026-06-01.md",
        "zebraquux fresh decision note " + ("pad " * 5),
        mtime=time.time(),
    )

    import magi_agent.cli.memory_recall_block as rb

    monkeypatch.setattr(
        rb, "rerank_hits", lambda *, hits, query, memory_dir, config, env=None: list(hits)
    )

    block = build_cli_memory_recall_block(
        workspace_root=str(tmp_path), query="zebraquux", memory_mode="normal"
    )
    assert block
    assert "<system-reminder>" not in block


# ---------------------------------------------------------------------------
# D2 (N-14): recall re-rank is loop-aware.
#   * On a running event loop (the serve prompt-assembly path) the sync
#     rerank_hits returns identity WITH a one-time warning and pays ZERO
#     model/manifest build cost.
#   * rerank_hits_async is the real async surface (reorder + fail-open).
# ---------------------------------------------------------------------------

import asyncio  # noqa: E402
import logging  # noqa: E402
from types import SimpleNamespace  # noqa: E402


def _rerank_env_on() -> dict[str, str]:
    return {MAGI_MEMORY_RECALL_RERANK_ENABLED_ENV: "1"}


def _two_hits() -> list[SearchHit]:
    return [
        SearchHit(path="memory/daily/a.md", content="alpha body", score=3.0),
        SearchHit(path="memory/daily/b.md", content="beta body", score=2.0),
    ]


class _CountingFactory:
    """A no-arg model factory that records how many times it is called.

    It returns a reorder-capable fake model, so if the running-loop guard did
    NOT short-circuit, the model/manifest build cost would be paid (call count
    > 0) and the order would flip. The guard must keep the count at 0.
    """

    def __init__(self, order: list[str]) -> None:
        self.calls = 0
        self._order = order

    def __call__(self) -> object:
        self.calls += 1
        return _FakeModel({"order": self._order})


class _FakeResp:
    def __init__(self, text: str) -> None:
        self.content = SimpleNamespace(parts=[SimpleNamespace(text=text)])


class _FakeModel:
    def __init__(self, payload: dict[str, object]) -> None:
        self._text = __import__("json").dumps(payload)

    async def generate_content_async(self, request: object, stream: bool = False):  # noqa: ANN201
        yield _FakeResp(self._text)


class _BoomModel:
    async def generate_content_async(self, request: object, stream: bool = False):  # noqa: ANN201
        raise RuntimeError("model boom")
        yield  # pragma: no cover - makes this an async generator


def test_rerank_skips_identity_with_warning_on_running_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from magi_agent.cli import memory_recall_rerank as rr

    monkeypatch.setattr(rr, "_RUNNING_LOOP_WARNED", False, raising=False)
    factory = _CountingFactory(order=["memory/daily/b.md", "memory/daily/a.md"])
    hits = _two_hits()

    async def _inner() -> list[SearchHit]:
        return rr.rerank_hits(
            hits=hits,
            query="anything",
            memory_dir=tmp_path / "memory",
            config=object(),
            model_factory=factory,
            env=_rerank_env_on(),
        )

    with caplog.at_level(logging.WARNING, logger=rr.__name__):
        out = asyncio.run(_inner())

    # Identity: NOT reordered.
    assert [h.path for h in out] == [h.path for h in hits]
    # No pre-build cost paid.
    assert factory.calls == 0
    # Exactly one skip WARNING surfaced.
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "re-rank skipped" in warnings[0].getMessage()


def test_rerank_running_loop_warns_once_then_debug(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from magi_agent.cli import memory_recall_rerank as rr

    monkeypatch.setattr(rr, "_RUNNING_LOOP_WARNED", False, raising=False)
    factory = _CountingFactory(order=["memory/daily/b.md", "memory/daily/a.md"])
    hits = _two_hits()

    async def _inner() -> None:
        for _ in range(2):
            rr.rerank_hits(
                hits=hits,
                query="anything",
                memory_dir=tmp_path / "memory",
                config=object(),
                model_factory=factory,
                env=_rerank_env_on(),
            )

    with caplog.at_level(logging.DEBUG, logger=rr.__name__):
        asyncio.run(_inner())

    skip_records = [
        r for r in caplog.records if "re-rank skipped" in r.getMessage()
    ]
    levels = sorted(r.levelno for r in skip_records)
    assert levels == [logging.DEBUG, logging.WARNING]
    assert factory.calls == 0


def test_rerank_async_variant_reorders(tmp_path: Path) -> None:
    from magi_agent.cli.memory_recall_rerank import rerank_hits_async

    hits = _two_hits()
    factory = _CountingFactory(order=["memory/daily/b.md", "memory/daily/a.md"])

    out = asyncio.run(
        rerank_hits_async(
            hits=hits,
            query="anything",
            memory_dir=tmp_path / "memory",
            config=object(),
            model_factory=factory,
            env=_rerank_env_on(),
        )
    )
    assert [h.path for h in out] == ["memory/daily/b.md", "memory/daily/a.md"]
    assert factory.calls == 1

    # Fail-open: a model that raises yields the input BM25 order unchanged.
    out2 = asyncio.run(
        rerank_hits_async(
            hits=hits,
            query="anything",
            memory_dir=tmp_path / "memory",
            config=object(),
            model_factory=lambda: _BoomModel(),
            env=_rerank_env_on(),
        )
    )
    assert [h.path for h in out2] == [h.path for h in hits]


def test_rerank_sync_no_loop_path_byte_identical(tmp_path: Path) -> None:
    """The sync path (no running loop) is unchanged: with a real reordering
    model it still reorders (representative behavior-preservation case)."""
    from magi_agent.cli.memory_recall_rerank import rerank_hits

    hits = _two_hits()
    factory = _CountingFactory(order=["memory/daily/b.md", "memory/daily/a.md"])
    out = rerank_hits(
        hits=hits,
        query="anything",
        memory_dir=tmp_path / "memory",
        config=object(),
        model_factory=factory,
        env=_rerank_env_on(),
    )
    assert [h.path for h in out] == ["memory/daily/b.md", "memory/daily/a.md"]
    assert factory.calls == 1
