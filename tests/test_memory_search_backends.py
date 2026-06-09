"""PR2 — SearchBackend abstraction: BM25 ranking, qmd wrapper, selection.

Read-side only; nothing here wires the backends into the agent loop.  These
tests pin the governance invariant for PR2: the DEFAULT pure-Python BM25 backend
actually works with no external binary, the qmd wrapper degrades gracefully, and
``select_search_backend`` picks deterministically from a resolved config.
"""
from __future__ import annotations

import json
import shutil
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
# QmdBackend — real qmd CLI contract: per-workspace collection + scoped search
# ---------------------------------------------------------------------------


class _FakeCompleted:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _FakeQmd:
    """Routes qmd subcommands to canned output and records invocations.

    Models the verified CLI: ``collection list`` lists ``qmd://<name>/`` lines,
    ``collection add <path> --name <name>`` registers+indexes, ``update <name>``
    refreshes, ``search <q> --json`` emits cross-collection rows.
    """

    def __init__(self, *, existing: set[str] | None = None, search_rows: object = None) -> None:
        self.existing = set(existing or ())
        self.search_rows = search_rows if search_rows is not None else []
        self.calls: list[list[str]] = []

    def __call__(self, args, **kwargs):  # subprocess.run signature
        assert isinstance(args, list)
        assert kwargs.get("shell") in (None, False)
        self.calls.append(args)
        sub = args[1:]
        if sub[:2] == ["collection", "list"]:
            return _FakeCompleted(0, stdout="".join(f"qmd://{n}/\n" for n in self.existing))
        if sub[:2] == ["collection", "add"]:
            # args: [bin, collection, add, <path>, --name, <name>]
            name = args[args.index("--name") + 1]
            self.existing.add(name)
            return _FakeCompleted(0, stdout="Indexed: 1 new")
        if sub[:1] == ["update"]:
            return _FakeCompleted(0, stdout="updated")
        if sub[:1] == ["search"]:
            return _FakeCompleted(0, stdout=json.dumps(self.search_rows))
        return _FakeCompleted(0, stdout="")


def _mk_memory(tmp_path: Path) -> Path:
    """Create a memory/ dir so reindex registers a collection, return root."""
    _write(tmp_path, "memory/daily/a.md", "placeholder")
    return tmp_path


def test_qmd_collection_name_is_deterministic_and_unique() -> None:
    a = qmd_module.collection_name_for(Path("/bots/alice/memory"))
    b = qmd_module.collection_name_for(Path("/bots/bob/memory"))
    assert a.startswith("magi-mem-") and b.startswith("magi-mem-")
    assert a != b  # same basename "memory", different abspath -> distinct names
    assert qmd_module.collection_name_for(Path("/bots/alice/memory")) == a  # stable


