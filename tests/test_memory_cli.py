"""PR2 — ``magi memory`` CLI: optional qmd install + explicit search.

These tests pin the opt-in installer/search helpers in
:mod:`magi_agent.cli.memory_cli`.  No real qmd / brew / npm is invoked: the
``subprocess.run`` and ``shutil.which`` seams are monkeypatched.  Governance: the
hot path is untouched; everything here is explicit and fail-soft.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from magi_agent.cli import memory_cli


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class _FakeCompleted:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# ---------------------------------------------------------------------------
# install_qmd
# ---------------------------------------------------------------------------


def test_install_qmd_already_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(memory_cli.shutil, "which", lambda name: "/opt/homebrew/bin/qmd")

    def explode(*a, **k):  # pragma: no cover - must not shell out when present
        raise AssertionError("must not install when qmd already present")

    monkeypatch.setattr(memory_cli.subprocess, "run", explode)
    ok, method = memory_cli.install_qmd()
    assert ok is True and method == "already-present"


def test_install_qmd_via_brew(monkeypatch: pytest.MonkeyPatch) -> None:
    # qmd absent at first, present after brew install.
    state = {"qmd": False}

    def which(name: str):
        if name == "qmd":
            return "/opt/homebrew/bin/qmd" if state["qmd"] else None
        if name == "brew":
            return "/opt/homebrew/bin/brew"
        return None

    def run(args, **kwargs):
        assert args[:2] == ["brew", "install"]
        state["qmd"] = True
        return _FakeCompleted(0)

    monkeypatch.setattr(memory_cli.shutil, "which", which)
    monkeypatch.setattr(memory_cli.subprocess, "run", run)
    ok, method = memory_cli.install_qmd()
    assert ok is True and method == "brew"


def test_install_qmd_falls_back_to_npm(monkeypatch: pytest.MonkeyPatch) -> None:
    state = {"qmd": False}

    def which(name: str):
        if name == "qmd":
            return "/usr/local/bin/qmd" if state["qmd"] else None
        if name == "npm":
            return "/usr/local/bin/npm"
        return None  # no brew

    def run(args, **kwargs):
        assert args[:3] == ["npm", "install", "-g"]
        assert args[3] == memory_cli._QMD_NPM_PACKAGE
        state["qmd"] = True
        return _FakeCompleted(0)

    monkeypatch.setattr(memory_cli.shutil, "which", which)
    monkeypatch.setattr(memory_cli.subprocess, "run", run)
    ok, method = memory_cli.install_qmd()
    assert ok is True and method == "npm"


def test_install_qmd_no_package_manager(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(memory_cli.shutil, "which", lambda name: None)
    ok, method = memory_cli.install_qmd()
    assert ok is False and method == "no-package-manager"


# ---------------------------------------------------------------------------
# register_collection
# ---------------------------------------------------------------------------


def test_register_collection_uses_auto_register(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write(tmp_path, "memory/daily/a.md", "content")
    from magi_agent.memory.search import qmd as qmd_module

    calls: list[list[str]] = []

    def run(args, **kwargs):
        calls.append(args)
        if args[1:3] == ["collection", "list"]:
            return _FakeCompleted(0, stdout="")  # not yet registered
        return _FakeCompleted(0, stdout="Indexed: 1 new")

    monkeypatch.setattr(memory_cli.shutil, "which", lambda name: "/fake/qmd")
    monkeypatch.setattr(qmd_module.shutil, "which", lambda name: "/fake/qmd")
    monkeypatch.setattr(qmd_module.subprocess, "run", run)

    name = memory_cli.register_collection(tmp_path)
    assert name is not None and name.startswith("magi-mem-")
    # The explicit opt-in path performs `collection add` (auto-register ON).
    assert any(c[1:3] == ["collection", "add"] for c in calls)


def test_register_collection_none_without_memory_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(memory_cli.shutil, "which", lambda name: "/fake/qmd")
    assert memory_cli.register_collection(tmp_path) is None


def test_register_collection_none_without_qmd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write(tmp_path, "memory/a.md", "x")
    monkeypatch.setattr(memory_cli.shutil, "which", lambda name: None)
    assert memory_cli.register_collection(tmp_path) is None


# ---------------------------------------------------------------------------
# write_memory_opt_ins
# ---------------------------------------------------------------------------


def test_write_opt_ins_sets_prefer_qmd_only_without_vector(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = tmp_path / "config.toml"
    monkeypatch.setenv("MAGI_CONFIG", str(cfg))
    path = memory_cli.write_memory_opt_ins(vector=False)
    assert Path(path) == cfg
    data = tomllib.loads(cfg.read_text())
    assert data["memory"]["prefer_qmd"] is True
    assert "vector_search" not in data["memory"]


def test_write_opt_ins_sets_vector_search(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = tmp_path / "config.toml"
    monkeypatch.setenv("MAGI_CONFIG", str(cfg))
    memory_cli.write_memory_opt_ins(vector=True)
    data = tomllib.loads(cfg.read_text())
    assert data["memory"]["prefer_qmd"] is True
    assert data["memory"]["vector_search"] is True


def test_write_opt_ins_preserves_existing_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        'model = "anthropic/claude"\n\n[memory]\nenabled = true\nrecall_k = 12\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("MAGI_CONFIG", str(cfg))
    memory_cli.write_memory_opt_ins(vector=True)
    data = tomllib.loads(cfg.read_text())
    assert data["model"] == "anthropic/claude"
    assert data["memory"]["enabled"] is True
    assert data["memory"]["recall_k"] == 12
    assert data["memory"]["vector_search"] is True


# ---------------------------------------------------------------------------
# init_memory (orchestration)
# ---------------------------------------------------------------------------


def test_init_memory_full_flow_with_vector(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write(tmp_path, "memory/daily/a.md", "content")
    cfg = tmp_path / "config.toml"
    monkeypatch.setenv("MAGI_CONFIG", str(cfg))
    monkeypatch.setattr(memory_cli, "install_qmd", lambda: (True, "already-present"))
    monkeypatch.setattr(memory_cli, "register_collection", lambda root: "magi-mem-abc123")
    embedded = {"called": False}

    def fake_embed():
        embedded["called"] = True
        return True

    monkeypatch.setattr(memory_cli, "generate_embeddings", fake_embed)

    report = memory_cli.init_memory(root=tmp_path, vector=True)
    assert report.qmd_installed is True
    assert report.collection_registered is True
    assert report.embedded is True
    assert embedded["called"] is True
    data = tomllib.loads(cfg.read_text())
    assert data["memory"]["vector_search"] is True


def test_init_memory_skips_embed_without_vector(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write(tmp_path, "memory/a.md", "content")
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setattr(memory_cli, "install_qmd", lambda: (True, "brew"))
    monkeypatch.setattr(memory_cli, "register_collection", lambda root: "magi-mem-x")

    def boom():  # pragma: no cover - must not embed without --vector
        raise AssertionError("must not embed without --vector")

    monkeypatch.setattr(memory_cli, "generate_embeddings", boom)
    report = memory_cli.init_memory(root=tmp_path, vector=False)
    assert report.embedded is False
    assert report.vector_requested is False


def test_init_memory_qmd_install_failure_still_writes_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = tmp_path / "config.toml"
    monkeypatch.setenv("MAGI_CONFIG", str(cfg))
    monkeypatch.setattr(memory_cli, "install_qmd", lambda: (False, "no-package-manager"))
    report = memory_cli.init_memory(root=tmp_path, vector=False)
    assert report.qmd_installed is False
    assert report.config_path is not None
    assert cfg.exists()  # config still persisted so a later qmd install is honored


# ---------------------------------------------------------------------------
# search_memory
# ---------------------------------------------------------------------------


def test_search_memory_maps_hits(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from magi_agent.memory.search.base import SearchHit

    class _Fake:
        def reindex(self, root):
            return None

        def search(self, query, *, k):
            return [SearchHit(path="memory/a.md", content="line one\nline two", score=0.5)]

    monkeypatch.setattr(
        "magi_agent.memory.search.select_search_backend",
        lambda config, *, vector=False: _Fake(),
    )
    out = memory_cli.search_memory(root=tmp_path, query="q", vector=False, k=5)
    assert out == [("memory/a.md", 0.5, "line one line two")]


def test_search_memory_failsoft_on_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def boom(config, *, vector=False):
        raise RuntimeError("backend exploded")

    monkeypatch.setattr("magi_agent.memory.search.select_search_backend", boom)
    assert memory_cli.search_memory(root=tmp_path, query="q", vector=True, k=5) == []
