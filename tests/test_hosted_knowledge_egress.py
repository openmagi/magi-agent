"""Tests for the hosted egress path of the native ``KnowledgeSearch`` tool.

The hosted egress is **default-OFF**. When the gate is not satisfied the tool
must behave byte-identically to today (local fake/boundary path). When the gate
IS satisfied it must POST to the chat-proxy knowledge endpoint with the right
URL / headers / body and map the response into the consistent ToolResult shape.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping

import pytest

from magi_agent.plugins.native import _hosted_knowledge, knowledge
from magi_agent.tools.context import ToolContext


def _context() -> ToolContext:
    return ToolContext(botId="bot-test", turnId="turn-1")


def _run(coro: object) -> object:
    return asyncio.run(coro)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "MAGI_KNOWLEDGE_HOSTED_EGRESS_ENABLED",
        "BOT_ID",
        "GATEWAY_TOKEN",
        "OPENCLAW_GATEWAY_TOKEN",
        "CHAT_PROXY_URL",
    ):
        monkeypatch.delenv(name, raising=False)


class _CapturingPost:
    """Captures the args of the mocked ``_http_post`` and returns a fixed response."""

    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self.payload = payload
        self.calls: list[dict[str, object]] = []

    async def __call__(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        json_body: Mapping[str, object],
        timeout: float,
    ) -> _hosted_knowledge._HttpResponse:
        self.calls.append(
            {"url": url, "headers": dict(headers), "json_body": dict(json_body), "timeout": timeout}
        )
        return _hosted_knowledge._HttpResponse(status_code=self.status_code, payload=self.payload)


# ---------------------------------------------------------------------------
# (a) gate OFF -> falls through to the existing fake/local boundary path.
# ---------------------------------------------------------------------------
def test_gate_off_falls_through_to_local_fake(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    # No flag set, so even with a token+bot present the gate stays off.
    monkeypatch.setenv("BOT_ID", "bot-test")
    monkeypatch.setenv("GATEWAY_TOKEN", "tok-abc")

    # Guard: if egress were taken, this would explode rather than silently pass.
    def _boom(*_a: object, **_k: object) -> object:
        raise AssertionError("hosted egress must not run when the flag is off")

    monkeypatch.setattr(_hosted_knowledge, "_http_post", _boom)

    # The local boundary now reads the real on-disk workspace KB instead of a
    # canned placeholder. Seed a matching document so fall-through returns it.
    doc = tmp_path / "knowledge" / "hr" / "leave.md"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text("Vacation policy: 20 days per year.", encoding="utf-8")
    ctx = ToolContext(botId="bot-test", turnId="turn-1", workspaceRoot=str(tmp_path))

    result = _run(knowledge.knowledge_search({"query": "vacation policy"}, ctx))

    assert _hosted_knowledge.hosted_egress() is None
    assert result.status == "ok"
    # The local boundary returns a REAL record from the workspace KB.
    sources = result.output["sources"]  # type: ignore[index]
    assert sources, "local boundary should return the on-disk KB record"
    assert sources[0]["title"] == "knowledge/hr/leave.md"
    assert "Vacation" in sources[0]["publicPreview"]
    # And the result came from the boundary handler, not the hosted egress.
    assert result.metadata.get("handler") != "hosted_egress"


# ---------------------------------------------------------------------------
# (b) gate ON + bot/token + HTTP 200 -> mapped ToolResult ok.
# ---------------------------------------------------------------------------
def test_gate_on_http_200_maps_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_KNOWLEDGE_HOSTED_EGRESS_ENABLED", "1")
    monkeypatch.setenv("BOT_ID", "bot-test")
    monkeypatch.setenv("GATEWAY_TOKEN", "tok-abc")

    payload = {
        "query": "vacation policy",
        "results": [
            {
                "score": 0.91,
                "collection": "hr-handbook",
                "scope": "org",
                "title": "Vacation Policy",
                "text": "Employees accrue 20 days per year.",
            },
            {
                "score": 0.42,
                "collection": "notes",
                "scope": "personal",
                "snippet": "Remember to file PTO early.",
            },
        ],
        "searched_collections": 2,
    }
    post = _CapturingPost(200, payload)
    monkeypatch.setattr(_hosted_knowledge, "_http_post", post)

    result = _run(
        knowledge.knowledge_search(
            {"query": "vacation policy", "top_k": 5, "scope": "org"},
            _context(),
        )
    )

    # Exactly one HTTP call with the right URL / headers / body.
    assert len(post.calls) == 1
    call = post.calls[0]
    assert call["url"] == (
        "http://chat-proxy.clawy-system.svc.cluster.local:3002"
        "/v1/integrations/knowledge/search"
    )
    headers = call["headers"]
    assert headers["Authorization"] == "Bearer tok-abc"
    assert headers["X-Bot-Id"] == "bot-test"
    assert headers["Content-Type"] == "application/json"
    body = call["json_body"]
    assert body["query"] == "vacation policy"
    assert body["top_k"] == 5
    assert body["scope"] == "org"

    # Mapped ToolResult.
    assert result.status == "ok"
    assert result.metadata.get("handler") == "hosted_egress"
    output = result.output
    assert output["toolName"] == "KnowledgeSearch"  # type: ignore[index]
    sources = output["sources"]  # type: ignore[index]
    assert len(sources) == 2
    first = sources[0]
    assert first["sourceRef"] == "knowledge:hr-handbook:1"
    assert first["evidenceRef"] == "evidence:knowledge:1"
    assert first["visibility"] == "org"
    assert first["contentDigest"].startswith("sha256:")
    assert first["text"] == "Employees accrue 20 days per year."
    assert sources[1]["visibility"] == "private"
    assert output["resultRefs"] == (  # type: ignore[index]
        "knowledge:hr-handbook:1",
        "knowledge:notes:2",
    )


# ---------------------------------------------------------------------------
# (c) gate ON but missing BOT_ID or token -> falls through to fake.
# ---------------------------------------------------------------------------
def test_gate_on_missing_bot_id_falls_through(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_KNOWLEDGE_HOSTED_EGRESS_ENABLED", "1")
    monkeypatch.setenv("GATEWAY_TOKEN", "tok-abc")
    # BOT_ID intentionally unset.

    def _boom(*_a: object, **_k: object) -> object:
        raise AssertionError("hosted egress must not run without BOT_ID")

    monkeypatch.setattr(_hosted_knowledge, "_http_post", _boom)

    assert _hosted_knowledge.hosted_egress() is None
    result = _run(knowledge.knowledge_search({"query": "vacation policy"}, _context()))
    assert result.status == "ok"
    assert result.metadata.get("handler") != "hosted_egress"
    # _context() has no workspace KB, so the local boundary returns zero records
    # (it no longer fabricates a placeholder) while still staying off the hosted
    # path. The key contract here is the fall-through, not the record content.
    assert result.output["sources"] == ()  # type: ignore[index]


def test_gate_on_missing_token_falls_through(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_KNOWLEDGE_HOSTED_EGRESS_ENABLED", "1")
    monkeypatch.setenv("BOT_ID", "bot-test")
    # No GATEWAY_TOKEN / OPENCLAW_GATEWAY_TOKEN.

    assert _hosted_knowledge.hosted_egress() is None
    result = _run(knowledge.knowledge_search({"query": "vacation policy"}, _context()))
    assert result.status == "ok"
    assert result.metadata.get("handler") != "hosted_egress"


def test_fallback_token_env_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_KNOWLEDGE_HOSTED_EGRESS_ENABLED", "1")
    monkeypatch.setenv("BOT_ID", "bot-test")
    monkeypatch.setenv("OPENCLAW_GATEWAY_TOKEN", "tok-fallback")

    post = _CapturingPost(200, {"results": []})
    monkeypatch.setattr(_hosted_knowledge, "_http_post", post)

    result = _run(knowledge.knowledge_search({"query": "x"}, _context()))
    assert result.status == "ok"
    assert post.calls[0]["headers"]["Authorization"] == "Bearer tok-fallback"


# ---------------------------------------------------------------------------
# (d) gate ON + HTTP 500 -> ToolResult error, no fake fallback.
# ---------------------------------------------------------------------------
def test_gate_on_http_500_returns_error_no_fake(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_KNOWLEDGE_HOSTED_EGRESS_ENABLED", "1")
    monkeypatch.setenv("BOT_ID", "bot-test")
    monkeypatch.setenv("GATEWAY_TOKEN", "tok-abc")

    post = _CapturingPost(500, {"error": "boom"})
    monkeypatch.setattr(_hosted_knowledge, "_http_post", post)

    result = _run(knowledge.knowledge_search({"query": "vacation policy"}, _context()))

    assert result.status == "error"
    assert result.error_code == "knowledge_hosted_egress_failed"
    assert result.metadata.get("httpStatus") == 500
    # Must NOT mask the failure with the fake record.
    assert result.output is None


def test_network_error_returns_error_no_fake(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_KNOWLEDGE_HOSTED_EGRESS_ENABLED", "1")
    monkeypatch.setenv("BOT_ID", "bot-test")
    monkeypatch.setenv("GATEWAY_TOKEN", "tok-abc")

    async def _raise(*_a: object, **_k: object) -> object:
        raise RuntimeError("connection refused")

    monkeypatch.setattr(_hosted_knowledge, "_http_post", _raise)

    result = _run(knowledge.knowledge_search({"query": "vacation policy"}, _context()))
    assert result.status == "error"
    assert result.error_code == "knowledge_hosted_egress_failed"
    assert result.output is None


def test_custom_chat_proxy_url_and_top_k_clamp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_KNOWLEDGE_HOSTED_EGRESS_ENABLED", "1")
    monkeypatch.setenv("BOT_ID", "bot-test")
    monkeypatch.setenv("GATEWAY_TOKEN", "tok-abc")
    monkeypatch.setenv("CHAT_PROXY_URL", "http://proxy.internal:9000/")

    post = _CapturingPost(200, {"results": []})
    monkeypatch.setattr(_hosted_knowledge, "_http_post", post)

    _run(knowledge.knowledge_search({"query": "x", "top_k": 999}, _context()))
    call = post.calls[0]
    assert call["url"] == "http://proxy.internal:9000/v1/integrations/knowledge/search"
    assert call["json_body"]["top_k"] == 20  # clamped to max