def test_qmd_reindex_registers_named_collection(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(qmd_module.shutil, "which", lambda name: "/fake/qmd")
    fake = _FakeQmd()
    monkeypatch.setattr(qmd_module.subprocess, "run", fake)

    root = _mk_memory(tmp_path)
    expected = qmd_module.collection_name_for(root / "memory")
    QmdBackend().reindex(root)

    add_calls = [c for c in fake.calls if c[1:3] == ["collection", "add"]]
    assert len(add_calls) == 1
    add = add_calls[0]
    assert add[-2:] == ["--name", expected]
    assert add[3] == str(root / "memory")
    # add already builds the BM25 index -> no slow update/embed on first register.
    assert not any(c[1:2] == ["update"] for c in fake.calls)
    assert not any("embed" in c for c in fake.calls)


def test_qmd_reindex_idempotent_refreshes_when_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(qmd_module.shutil, "which", lambda name: "/fake/qmd")
    root = _mk_memory(tmp_path)
    name = qmd_module.collection_name_for(root / "memory")
    fake = _FakeQmd(existing={name})
    monkeypatch.setattr(qmd_module.subprocess, "run", fake)

    QmdBackend().reindex(root)
    # Already-registered -> refresh via scoped update, not a second add.
    assert any(c[1:] == ["update", name] for c in fake.calls)
    assert not any(c[1:3] == ["collection", "add"] for c in fake.calls)


def test_qmd_search_scopes_to_our_collection_and_maps_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(qmd_module.shutil, "which", lambda name: "/fake/qmd")
    root = _mk_memory(tmp_path)
    name = qmd_module.collection_name_for(root / "memory")
    rows = [
        {"docid": "#1", "score": 0.2, "file": f"qmd://{name}/daily/a.md", "snippet": "alpha"},
        {"docid": "#2", "score": 0.9, "file": f"qmd://{name}/ROOT.md", "snippet": "root"},
        # foreign collection — must be excluded.
        {"docid": "#3", "score": 0.99, "file": "qmd://clawy-memory/other.md", "snippet": "nope"},
    ]
    fake = _FakeQmd(existing={name}, search_rows=rows)
    monkeypatch.setattr(qmd_module.subprocess, "run", fake)

    backend = QmdBackend()
    backend.reindex(root)
    hits = backend.search("anything", k=10)

    assert [(h.path, h.content) for h in hits] == [
        ("memory/ROOT.md", "root"),
        ("memory/daily/a.md", "alpha"),
    ]  # scoped to our collection, sorted desc, file->memory/<relpath>, snippet->content
    assert all(isinstance(h, SearchHit) for h in hits)
    # search uses BM25 keyword command with JSON output.
    search_calls = [c for c in fake.calls if c[1:2] == ["search"]]
    assert search_calls and "--json" in search_calls[0]


def test_qmd_search_before_reindex_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(qmd_module.shutil, "which", lambda name: "/fake/qmd")
    fake = _FakeQmd(search_rows=[{"score": 1.0, "file": "qmd://x/a.md", "snippet": "s"}])
    monkeypatch.setattr(qmd_module.subprocess, "run", fake)
    # No reindex -> no collection scope -> empty, without even shelling out.
    assert QmdBackend().search("q", k=5) == []
    assert not any(c[1:2] == ["search"] for c in fake.calls)


def test_qmd_respects_k(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(qmd_module.shutil, "which", lambda name: "/fake/qmd")
    root = _mk_memory(tmp_path)
    name = qmd_module.collection_name_for(root / "memory")
    rows = [
        {"score": float(i), "file": f"qmd://{name}/d{i}.md", "snippet": str(i)}
        for i in range(5)
    ]
    monkeypatch.setattr(qmd_module.subprocess, "run", _FakeQmd(existing={name}, search_rows=rows))
    backend = QmdBackend()
    backend.reindex(root)
    assert len(backend.search("q", k=2)) == 2
    assert backend.search("q", k=0) == []


def test_qmd_nonzero_exit_returns_empty(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(qmd_module.shutil, "which", lambda name: "/fake/qmd")
    root = _mk_memory(tmp_path)
    name = qmd_module.collection_name_for(root / "memory")

    def fake(args, **kw):
        if args[1:2] == ["collection"] or args[1:2] == ["update"]:
            return _FakeCompleted(0, stdout=f"qmd://{name}/\n")
        return _FakeCompleted(2, stdout="", stderr="boom")

    monkeypatch.setattr(qmd_module.subprocess, "run", fake)
    backend = QmdBackend()
    backend.reindex(root)
    assert backend.search("q", k=5) == []


def test_qmd_garbage_output_returns_empty(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(qmd_module.shutil, "which", lambda name: "/fake/qmd")
    root = _mk_memory(tmp_path)
    name = qmd_module.collection_name_for(root / "memory")
    fake = _FakeQmd(existing={name}, search_rows=None)
    fake.search_rows = "<<<not json>>>"  # raw garbage; _FakeQmd json.dumps would quote it

    def run(args, **kw):
        if args[1:2] == ["search"]:
            return _FakeCompleted(0, stdout="not json at all")
        return _FakeQmd(existing={name}).__call__(args, **kw)

    monkeypatch.setattr(qmd_module.subprocess, "run", run)
    backend = QmdBackend()
    backend.reindex(root)
    assert backend.search("q", k=5) == []


def test_qmd_missing_binary_is_unusable_no_crash(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(qmd_module.shutil, "which", lambda name: None)

    def explode(*args, **kwargs):  # pragma: no cover - must never be called
        raise AssertionError("subprocess.run must not be called when qmd is absent")

    monkeypatch.setattr(qmd_module.subprocess, "run", explode)
    backend = QmdBackend()
    assert backend.available is False
    backend.reindex(_mk_memory(tmp_path))  # no-op, no crash
    assert backend.search("q", k=5) == []


def test_qmd_subprocess_error_returns_empty(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(qmd_module.shutil, "which", lambda name: "/fake/qmd")
    root = _mk_memory(tmp_path)
    name = qmd_module.collection_name_for(root / "memory")

    def run(args, **kwargs):
        if args[1:2] == ["search"]:
            raise qmd_module.subprocess.TimeoutExpired(cmd=args, timeout=1)
        return _FakeCompleted(0, stdout=f"qmd://{name}/\n")

    monkeypatch.setattr(qmd_module.subprocess, "run", run)
    backend = QmdBackend()
    backend.reindex(root)
    assert backend.search("q", k=5) == []


def test_qmd_drops_rows_with_bad_types(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(qmd_module.shutil, "which", lambda name: "/fake/qmd")
    root = _mk_memory(tmp_path)
    name = qmd_module.collection_name_for(root / "memory")
    rows = [
        {"score": 0.5, "file": f"qmd://{name}/ok.md", "snippet": "good"},
        {"score": 0.5, "file": 123, "snippet": "bad file"},
        {"file": f"qmd://{name}/no-score.md", "snippet": "x"},
        {"score": True, "file": f"qmd://{name}/bool.md", "snippet": "x"},
        "not a dict",
    ]
    monkeypatch.setattr(qmd_module.subprocess, "run", _FakeQmd(existing={name}, search_rows=rows))
    backend = QmdBackend()
    backend.reindex(root)
    assert [h.path for h in backend.search("q", k=10)] == ["memory/ok.md"]


def test_qmd_capabilities_no_vector() -> None:
    caps = QmdBackend().capabilities
    assert caps.name == "qmd"
    assert caps.supports_vector is False


@pytest.mark.skipif(shutil.which("qmd") is None, reason="qmd binary not installed")
def test_qmd_real_binary_end_to_end(tmp_path: Path) -> None:
    """Live add/search/remove cycle against the real qmd, with cleanup."""
    import subprocess as sp

    root = tmp_path
    _write(root, "memory/daily/note.md", "zebraquux appears here once")
    name = qmd_module.collection_name_for(root / "memory")
    backend = QmdBackend()
    try:
        backend.reindex(root)
        hits = backend.search("zebraquux", k=5)
        assert [h.path for h in hits] == ["memory/daily/note.md"]
    finally:
        sp.run(["qmd", "collection", "remove", name], stdin=sp.DEVNULL,
               capture_output=True, text=True, timeout=30, check=False)
        listed = sp.run(["qmd", "collection", "list"], stdin=sp.DEVNULL,
                        capture_output=True, text=True, timeout=30, check=False)
        assert name not in listed.stdout  # left nothing behind
