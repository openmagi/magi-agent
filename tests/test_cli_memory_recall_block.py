"""PR-E item 3 — per-turn query-based recall block builder.

The static memory snapshot is frozen per session and takes NO query.  This
builder adds an OPTIONAL per-turn recall: when ``recall_enabled`` AND
``prefer_local_search`` are on, it runs the local ``memory/search`` backend over
the current user message and fences the top hits as a ``<memory-recall>`` block.

Governance invariant: OFF (the default) => "" (no block, no search); ON => a
real, redacted, byte-bounded block.  Fail-soft: a search error returns "".
"""
from __future__ import annotations

from pathlib import Path

import pytest

from magi_agent.cli.memory_recall_block import build_cli_memory_recall_block


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _on_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_MEMORY_ENABLED", "1")
    monkeypatch.setenv("MAGI_MEMORY_RECALL_ENABLED", "1")
    monkeypatch.setenv("MAGI_MEMORY_PREFER_LOCAL_SEARCH", "1")
    # Force the pure-python backend so the test never depends on a qmd binary.
    monkeypatch.setenv("MAGI_MEMORY_PREFER_QMD", "0")


def test_off_by_default_returns_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAGI_MEMORY_ENABLED", raising=False)
    monkeypatch.delenv("MAGI_MEMORY_RECALL_ENABLED", raising=False)
    monkeypatch.delenv("MAGI_MEMORY_PREFER_LOCAL_SEARCH", raising=False)
    _write(tmp_path, "memory/daily/2026-06-01.md", "zebraquux distinctive term here")
    assert (
        build_cli_memory_recall_block(
            workspace_root=str(tmp_path), query="zebraquux", memory_mode="normal"
        )
        == ""
    )


def test_recall_enabled_but_prefer_local_off_returns_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAGI_MEMORY_RECALL_ENABLED", "1")
    monkeypatch.delenv("MAGI_MEMORY_PREFER_LOCAL_SEARCH", raising=False)
    _write(tmp_path, "memory/daily/2026-06-01.md", "zebraquux term")
    assert (
        build_cli_memory_recall_block(
            workspace_root=str(tmp_path), query="zebraquux", memory_mode="normal"
        )
        == ""
    )


def test_on_returns_fenced_block_with_matching_hit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _on_env(monkeypatch)
    _write(
        tmp_path,
        "memory/daily/2026-06-01.md",
        "decision: we will adopt zebraquux for the billing rollout",
    )
    _write(tmp_path, "memory/daily/2026-06-02.md", "unrelated grocery list")
    block = build_cli_memory_recall_block(
        workspace_root=str(tmp_path), query="zebraquux", memory_mode="normal"
    )
    assert block
    assert "<memory-recall" in block and "</memory-recall>" in block
    assert "zebraquux" in block
    # The unrelated doc must not appear.
    assert "grocery" not in block


def test_recall_block_is_background_tagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A2: the emitted block must carry a ``continuity="background"`` attribute
    so the model treats recalled memory as background reference, not as the
    current conversation."""
    _on_env(monkeypatch)
    _write(
        tmp_path,
        "memory/daily/2026-06-01.md",
        "decision: we will adopt zebraquux for the billing rollout",
    )
    block = build_cli_memory_recall_block(
        workspace_root=str(tmp_path), query="zebraquux", memory_mode="normal"
    )
    assert block
    assert block.startswith('<memory-recall hidden="true" continuity="background">')
    assert block.endswith("</memory-recall>")


def test_master_only_produces_recall_block_after_dual_gate_fix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PR1 dual-gate fix: master-on alone (no explicit prefer_local_search)
    yields a real per-turn recall block, proving recall is no longer silently
    double-gated off when the master is on."""
    monkeypatch.delenv("MAGI_MEMORY_RECALL_ENABLED", raising=False)
    monkeypatch.delenv("MAGI_MEMORY_PREFER_LOCAL_SEARCH", raising=False)
    monkeypatch.setenv("MAGI_MEMORY_ENABLED", "1")
    # Force the pure-python backend so the test never depends on a qmd binary.
    monkeypatch.setenv("MAGI_MEMORY_PREFER_QMD", "0")
    _write(
        tmp_path,
        "memory/daily/2026-06-01.md",
        "decision: we will adopt zebraquux for the billing rollout",
    )
    block = build_cli_memory_recall_block(
        workspace_root=str(tmp_path), query="zebraquux", memory_mode="normal"
    )
    assert block
    assert "<memory-recall" in block and "zebraquux" in block


