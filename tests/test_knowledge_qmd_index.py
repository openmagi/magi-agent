"""qmd-accelerated knowledge search + registration (no real qmd global state).

All ``qmd`` subprocess calls are faked, so these tests never touch the operator's
real qmd index.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from magi_agent.knowledge import qmd_index
from magi_agent.knowledge.qmd_index import (
    KB_COLLECTION_PREFIX,
    register_knowledge_collections,
    search_knowledge_via_qmd,
)
from magi_agent.memory.search import qmd as qmd_module


class _FakeCompleted:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _FakeQmd:
    """Routes qmd subcommands to canned output (mirrors the verified CLI)."""

    def __init__(self, *, existing: set[str] | None = None, search_rows: object = None) -> None:
        self.existing = set(existing or ())
        self.search_rows = search_rows if search_rows is not None else []
        self.calls: list[list[str]] = []

    def __call__(self, args, **kwargs):  # subprocess.run signature
        self.calls.append(args)
        sub = args[1:]
        if sub[:2] == ["collection", "list"]:
            return _FakeCompleted(0, stdout="".join(f"qmd://{n}/\n" for n in self.existing))
        if sub[:2] == ["collection", "add"]:
            name = args[args.index("--name") + 1]
            self.existing.add(name)
            return _FakeCompleted(0, stdout="Indexed: 1 new")
        if sub[:1] == ["update"]:
            return _FakeCompleted(0, stdout="updated")
        if sub[:1] in (["search"], ["vsearch"]):
            return _FakeCompleted(0, stdout=json.dumps(self.search_rows))
        return _FakeCompleted(0, stdout="")


def _install_fake(monkeypatch: pytest.MonkeyPatch, fake: _FakeQmd) -> None:
    monkeypatch.setattr(qmd_module.shutil, "which", lambda name: "/fake/qmd")
    monkeypatch.setattr(qmd_module.subprocess, "run", fake)


def _kb_name(root: Path, subdir: str = "knowledge") -> str:
    return qmd_module.collection_name_for(
        (root / subdir).resolve(), prefix=KB_COLLECTION_PREFIX
    )


def _seed(root: Path, rel: str, text: str = "x") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_register_uses_kb_prefix_and_knowledge_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_fake(monkeypatch, _FakeQmd())
    _seed(tmp_path, "knowledge/notes/a.md")

    names = register_knowledge_collections(tmp_path)

    assert names == [_kb_name(tmp_path)]
    assert names[0].startswith("magi-kb-")


def test_search_maps_qmd_rows_to_kb_records(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _seed(tmp_path, "knowledge/notes/a.md")
    name = _kb_name(tmp_path)
    fake = _FakeQmd(
        existing={name},
        search_rows=[{"file": f"qmd://{name}/notes/a.md", "score": 2.5, "snippet": "matched ctx"}],
    )
    _install_fake(monkeypatch, fake)

    records = search_knowledge_via_qmd([tmp_path], "matched", k=5)

    assert records is not None and len(records) == 1
    rec = records[0]
    assert rec["sourceRef"] == "knowledge:knowledge/notes/a.md"
    assert rec["title"] == "knowledge/notes/a.md"
    assert rec["publicPreview"] == "matched ctx"
    # bind() avoids the slow `update` refresh on the search hot path.
    assert not any(c[1:2] == ["update"] for c in fake.calls)


def test_search_returns_none_when_qmd_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(qmd_module.shutil, "which", lambda name: None)
    _seed(tmp_path, "knowledge/notes/a.md")
    assert search_knowledge_via_qmd([tmp_path], "anything", k=5) is None


def test_search_returns_none_when_no_collection_registered(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # qmd present, but the KB collection was never registered and auto_register
    # is off -> None so the caller falls back to the linear scan.
    _install_fake(monkeypatch, _FakeQmd(existing=set()))
    _seed(tmp_path, "knowledge/notes/a.md")
    assert search_knowledge_via_qmd([tmp_path], "anything", k=5, auto_register=False) is None


def test_search_empty_is_authoritative_not_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Collection exists but matches nothing -> [] (do not fall back).
    _seed(tmp_path, "knowledge/notes/a.md")
    name = _kb_name(tmp_path)
    _install_fake(monkeypatch, _FakeQmd(existing={name}, search_rows=[]))
    assert search_knowledge_via_qmd([tmp_path], "nomatch", k=5) == []


def test_auto_register_registers_then_searches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _seed(tmp_path, "knowledge/notes/a.md")
    name = _kb_name(tmp_path)
    fake = _FakeQmd(
        existing=set(),
        search_rows=[{"file": f"qmd://{name}/notes/a.md", "score": 1.0, "snippet": "hit"}],
    )
    _install_fake(monkeypatch, fake)

    records = search_knowledge_via_qmd([tmp_path], "hit", k=5, auto_register=True)

    assert records is not None and len(records) == 1
    assert any(c[1:3] == ["collection", "add"] for c in fake.calls)
