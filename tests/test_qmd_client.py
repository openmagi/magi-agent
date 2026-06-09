from __future__ import annotations

import pytest

from magi_agent.memory.qmd_client import QmdClient, QmdUnavailable


def test_query_returns_parsed_results_when_raw_query_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    client = QmdClient(endpoint="http://qmd.local/search")

    def _fake_raw_query(text: str, **kwargs: object) -> dict:
        return {
            "results": [
                {
                    "path": "memory/daily/2026-06-08.md",
                    "content": "launch plan note",
                    "score": 0.91,
                    "context": "daily note",
                }
            ]
        }

    monkeypatch.setattr(client, "_raw_query", _fake_raw_query)

    results = client.query("launch plan", collection="clawy-memory")

    assert results == [
        {
            "path": "memory/daily/2026-06-08.md",
            "content": "launch plan note",
            "score": 0.91,
            "context": "daily note",
        }
    ]


def test_query_returns_empty_list_and_does_not_raise_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = QmdClient(endpoint="http://qmd.local/search")

    def _raising_raw_query(text: str, **kwargs: object) -> dict:
        raise QmdUnavailable("no endpoint")

    monkeypatch.setattr(client, "_raw_query", _raising_raw_query)

    results = client.query("launch plan", collection="clawy-memory")

    assert results == []


def test_query_returns_empty_list_on_arbitrary_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = QmdClient(endpoint="http://qmd.local/search")

    def _boom(text: str, **kwargs: object) -> dict:
        raise RuntimeError("connection reset")

    monkeypatch.setattr(client, "_raw_query", _boom)

    assert client.query("anything", collection="clawy-memory") == []


def test_query_filters_results_below_min_score(monkeypatch: pytest.MonkeyPatch) -> None:
    client = QmdClient(endpoint="http://qmd.local/search")

    def _fake_raw_query(text: str, **kwargs: object) -> dict:
        return {
            "results": [
                {"path": "a.md", "content": "high", "score": 0.9, "context": ""},
                {"path": "b.md", "content": "low", "score": 0.1, "context": ""},
                {"path": "c.md", "content": "edge", "score": 0.5, "context": ""},
            ]
        }

    monkeypatch.setattr(client, "_raw_query", _fake_raw_query)

    results = client.query("q", collection="clawy-memory", min_score=0.5)

    paths = [item["path"] for item in results]
    assert paths == ["a.md", "c.md"]


def test_query_drops_malformed_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    client = QmdClient(endpoint="http://qmd.local/search")

    def _fake_raw_query(text: str, **kwargs: object) -> dict:
        return {
            "results": [
                {"path": "ok.md", "content": "fine", "score": 0.8},
                {"path": 123, "content": "bad path", "score": 0.8},
                {"path": "missing-score.md", "content": "no score"},
                "not-a-dict",
            ]
        }

    monkeypatch.setattr(client, "_raw_query", _fake_raw_query)

    results = client.query("q", collection="clawy-memory")

    assert [item["path"] for item in results] == ["ok.md"]
    assert results[0]["context"] == ""


def test_raw_query_raises_when_no_endpoint_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MAGI_QMD_ENDPOINT", raising=False)
    client = QmdClient()

    with pytest.raises(QmdUnavailable):
        client._raw_query("q", collection="clawy-memory", limit=10)


def test_query_returns_empty_when_no_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAGI_QMD_ENDPOINT", raising=False)
    client = QmdClient()

    # fail-open: no endpoint -> _raw_query raises QmdUnavailable -> query returns []
    assert client.query("q", collection="clawy-memory") == []


def test_endpoint_falls_back_to_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_QMD_ENDPOINT", "http://from-env.local/search")
    client = QmdClient()

    assert client.endpoint == "http://from-env.local/search"