def test_incognito_blocks_recall(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on_env(monkeypatch)
    _write(tmp_path, "memory/daily/2026-06-01.md", "zebraquux term present")
    assert (
        build_cli_memory_recall_block(
            workspace_root=str(tmp_path), query="zebraquux", memory_mode="incognito"
        )
        == ""
    )


def test_no_hits_returns_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on_env(monkeypatch)
    _write(tmp_path, "memory/daily/2026-06-01.md", "nothing relevant here")
    assert (
        build_cli_memory_recall_block(
            workspace_root=str(tmp_path), query="zebraquux", memory_mode="normal"
        )
        == ""
    )


def test_empty_query_returns_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on_env(monkeypatch)
    _write(tmp_path, "memory/daily/2026-06-01.md", "zebraquux term")
    assert (
        build_cli_memory_recall_block(
            workspace_root=str(tmp_path), query="   ", memory_mode="normal"
        )
        == ""
    )


def test_no_workspace_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _on_env(monkeypatch)
    assert (
        build_cli_memory_recall_block(
            workspace_root=None, query="zebraquux", memory_mode="normal"
        )
        == ""
    )


def test_redaction_applied_to_recall_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _on_env(monkeypatch)
    # Build a secret-shaped fixture at runtime (push-protection safe).
    secret = "Bearer " + "x" * 24
    _write(
        tmp_path,
        "memory/daily/2026-06-01.md",
        f"zebraquux rollout note\nAuthorization: {secret}\n/Users/kevin/private path",
    )
    block = build_cli_memory_recall_block(
        workspace_root=str(tmp_path), query="zebraquux", memory_mode="normal"
    )
    assert block
    assert secret not in block
    assert "/Users/kevin" not in block


def test_byte_budget_caps_block(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on_env(monkeypatch)
    monkeypatch.setenv("MAGI_MEMORY_RECALL_MAX_BYTES", "200")
    big = "zebraquux " + ("filler " * 500)
    _write(tmp_path, "memory/daily/2026-06-01.md", big)
    block = build_cli_memory_recall_block(
        workspace_root=str(tmp_path), query="zebraquux", memory_mode="normal"
    )
    assert block
    assert len(block.encode("utf-8")) <= 200 + 64  # block + fence headroom


def test_empty_tree_skips_reindex_and_search(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gates ON but no indexable memory (no memory/ tree, no top-level
    MEMORY.md/ROOT.md) => "" WITHOUT consulting the backend at all.  Proves the
    cheap empty-tree guard skips the per-turn reindex scan on a fresh workspace.
    """
    _on_env(monkeypatch)
    # Deliberately write nothing the BM25 backend would index.  A non-memory file
    # must not defeat the guard.
    _write(tmp_path, "README.md", "zebraquux mentioned but not under memory/")

    import magi_agent.cli.memory_recall_block as mod

    class _Boom:
        def reindex(self, root: object, **kwargs: object) -> None:
            raise AssertionError("reindex must not run on an empty memory tree")

        def search(self, query: str, *, k: int) -> object:
            raise AssertionError("search must not run on an empty memory tree")

    monkeypatch.setattr(mod, "select_search_backend", lambda config: _Boom())
    assert (
        build_cli_memory_recall_block(
            workspace_root=str(tmp_path), query="zebraquux", memory_mode="normal"
        )
        == ""
    )


def test_empty_memory_dir_skips_reindex_and_search(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An EMPTY ``memory/`` directory (no *.md under it, no top-level files) is
    still "no indexable memory" => "" without touching the backend."""
    _on_env(monkeypatch)
    (tmp_path / "memory").mkdir()

    import magi_agent.cli.memory_recall_block as mod

    class _Boom:
        def reindex(self, root: object, **kwargs: object) -> None:
            raise AssertionError("reindex must not run on an empty memory tree")

        def search(self, query: str, *, k: int) -> object:
            raise AssertionError("search must not run on an empty memory tree")

    monkeypatch.setattr(mod, "select_search_backend", lambda config: _Boom())
    assert (
        build_cli_memory_recall_block(
            workspace_root=str(tmp_path), query="zebraquux", memory_mode="normal"
        )
        == ""
    )


def test_multi_hit_budget_truncates_keeps_top_rank(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Several matching daily files whose combined size exceeds the byte budget
    => the emitted block stays within budget, includes the highest-ranked hit,
    and is truncated (exercises the per-part ``remaining`` decrement / break)."""
    _on_env(monkeypatch)
    monkeypatch.setenv("MAGI_MEMORY_RECALL_MAX_BYTES", "400")
    monkeypatch.setenv("MAGI_MEMORY_RECALL_K", "5")
    # Several distinct matching docs.  The first is densest in the query term so
    # BM25 ranks it highest; each doc is sized so that a FEW (but not all) fit —
    # the loop must append multiple parts, decrementing ``remaining`` per part,
    # then break.  This is the multi-part path, not a single oversized doc.
    _write(
        tmp_path,
        "memory/daily/2026-06-01.md",
        "zebraquux zebraquux zebraquux top hit " + ("pad " * 30),
    )
    for day in range(2, 6):
        _write(
            tmp_path,
            f"memory/daily/2026-06-0{day}.md",
            f"zebraquux note {day} " + ("pad " * 30),
        )
    block = build_cli_memory_recall_block(
        workspace_root=str(tmp_path), query="zebraquux", memory_mode="normal"
    )
    assert block
    # Within budget (block content + fence headroom).
    assert len(block.encode("utf-8")) <= 400 + 64
    # More than one hit was emitted (per-part decrement path, not a lone doc).
    assert block.count("<!--") >= 2
    # The highest-ranked hit is present.
    assert "top hit" in block
    # Truncated: the lowest-ranked doc could not fit alongside the leaders.
    assert "note 5" not in block


def test_fail_soft_when_backend_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _on_env(monkeypatch)
    _write(tmp_path, "memory/daily/2026-06-01.md", "zebraquux term")

    import magi_agent.cli.memory_recall_block as mod

    class _Boom:
        def reindex(self, root: object, **kwargs: object) -> None:
            pass

        def search(self, query: str, *, k: int) -> object:
            raise RuntimeError("backend boom")

    monkeypatch.setattr(mod, "select_search_backend", lambda config: _Boom())
    assert (
        build_cli_memory_recall_block(
            workspace_root=str(tmp_path), query="zebraquux", memory_mode="normal"
        )
        == ""
    )


# --- D1 (N-12 / N-13): process-scope backend cache + qmd bind-first hot path ---


def _clear_backend_cache() -> None:
    from magi_agent.memory.search.backend_cache import clear_search_backend_cache

    clear_search_backend_cache()


def test_recall_reuses_backend_across_turns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The per-turn recall backend is constructed ONCE and reused across turns
    (the H-27 mtime cache and qmd binding survive), and both turns emit a
    byte-identical block."""
    _on_env(monkeypatch)
    _clear_backend_cache()
    _write(
        tmp_path,
        "memory/daily/2026-06-01.md",
        "decision: we will adopt zebraquux for the billing rollout",
    )

    import magi_agent.cli.memory_recall_block as mod
    from magi_agent.memory.search.base import SearchHit

    constructions = {"count": 0}

    class _Fake:
        def reindex(self, root: object, **kwargs: object) -> None:
            pass

        def search(self, query: str, *, k: int) -> object:
            return [
                SearchHit(
                    path="memory/daily/2026-06-01.md",
                    content="decision: we will adopt zebraquux for the billing rollout",
                    score=1.0,
                )
            ]

    def _factory(config: object) -> object:
        constructions["count"] += 1
        return _Fake()

    monkeypatch.setattr(mod, "select_search_backend", _factory)

    first = build_cli_memory_recall_block(
        workspace_root=str(tmp_path), query="zebraquux", memory_mode="normal"
    )
    second = build_cli_memory_recall_block(
        workspace_root=str(tmp_path), query="zebraquux", memory_mode="normal"
    )
    assert first
    assert first == second  # behavior-preservation across turns
    assert constructions["count"] == 1  # counter-based regression


def test_qmd_preferred_recall_never_runs_update_on_hot_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a qmd-like backend the hot path binds once and NEVER calls reindex
    (which is what would run ``qmd update``)."""
    _on_env(monkeypatch)
    _clear_backend_cache()
    _write(tmp_path, "memory/daily/2026-06-01.md", "zebraquux billing note")

    import magi_agent.cli.memory_recall_block as mod
    from magi_agent.memory.search.base import SearchHit

    class _FakeQmd:
        def __init__(self) -> None:
            self.bind_calls = 0
            self.reindex_calls = 0
            self._bound = False

        @property
        def bound(self) -> bool:
            return self._bound

        def bind(self, root: object) -> bool:
            self.bind_calls += 1
            self._bound = True
            return True

        def reindex(self, root: object, **kwargs: object) -> None:
            self.reindex_calls += 1

        def search(self, query: str, *, k: int) -> object:
            return [
                SearchHit(
                    path="memory/daily/2026-06-01.md",
                    content="zebraquux billing note",
                    score=1.0,
                )
            ]

    fake = _FakeQmd()
    monkeypatch.setattr(mod, "select_search_backend", lambda config: fake)

    for _ in range(2):
        build_cli_memory_recall_block(
            workspace_root=str(tmp_path), query="zebraquux", memory_mode="normal"
        )
    assert fake.bind_calls == 1
    assert fake.reindex_calls == 0
