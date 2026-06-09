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

    results = client.query("launch plan", collection="magi-memory")

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

    results = client.query("launch plan", collection="magi-memory")

    assert results == []


def test_query_returns_empty_list_on_arbitrary_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = QmdClient(endpoint="http://qmd.local/search")

    def _boom(text: str, **kwargs: object) -> dict:
        raise RuntimeError("connection reset")

    monkeypatch.setattr(client, "_raw_query", _boom)

    assert client.query("anything", collection="magi-memory") == []


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

    results = client.query("q", collection="magi-memory", min_score=0.5)

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

    results = client.query("q", collection="magi-memory")

    assert [item["path"] for item in results] == ["ok.md"]
    assert results[0]["context"] == ""


def test_raw_query_raises_when_no_endpoint_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MAGI_QMD_ENDPOINT", raising=False)
    client = QmdClient()

    with pytest.raises(QmdUnavailable):
        client._raw_query("q", collection="magi-memory", limit=10)


def test_query_returns_empty_when_no_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAGI_QMD_ENDPOINT", raising=False)
    client = QmdClient()

    # fail-open: no endpoint -> _raw_query raises QmdUnavailable -> query returns []
    assert client.query("q", collection="magi-memory") == []


def test_endpoint_falls_back_to_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_QMD_ENDPOINT", "http://from-env.local/search")
    client = QmdClient()

    assert client.endpoint == "http://from-env.local/search"


# ---------------------------------------------------------------------------
# Fix 1: non-http(s) scheme rejected by _raw_query (defense-in-depth)
# ---------------------------------------------------------------------------


def test_raw_query_raises_on_file_scheme() -> None:
    """_raw_query must raise QmdUnavailable for file:// endpoints."""
    client = QmdClient(endpoint="file:///etc/passwd")
    with pytest.raises(QmdUnavailable, match="scheme"):
        client._raw_query("q", collection="magi-memory", limit=10)


def test_query_returns_empty_on_file_scheme() -> None:
    """query() must fail-open (return []) when the endpoint uses a non-http scheme."""
    client = QmdClient(endpoint="file:///etc/passwd")
    assert client.query("q", collection="magi-memory") == []


def test_query_returns_empty_on_ftp_scheme() -> None:
    """ftp:// endpoints should also be rejected."""
    client = QmdClient(endpoint="ftp://internal.host/search")
    assert client.query("q", collection="magi-memory") == []


# ---------------------------------------------------------------------------
# Fix 2: real _raw_query path exercised via urllib.request.urlopen monkeypatch
# ---------------------------------------------------------------------------


def _make_mock_response(body: bytes, status: int = 200) -> object:
    """Return a minimal file-like object that urllib_request.urlopen would yield."""
    import io

    class _MockResponse:
        def __init__(self, data: bytes) -> None:
            self._buf = io.BytesIO(data)
            self.status = status

        def read(self) -> bytes:
            return self._buf.read()

        def __enter__(self) -> "_MockResponse":
            return self

        def __exit__(self, *_: object) -> None:
            pass

    return _MockResponse(body)


def test_query_returns_empty_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """query() returns [] when urlopen raises HTTPError (non-200 response)."""
    import urllib.error
    import urllib.request

    def _raise_http(*_args: object, **_kwargs: object) -> None:
        raise urllib.error.HTTPError(
            url="http://qmd.local/search",
            code=503,
            msg="Service Unavailable",
            hdrs=None,  # type: ignore[arg-type]
            fp=None,
        )

    monkeypatch.setattr(urllib.request, "urlopen", _raise_http)

    client = QmdClient(endpoint="http://qmd.local/search")
    assert client.query("q", collection="magi-memory") == []


def test_query_returns_empty_on_junk_json_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """query() returns [] when the response body is not valid JSON."""
    import urllib.request

    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *_a, **_kw: _make_mock_response(b"<html>not json</html>"),
    )

    client = QmdClient(endpoint="http://qmd.local/search")
    assert client.query("q", collection="magi-memory") == []


def test_query_returns_empty_on_url_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """query() returns [] when urlopen raises URLError (network-level failure)."""
    import urllib.error
    import urllib.request

    def _raise_url(*_args: object, **_kwargs: object) -> None:
        raise urllib.error.URLError("timed out")

    monkeypatch.setattr(urllib.request, "urlopen", _raise_url)

    client = QmdClient(endpoint="http://qmd.local/search")
    assert client.query("q", collection="magi-memory") == []


def test_query_returns_results_via_urlopen_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Smoke test: happy path through the real _raw_query using a mocked urlopen."""
    import json
    import urllib.request

    payload = json.dumps(
        {
            "results": [
                {
                    "path": "memory/ROOT.md",
                    "content": "root memory content",
                    "score": 0.88,
                    "context": "root",
                }
            ]
        }
    ).encode("utf-8")

    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *_a, **_kw: _make_mock_response(payload),
    )

    client = QmdClient(endpoint="http://qmd.local/search")
    results = client.query("root", collection="magi-memory")

    assert len(results) == 1
    assert results[0]["path"] == "memory/ROOT.md"
    assert results[0]["score"] == pytest.approx(0.88)
