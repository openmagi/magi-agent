"""PR2 — SearchBackend abstraction: BM25 ranking, qmd wrapper, selection.

Read-side only; nothing here wires the backends into the agent loop.  These
tests pin the governance invariant for PR2: the DEFAULT pure-Python BM25 backend
actually works with no external binary, the qmd wrapper degrades gracefully, and
``select_search_backend`` picks deterministically from a resolved config.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from magi_agent.memory.config import resolve_memory_config
from magi_agent.memory.search import (
    PyBM25Backend,
    QmdBackend,
    SearchHit,
    select_search_backend,
)
from magi_agent.memory.search import qmd as qmd_module
import magi_agent.memory.search as search_pkg


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def memory_root(tmp_path: Path) -> Path:
    """A small workspace with a memory/ tree + top-level files."""
    _write(root := tmp_path, "memory/daily/2026-06-01.md",
           "kubernetes kubernetes kubernetes deploy rollout on the cluster kubernetes")
    _write(root, "memory/daily/2026-06-02.md",
           "we mentioned kubernetes once today and talked about billing mostly billing")
    _write(root, "memory/weekly/2026-W23.md",
           "weekly summary about stripe billing and credit reconciliation only")
    _write(root, "MEMORY.md", "top level memory digest covering kubernetes provisioning")
    _write(root, "ROOT.md", "root pointer file with general orientation notes")
    return root


# ---------------------------------------------------------------------------
# BM25 ranking correctness
# ---------------------------------------------------------------------------


def test_bm25_ranks_term_frequency_desc_and_excludes_non_matches(memory_root: Path) -> None:
    backend = PyBM25Backend()
    backend.reindex(memory_root)

    hits = backend.search("kubernetes", k=10)
    paths = [hit.path for hit in hits]

    # The doc mentioning the term many times outranks the one mentioning it once.
    assert paths[0] == "memory/daily/2026-06-01.md"
    assert "memory/daily/2026-06-02.md" in paths
    assert paths.index("memory/daily/2026-06-01.md") < paths.index("memory/daily/2026-06-02.md")

    # A doc that never mentions the term is excluded entirely.
    assert "memory/weekly/2026-W23.md" not in paths
    assert "ROOT.md" not in paths

    # Scores are strictly descending and positive.
    scores = [hit.score for hit in hits]
    assert all(s > 0 for s in scores)
    assert scores == sorted(scores, reverse=True)


def test_bm25_idf_weights_rare_terms_higher(tmp_path: Path) -> None:
    # 'billing' appears in every doc (common -> low idf); 'zebra' is rare.
    _write(tmp_path, "memory/a.md", "billing billing zebra")
    _write(tmp_path, "memory/b.md", "billing billing billing")
    _write(tmp_path, "memory/c.md", "billing notes only")

    backend = PyBM25Backend()
    backend.reindex(tmp_path)

    rare = backend.search("zebra", k=5)
    common = backend.search("billing", k=5)

    assert rare and common
    # The single rare-term hit should outscore the top common-term hit, because
    # idf for the rare term is much larger.
    assert rare[0].path == "memory/a.md"
    assert rare[0].score > common[0].score


def test_bm25_respects_k_and_returns_searchhit_shape(memory_root: Path) -> None:
    backend = PyBM25Backend()
    backend.reindex(memory_root)
    hits = backend.search("kubernetes", k=1)
    assert len(hits) == 1
    hit = hits[0]
    assert isinstance(hit, SearchHit)
    assert isinstance(hit.path, str) and isinstance(hit.content, str)
    assert isinstance(hit.score, float)


def test_bm25_indexes_toplevel_memory_and_root(tmp_path: Path) -> None:
    _write(tmp_path, "MEMORY.md", "unicornterm appears here")
    _write(tmp_path, "ROOT.md", "rootonlyterm appears here")
    backend = PyBM25Backend()
    backend.reindex(tmp_path)
    assert [h.path for h in backend.search("unicornterm", k=5)] == ["MEMORY.md"]
    assert [h.path for h in backend.search("rootonlyterm", k=5)] == ["ROOT.md"]


def test_bm25_empty_or_unindexed_returns_empty(tmp_path: Path) -> None:
    backend = PyBM25Backend()
    # Search before reindex.
    assert backend.search("anything", k=5) == []
    # Reindex over empty workspace.
    backend.reindex(tmp_path)
    assert backend.search("anything", k=5) == []
    # Empty query / non-positive k.
    _write(tmp_path, "memory/x.md", "content here")
    backend.reindex(tmp_path)
    assert backend.search("   ", k=5) == []
    assert backend.search("content", k=0) == []


def test_bm25_capabilities_no_vector() -> None:
    caps = PyBM25Backend().capabilities
    assert caps.name == "pybm25"
    assert caps.supports_vector is False


# ---------------------------------------------------------------------------
# reindex picks up new files
# ---------------------------------------------------------------------------


def test_bm25_reindex_reflects_filesystem(tmp_path: Path) -> None:
    _write(tmp_path, "memory/daily/one.md", "alpha beta")
    backend = PyBM25Backend()
    backend.reindex(tmp_path)
    assert [h.path for h in backend.search("gamma", k=5)] == []

    _write(tmp_path, "memory/daily/two.md", "gamma gamma delta")
    backend.reindex(tmp_path)
    assert [h.path for h in backend.search("gamma", k=5)] == ["memory/daily/two.md"]


# ---------------------------------------------------------------------------
# select_search_backend
# ---------------------------------------------------------------------------


def test_select_prefers_qmd_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(search_pkg.shutil, "which", lambda name: "/usr/local/bin/qmd")
    config = resolve_memory_config(env={}, config={"memory": {"prefer_qmd": True}})
    assert config.prefer_qmd is True
    backend = select_search_backend(config)
    assert isinstance(backend, QmdBackend)


def test_select_falls_back_to_bm25_when_qmd_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(search_pkg.shutil, "which", lambda name: None)
    config = resolve_memory_config(env={}, config={"memory": {"prefer_qmd": True}})
    backend = select_search_backend(config)
    assert isinstance(backend, PyBM25Backend)


def test_select_bm25_when_prefer_qmd_false_even_if_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(search_pkg.shutil, "which", lambda name: "/usr/local/bin/qmd")
    config = resolve_memory_config(env={}, config={"memory": {"prefer_qmd": False}})
    assert config.prefer_qmd is False
    backend = select_search_backend(config)
    assert isinstance(backend, PyBM25Backend)


# ---------------------------------------------------------------------------
# QmdBackend — subprocess parsing + graceful degradation
# ---------------------------------------------------------------------------


class _FakeCompleted:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_qmd_parses_results_envelope(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(qmd_module.shutil, "which", lambda name: "/fake/qmd")
    payload = {
        "results": [
            {"path": "memory/daily/a.md", "content": "alpha", "score": 0.2},
            {"path": "memory/daily/b.md", "content": "beta", "score": 0.9},
        ]
    }

    def fake_run(args, **kwargs):
        assert "shell" not in kwargs or kwargs["shell"] is False
        assert isinstance(args, list)
        return _FakeCompleted(0, stdout=json.dumps(payload))

    monkeypatch.setattr(qmd_module.subprocess, "run", fake_run)

    backend = QmdBackend()
    backend.reindex(tmp_path)
    hits = backend.search("anything", k=10)
    # Sorted by score desc.
    assert [h.path for h in hits] == ["memory/daily/b.md", "memory/daily/a.md"]
    assert all(isinstance(h, SearchHit) for h in hits)


def test_qmd_parses_bare_list_output(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(qmd_module.shutil, "which", lambda name: "/fake/qmd")
    rows = [{"path": "memory/x.md", "content": "hit", "score": 1.5}]
    monkeypatch.setattr(
        qmd_module.subprocess, "run",
        lambda args, **kw: _FakeCompleted(0, stdout=json.dumps(rows)),
    )
    backend = QmdBackend()
    hits = backend.search("q", k=5)
    assert [(h.path, h.score) for h in hits] == [("memory/x.md", 1.5)]


def test_qmd_nonzero_exit_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(qmd_module.shutil, "which", lambda name: "/fake/qmd")
    monkeypatch.setattr(
        qmd_module.subprocess, "run",
        lambda args, **kw: _FakeCompleted(2, stdout="", stderr="boom"),
    )
    assert QmdBackend().search("q", k=5) == []


def test_qmd_garbage_output_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(qmd_module.shutil, "which", lambda name: "/fake/qmd")
    monkeypatch.setattr(
        qmd_module.subprocess, "run",
        lambda args, **kw: _FakeCompleted(0, stdout="not json at all"),
    )
    assert QmdBackend().search("q", k=5) == []


def test_qmd_missing_binary_is_unusable_no_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(qmd_module.shutil, "which", lambda name: None)

    def explode(*args, **kwargs):  # pragma: no cover - must never be called
        raise AssertionError("subprocess.run must not be called when qmd is absent")

    monkeypatch.setattr(qmd_module.subprocess, "run", explode)
    backend = QmdBackend()
    assert backend.available is False
    assert backend.search("q", k=5) == []
    backend.reindex(Path("/tmp"))  # no-op, no crash


def test_qmd_subprocess_error_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(qmd_module.shutil, "which", lambda name: "/fake/qmd")

    def raise_timeout(args, **kwargs):
        raise qmd_module.subprocess.TimeoutExpired(cmd=args, timeout=1)

    monkeypatch.setattr(qmd_module.subprocess, "run", raise_timeout)
    assert QmdBackend().search("q", k=5) == []


def test_qmd_drops_rows_with_bad_types(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(qmd_module.shutil, "which", lambda name: "/fake/qmd")
    rows = [
        {"path": "ok.md", "content": "good", "score": 0.5},
        {"path": 123, "content": "bad path", "score": 0.5},
        {"path": "no-score.md", "content": "x"},
        {"path": "bool-score.md", "content": "x", "score": True},
        "not a dict",
    ]
    monkeypatch.setattr(
        qmd_module.subprocess, "run",
        lambda args, **kw: _FakeCompleted(0, stdout=json.dumps(rows)),
    )
    hits = QmdBackend().search("q", k=10)
    assert [h.path for h in hits] == ["ok.md"]


def test_qmd_capabilities_no_vector() -> None:
    caps = QmdBackend().capabilities
    assert caps.name == "qmd"
    assert caps.supports_vector is False
