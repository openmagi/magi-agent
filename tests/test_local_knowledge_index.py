"""Local workspace knowledge scan/search + native KnowledgeSearch wiring."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from magi_agent.knowledge.local_index import search_local_knowledge
from magi_agent.plugins.native import knowledge as native_knowledge
from magi_agent.plugins.native.knowledge import knowledge_search
from magi_agent.tools.context import ToolContext


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_search_finds_document_under_knowledge_dir(tmp_path: Path) -> None:
    _write(tmp_path / "knowledge" / "notes" / "tesla.md", "Tesla reported strong margins.")
    records = search_local_knowledge([tmp_path], "tesla margins", limit=5)
    assert len(records) == 1
    rec = records[0]
    assert rec["sourceRef"] == "knowledge:knowledge/notes/tesla.md"
    assert rec["title"] == "knowledge/notes/tesla.md"
    assert rec["metadata"]["collection"] == "notes"
    assert "Tesla" in str(rec["publicPreview"])
    assert rec["metadata"]["publicSafe"] is True


def test_search_also_scans_dot_magi_knowledge(tmp_path: Path) -> None:
    _write(tmp_path / ".magi" / "knowledge" / "kb" / "doc.md", "alpha beta gamma keyword")
    records = search_local_knowledge([tmp_path], "keyword", limit=5)
    assert [r["sourceRef"] for r in records] == ["knowledge:.magi/knowledge/kb/doc.md"]


def test_search_blank_query_or_missing_dir_returns_empty(tmp_path: Path) -> None:
    assert search_local_knowledge([tmp_path], "", limit=5) == []
    assert search_local_knowledge([tmp_path], "anything", limit=5) == []
    _write(tmp_path / "knowledge" / "c" / "d.md", "content here")
    assert search_local_knowledge([tmp_path], "no-such-term", limit=5) == []


def test_search_respects_limit_newest_first(tmp_path: Path) -> None:
    import os
    import time

    for i in range(3):
        p = tmp_path / "knowledge" / "c" / f"doc{i}.md"
        _write(p, f"shared term doc {i}")
        os.utime(p, (1_000 + i * 10, 1_000 + i * 10))
        time.sleep(0)
    records = search_local_knowledge([tmp_path], "shared term", limit=2)
    assert len(records) == 2
    # Newest mtime (doc2) first.
    assert records[0]["sourceRef"].endswith("doc2.md")


def test_search_skips_binary_extensions(tmp_path: Path) -> None:
    _write(tmp_path / "knowledge" / "c" / "image.png", "term inside png bytes")
    assert search_local_knowledge([tmp_path], "term", limit=5) == []


def test_native_knowledge_search_returns_real_records(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Force the linear-scan path (qmd may or may not be installed on the host).
    monkeypatch.setattr(
        native_knowledge, "search_knowledge_via_qmd", lambda *a, **k: None
    )
    _write(tmp_path / "knowledge" / "kb" / "policy.md", "Refund policy: 30 days.")
    ctx = ToolContext(botId="local", workspaceRoot=str(tmp_path))
    result = asyncio.run(knowledge_search({"query": "refund policy"}, ctx))
    assert result.status == "ok"
    sources = result.output["sources"]
    assert len(sources) == 1
    # The boundary opacifies the sourceRef but surfaces the real title (locator)
    # and a real public preview from the on-disk document.
    assert sources[0]["title"] == "knowledge/kb/policy.md"
    assert "Refund" in str(sources[0]["publicPreview"])


def test_native_knowledge_search_no_workspace_root_is_empty_ok(tmp_path: Path) -> None:
    ctx = ToolContext(botId="local")
    result = asyncio.run(knowledge_search({"query": "anything"}, ctx))
    assert result.status == "ok"
    assert result.output["sources"] == ()


def test_native_prefers_qmd_when_available(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A document exists on disk, but qmd returns a DIFFERENT record; the provider
    # must surface the qmd result (qmd preferred over the linear scan).
    _write(tmp_path / "knowledge" / "kb" / "disk.md", "linear scan content")

    def _fake_qmd(roots, query, *, k, auto_register):
        return [{
            "sourceRef": "knowledge:knowledge/kb/qmd.md",
            "title": "knowledge/kb/qmd.md",
            "publicPreview": "qmd ranked content",
            "metadata": {"visibility": "public-safe", "publicSafe": True},
        }]

    monkeypatch.setattr(native_knowledge, "search_knowledge_via_qmd", _fake_qmd)
    ctx = ToolContext(botId="local", workspaceRoot=str(tmp_path))
    result = asyncio.run(knowledge_search({"query": "content"}, ctx))
    assert result.status == "ok"
    assert result.output["sources"][0]["title"] == "knowledge/kb/qmd.md"
    assert "qmd ranked" in result.output["sources"][0]["publicPreview"]


def test_native_falls_back_to_linear_scan_when_qmd_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write(tmp_path / "knowledge" / "kb" / "disk.md", "linear scan content here")
    monkeypatch.setattr(
        native_knowledge, "search_knowledge_via_qmd", lambda *a, **k: None
    )
    ctx = ToolContext(botId="local", workspaceRoot=str(tmp_path))
    result = asyncio.run(knowledge_search({"query": "linear scan"}, ctx))
    assert result.status == "ok"
    assert result.output["sources"][0]["title"] == "knowledge/kb/disk.md"
