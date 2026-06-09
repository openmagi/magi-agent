from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from magi_agent.memory.adapters import hipocampus_readonly
from magi_agent.memory.adapters.hipocampus_readonly import (
    MAGI_MEMORY_QMD_LIVE_ENABLED_ENV,
    HipocampusReadOnlyAdapter,
    HipocampusReadOnlyConfig,
)
from magi_agent.memory.contracts import RecallRequest
from magi_agent.memory.policy import MemoryPolicy


def _write_qmd_json_fixture(root: Path) -> None:
    memory_dir = root / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    (memory_dir / "qmd_results.json").write_text(
        json.dumps(
            {
                "results": [
                    {
                        "path": "memory/daily/2026-05-24.md",
                        "content": "QMD launch plan from JSON file.",
                        "score": 0.97,
                        "context": "daily note",
                    }
                ]
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    (memory_dir / "daily").mkdir(parents=True, exist_ok=True)
    (memory_dir / "daily" / "2026-05-24.md").write_text(
        "launch plan daily note", encoding="utf-8"
    )


def _request(*, max_bytes: int = 240, min_score: float = 0.3) -> RecallRequest:
    return RecallRequest(
        scope={"tenantId": "tenant-1", "botId": "bot-1"},
        query="launch plan",
        purpose="answer_user",
        maxBytes=max_bytes,
        minScore=min_score,
    )


def _policy() -> MemoryPolicy:
    return MemoryPolicy(memory_mode="normal", source_authority="long_term_allowed")


def test_prefer_local_search_uses_search_backend_and_maps_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When ``prefer_local_search`` is ON the adapter sources qmd records from
    the LOCAL ``memory/search`` backend (BM25/qmd), mapping each ``SearchHit``
    to the same record shape — NOT from the QmdClient or the JSON file."""
    from magi_agent.memory.search.base import SearchHit

    monkeypatch.setenv("MAGI_MEMORY_PREFER_LOCAL_SEARCH", "1")
    # Provide a JSON file + would-be client to prove neither is used.
    _write_qmd_json_fixture(tmp_path)

    def _boom_client(*args: object, **kwargs: object) -> object:
        raise AssertionError("QmdClient must not be used when prefer_local_search is ON")

    monkeypatch.setattr(hipocampus_readonly, "QmdClient", _boom_client)

    captured: dict[str, object] = {}

    class _FakeBackend:
        def reindex(self, root: object, **kwargs: object) -> None:
            captured["reindexed"] = str(root)

        def search(self, query: str, *, k: int) -> list[SearchHit]:
            captured["query"] = query
            captured["k"] = k
            return [
                SearchHit(
                    path="memory/daily/2026-05-24.md",
                    content=(
                        "LOCAL launch plan from search backend.\n"
                        "Authorization: Bearer local-secret-token\n"
                        "/Users/kevin/private must not leak"
                    ),
                    score=0.93,
                ),
            ]

    monkeypatch.setattr(
        hipocampus_readonly,
        "select_search_backend",
        lambda config: _FakeBackend(),
    )

    adapter = HipocampusReadOnlyAdapter(
        HipocampusReadOnlyConfig(workspace_root=tmp_path, enabled=True)
    )
    result = asyncio.run(adapter.search(_request(), policy=_policy()))

    assert captured.get("query") == "launch plan"
    qmd_records = [
        record
        for record in result.records
        if record.custom_metadata.get("sourceKind") == "qmd_search"
    ]
    assert qmd_records, "prefer_local_search should surface search-backend records"
    assert any("LOCAL launch plan from search backend." in r.body for r in qmd_records)
    assert all("from JSON file" not in r.body for r in qmd_records)
    rendered = result.model_dump_json(by_alias=True)
    for leaked in ("Bearer local-secret-token", "/Users/kevin", "Authorization:"):
        assert leaked not in rendered


def test_prefer_local_search_off_falls_back_to_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default (prefer_local_search OFF): the search backend is never consulted;
    the JSON-file path supplies records (byte-identical to before)."""
    monkeypatch.delenv("MAGI_MEMORY_PREFER_LOCAL_SEARCH", raising=False)
    monkeypatch.delenv(MAGI_MEMORY_QMD_LIVE_ENABLED_ENV, raising=False)
    _write_qmd_json_fixture(tmp_path)

    def _boom_select(*args: object, **kwargs: object) -> object:
        raise AssertionError("select_search_backend must not run when flag is OFF")

    monkeypatch.setattr(hipocampus_readonly, "select_search_backend", _boom_select)

    adapter = HipocampusReadOnlyAdapter(
        HipocampusReadOnlyConfig(workspace_root=tmp_path, enabled=True)
    )
    result = asyncio.run(adapter.search(_request(), policy=_policy()))
    qmd_records = [
        record
        for record in result.records
        if record.custom_metadata.get("sourceKind") == "qmd_search"
    ]
    assert any("QMD launch plan from JSON file." in r.body for r in qmd_records)


def test_prefer_local_search_fail_soft_when_backend_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A search-backend error must never break recall — it yields no qmd
    records (and does NOT silently fall through to the JSON file)."""
    monkeypatch.setenv("MAGI_MEMORY_PREFER_LOCAL_SEARCH", "1")
    _write_qmd_json_fixture(tmp_path)

    class _ExplodingBackend:
        def reindex(self, root: object, **kwargs: object) -> None:
            pass

        def search(self, query: str, *, k: int) -> object:
            raise RuntimeError("backend boom")

    monkeypatch.setattr(
        hipocampus_readonly,
        "select_search_backend",
        lambda config: _ExplodingBackend(),
    )

    adapter = HipocampusReadOnlyAdapter(
        HipocampusReadOnlyConfig(workspace_root=tmp_path, enabled=True)
    )
    result = asyncio.run(adapter.search(_request(), policy=_policy()))
    qmd_records = [
        record
        for record in result.records
        if record.custom_metadata.get("sourceKind") == "qmd_search"
    ]
    assert qmd_records == []


def test_gate_off_reads_json_file_and_does_not_call_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(MAGI_MEMORY_QMD_LIVE_ENABLED_ENV, raising=False)
    _write_qmd_json_fixture(tmp_path)

    # If the client is constructed when the gate is OFF, fail loudly.
    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError("QmdClient must not be used when the gate is OFF")

    monkeypatch.setattr(hipocampus_readonly, "QmdClient", _boom)

    adapter = HipocampusReadOnlyAdapter(
        HipocampusReadOnlyConfig(workspace_root=tmp_path, enabled=True)
    )
    result = asyncio.run(adapter.search(_request(), policy=_policy()))

    qmd_records = [
        record
        for record in result.records
        if record.custom_metadata.get("sourceKind") == "qmd_search"
    ]
    assert qmd_records, "gate OFF should still surface JSON-derived qmd records"
    assert any("QMD launch plan from JSON file." in r.body for r in qmd_records)


def test_gate_on_uses_client_with_redaction_and_min_score(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(MAGI_MEMORY_QMD_LIVE_ENABLED_ENV, "1")
    # Provide a JSON file too, to prove the live path is what is used (not JSON).
    _write_qmd_json_fixture(tmp_path)

    captured: dict[str, object] = {}

    class _FakeClient:
        def __init__(self, *, endpoint: object = None, timeout_s: float = 5.0) -> None:
            captured["constructed"] = True

        def query(
            self,
            text: str,
            *,
            collection: str,
            limit: int = 10,
            min_score: float = 0.0,
        ) -> list[dict]:
            captured["text"] = text
            captured["collection"] = collection
            captured["min_score"] = min_score
            captured["limit"] = limit
            return [
                {
                    "path": "memory/daily/2026-06-08.md",
                    "content": (
                        "LIVE launch plan recall.\n"
                        "Authorization: Bearer live-secret-token-value\n"
                        "/Users/kevin/private/path must not leak"
                    ),
                    "score": 0.88,
                    "context": "live daily note",
                },
            ]

    monkeypatch.setattr(hipocampus_readonly, "QmdClient", _FakeClient)

    adapter = HipocampusReadOnlyAdapter(
        HipocampusReadOnlyConfig(workspace_root=tmp_path, enabled=True)
    )
    result = asyncio.run(adapter.search(_request(min_score=0.5), policy=_policy()))

    assert captured.get("constructed") is True
    assert captured.get("text") == "launch plan"
    assert captured.get("min_score") == 0.5

    qmd_records = [
        record
        for record in result.records
        if record.custom_metadata.get("sourceKind") == "qmd_search"
    ]
    assert qmd_records, "gate ON should surface live qmd records"
    assert any("LIVE launch plan recall." in r.body for r in qmd_records)
    # JSON-file content must NOT appear when the live path is active.
    assert all("from JSON file" not in r.body for r in qmd_records)

    rendered = result.model_dump_json(by_alias=True)
    for leaked in ("Bearer live-secret-token-value", "/Users/kevin", "Authorization:"):
        assert leaked not in rendered


def test_gate_on_filters_below_min_score(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(MAGI_MEMORY_QMD_LIVE_ENABLED_ENV, "1")
    _write_qmd_json_fixture(tmp_path)

    class _FakeClient:
        def __init__(self, *, endpoint: object = None, timeout_s: float = 5.0) -> None:
            pass

        def query(self, text: str, *, collection: str, limit: int = 10, min_score: float = 0.0) -> list[dict]:
            # Adapter should pass request.min_score through; we honor it here.
            return [
                item
                for item in [
                    {"path": "memory/daily/a.md", "content": "launch plan high", "score": 0.9, "context": ""},
                ]
                if item["score"] >= min_score
            ]

    monkeypatch.setattr(hipocampus_readonly, "QmdClient", _FakeClient)

    adapter = HipocampusReadOnlyAdapter(
        HipocampusReadOnlyConfig(workspace_root=tmp_path, enabled=True)
    )
    result = asyncio.run(adapter.search(_request(min_score=0.95), policy=_policy()))

    qmd_records = [
        record
        for record in result.records
        if record.custom_metadata.get("sourceKind") == "qmd_search"
    ]
    assert qmd_records == []


def test_gate_on_fail_open_when_client_returns_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(MAGI_MEMORY_QMD_LIVE_ENABLED_ENV, "1")
    _write_qmd_json_fixture(tmp_path)

    class _EmptyClient:
        def __init__(self, *, endpoint: object = None, timeout_s: float = 5.0) -> None:
            pass

        def query(self, text: str, *, collection: str, limit: int = 10, min_score: float = 0.0) -> list[dict]:
            return []

    monkeypatch.setattr(hipocampus_readonly, "QmdClient", _EmptyClient)

    adapter = HipocampusReadOnlyAdapter(
        HipocampusReadOnlyConfig(workspace_root=tmp_path, enabled=True)
    )
    # Live path empty -> no qmd records, no JSON fallback, no exception.
    result = asyncio.run(adapter.search(_request(), policy=_policy()))
    qmd_records = [
        record
        for record in result.records
        if record.custom_metadata.get("sourceKind") == "qmd_search"
    ]
    assert qmd_records == []
