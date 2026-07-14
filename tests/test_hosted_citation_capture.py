"""Source-citation capture on the HOSTED governed gate5b serving path.

The LOCAL headless runtime threads a session-scoped SessionSourceRegistry onto
every ToolContext (via engine.local_tool_evidence_collector) and the LOCAL
streaming driver reads it at terminal time to build the citations payload. The
hosted governed serving path never wired this (a gate5b/OSS dual-path split), so
source-citation was completely dark on hosted: no web tool registered a source,
no collector accumulated one, and the terminal frame carried no citations.

These tests cover the three linked pieces that close the hosted side (mirroring
PR #1516's activity-event sink wiring):

    A. hosted_citations_payload_for -- the fail-soft composer that turns a
       per-turn collector + session_id + visible text into a citations payload.
    B. Gate5BFullToolHost threads citationRegistry=collector.source_registry_for(
       session_id) into the dispatch ToolContext (the SAME registry instance the
       serving driver reads), and stays byte-identical (None) when no collector.
    C. _drive_selected_gate5b_stream builds the terminal citations payload from
       the collector web tools registered into, and omits the key when off.

No em-dashes per the citation feature style rule.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from magi_agent.cli.contracts import EngineResult, Terminal
from magi_agent.evidence.citation_render import hosted_citations_payload_for
from magi_agent.evidence.local_tool_collector import LocalToolEvidenceCollector
from magi_agent.tools.context import ToolContext


# ---------------------------------------------------------------------------
# A. hosted_citations_payload_for -- fail-soft composer
# ---------------------------------------------------------------------------


def test_hosted_payload_none_when_no_collector() -> None:
    assert hosted_citations_payload_for("text", None, "sess") is None


def test_hosted_payload_none_when_no_session_id() -> None:
    collector = LocalToolEvidenceCollector()
    assert hosted_citations_payload_for("text", collector, "") is None
    assert hosted_citations_payload_for("text", collector, None) is None


def test_hosted_payload_none_when_flag_off(monkeypatch) -> None:
    """Flag off -> source_registry_for returns None -> no payload (byte-identical)."""
    monkeypatch.setenv("MAGI_SOURCE_CITATION_ENABLED", "0")
    collector = LocalToolEvidenceCollector()
    assert hosted_citations_payload_for("text", collector, "sess") is None


def test_hosted_payload_none_when_flag_on_but_no_source(monkeypatch) -> None:
    """Flag on but nothing registered -> a registry with an empty snapshot; the
    payload projects but carries no sources and an uncited verdict."""
    monkeypatch.setenv("MAGI_SOURCE_CITATION_ENABLED", "1")
    collector = LocalToolEvidenceCollector()
    payload = hosted_citations_payload_for("plain answer", collector, "sess")
    assert payload is not None
    assert payload["sources"] == []
    assert payload["markers"] == []


def test_hosted_payload_projects_registered_source(monkeypatch) -> None:
    """Flag on, a source registered into the collector's session registry, and a
    src_N marker in the visible text -> a cited payload with that source."""
    monkeypatch.setenv("MAGI_SOURCE_CITATION_ENABLED", "1")
    collector = LocalToolEvidenceCollector()
    registry = collector.source_registry_for("sess")
    assert registry is not None
    record = registry.register(
        "web_fetch",
        "https://sec.gov/tsla",
        turn_id="t1",
        tool_name="web_fetch",
        title="Tesla 10-Q",
        trust_tier="official",
        inspected=True,
    )
    assert record is not None
    src = record.source_id
    payload = hosted_citations_payload_for(
        f"Revenue was 12.77B [{src}].", collector, "sess"
    )
    assert payload is not None
    assert payload["markers"] == [[src, 1]]
    assert payload["verdict"] == "cited"
    assert payload["sources"][0]["uri"] == "https://sec.gov/tsla"


def test_hosted_payload_faulty_collector_is_fail_soft() -> None:
    """A collector whose source_registry_for raises -> None, never propagates."""

    class _BoomCollector:
        def source_registry_for(self, session_id: str) -> object:
            raise RuntimeError("boom")

    assert hosted_citations_payload_for("text", _BoomCollector(), "sess") is None


# ---------------------------------------------------------------------------
# B. Gate5BFullToolHost threads the SAME registry into the ToolContext
# ---------------------------------------------------------------------------


def _build_host(tmp_path: Any, collector: object | None):
    from magi_agent.gates.gate5b_full_toolhost import (
        Gate5BFullToolHost,
        Gate5BFullToolHostConfig,
    )

    return Gate5BFullToolHost(
        config=Gate5BFullToolHostConfig(),
        workspace_root=tmp_path,
        exposed_tool_names=("web_fetch",),
        now_ms=lambda: 0,
        tool_registry=None,
        session_id="sess-b",
        citation_collector=collector,
    )


def test_host_citation_registry_is_same_instance(tmp_path: Any, monkeypatch) -> None:
    """host._citation_registry() returns the collector's session registry -- the
    SAME instance the serving driver reads via the same collector."""
    monkeypatch.setenv("MAGI_SOURCE_CITATION_ENABLED", "1")
    collector = LocalToolEvidenceCollector()
    host = _build_host(tmp_path, collector)
    from_host = host._citation_registry()
    from_collector = collector.source_registry_for("sess-b")
    assert from_host is not None
    assert from_host is from_collector


def test_host_citation_registry_none_without_collector(tmp_path: Any, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_SOURCE_CITATION_ENABLED", "1")
    host = _build_host(tmp_path, None)
    assert host._citation_registry() is None


def test_host_citation_registry_none_when_flag_off(tmp_path: Any, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_SOURCE_CITATION_ENABLED", "0")
    collector = LocalToolEvidenceCollector()
    host = _build_host(tmp_path, collector)
    assert host._citation_registry() is None


def test_dispatch_tool_context_carries_citation_registry(tmp_path: Any, monkeypatch) -> None:
    """_dispatch_registry_tool builds a ToolContext whose citation_registry is the
    collector's live registry -- so web tools register into the SAME instance the
    serving driver reads."""
    monkeypatch.setenv("MAGI_SOURCE_CITATION_ENABLED", "1")
    collector = LocalToolEvidenceCollector()
    host = _build_host(tmp_path, collector)

    captured: dict[str, object] = {}

    class _FakeDispatcher:
        async def dispatch(self, name, args, context, *, mode, exposed_tool_names):  # noqa: ANN001, ANN201
            captured["context"] = context
            return SimpleNamespace(
                status="ok", model_dump=lambda **kwargs: {"ok": True}
            )

    host._tool_dispatcher = _FakeDispatcher()
    host._tool_registry = SimpleNamespace(
        resolve_enabled=lambda name: SimpleNamespace(
            adk_tool_type="FunctionTool", available_in_modes=("act",)
        )
    )

    asyncio.run(
        host._dispatch_registry_tool("web_fetch", {"url": "x"}, tool_call_id="tu_1")
    )
    ctx = captured["context"]
    assert isinstance(ctx, ToolContext)
    assert ctx.citation_registry is collector.source_registry_for("sess-b")


def test_dispatch_tool_context_citation_registry_none_when_off(tmp_path: Any, monkeypatch) -> None:
    """Flag off -> the dispatch ToolContext carries citation_registry=None
    (byte-identical to the pre-citation ToolContext)."""
    monkeypatch.setenv("MAGI_SOURCE_CITATION_ENABLED", "0")
    collector = LocalToolEvidenceCollector()
    host = _build_host(tmp_path, collector)

    captured: dict[str, object] = {}

    class _FakeDispatcher:
        async def dispatch(self, name, args, context, *, mode, exposed_tool_names):  # noqa: ANN001, ANN201
            captured["context"] = context
            return SimpleNamespace(
                status="ok", model_dump=lambda **kwargs: {"ok": True}
            )

    host._tool_dispatcher = _FakeDispatcher()
    host._tool_registry = SimpleNamespace(
        resolve_enabled=lambda name: SimpleNamespace(
            adk_tool_type="FunctionTool", available_in_modes=("act",)
        )
    )

    asyncio.run(
        host._dispatch_registry_tool("web_fetch", {"url": "x"}, tool_call_id="tu_1")
    )
    assert captured["context"].citation_registry is None


# ---------------------------------------------------------------------------
# C. _drive_selected_gate5b_stream builds the terminal citations payload
# ---------------------------------------------------------------------------


def _terminal_citations(chunks: list[bytes]) -> dict | None:
    for chunk in chunks:
        text = chunk.decode()
        if '"type": "turn_result"' in text or '"type":"turn_result"' in text:
            body = text.split("data: ", 1)[1].strip()
            return json.loads(body).get("citations")
    raise AssertionError("no turn_result frame")


def _python_ready_response(content: str) -> SimpleNamespace:
    return SimpleNamespace(
        status_code=200,
        body=json.dumps(
            {"status": "python_ready", "publicEvents": [], "content": content}
        ).encode(),
    )


async def _drive(monkeypatch, *, register_source: bool, content: str) -> list[bytes]:
    """Drive the hosted gate5b stream with run_gate5b_user_visible_chat_response
    faked to (optionally) register a source into the collector it is passed by
    reference, then return the terminal python_ready response."""
    from magi_agent.transport import streaming_chat_route as route

    async def fake_run(runtime, body, *, request, public_event_sink=None, citation_collector=None):  # noqa: ANN001, ANN201
        # Stream the visible text as a live token delta (numbers markers the way
        # the user saw them), like the real serving path does.
        if public_event_sink is not None:
            public_event_sink({"type": "text_delta", "delta": content})
        if register_source and citation_collector is not None:
            registry = citation_collector.source_registry_for("sess-c")
            if registry is not None:
                registry.register(
                    "web_fetch",
                    "https://sec.gov/tsla",
                    turn_id="t1",
                    tool_name="web_fetch",
                    title="Tesla 10-Q",
                    trust_tier="official",
                    inspected=True,
                )
        return _python_ready_response(content)

    monkeypatch.setattr(route, "run_gate5b_user_visible_chat_response", fake_run)

    chunks: list[bytes] = []
    async for chunk in route._drive_selected_gate5b_stream(
        SimpleNamespace(),
        {"sessionId": "sess-c"},
        SimpleNamespace(),
        session_id="sess-c",
        turn_id="t1",
    ):
        chunks.append(chunk)
    return chunks


def test_driver_attaches_citations_when_flag_on(monkeypatch) -> None:
    """A hosted governed turn whose web tool registered a source (flag ON) yields
    a terminal frame carrying the citations payload with that source. The visible
    text uses a src_1 marker so the projection numbers it [1]."""
    monkeypatch.setenv("MAGI_SOURCE_CITATION_ENABLED", "1")
    chunks = asyncio.run(
        _drive(monkeypatch, register_source=True, content="Revenue was 12B [src_1].")
    )
    citations = _terminal_citations(chunks)
    assert citations is not None
    assert citations["sources"], citations
    assert citations["sources"][0]["uri"] == "https://sec.gov/tsla"
    assert citations["markers"] == [["src_1", 1]]
    assert citations["verdict"] == "cited"


def test_driver_no_citations_key_when_flag_off(monkeypatch) -> None:
    """Flag off -> source_registry_for returns None -> the terminal frame carries
    NO citations key (byte-identical to the pre-citation frame)."""
    monkeypatch.setenv("MAGI_SOURCE_CITATION_ENABLED", "0")
    chunks = asyncio.run(
        _drive(monkeypatch, register_source=False, content="plain answer")
    )
    assert _terminal_citations(chunks) is None
    assert b"citations" not in b"".join(chunks)


def test_driver_no_citations_key_when_flag_on_but_no_source(monkeypatch) -> None:
    """Flag on but no web source registered and no markers -> the projection has
    no sources; verdict is not 'cited'. The key rides the frame (as on the local
    path) but reflects an uncited answer."""
    monkeypatch.setenv("MAGI_SOURCE_CITATION_ENABLED", "1")
    chunks = asyncio.run(
        _drive(monkeypatch, register_source=False, content="plain answer")
    )
    citations = _terminal_citations(chunks)
    assert citations is not None
    assert citations["sources"] == []
    assert citations["markers"] == []
