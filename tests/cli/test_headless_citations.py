"""Wave 3a: CLI / headless Sources footer + NDJSON citations on the result frame.

RED-first: the footer projection format, ResultFrame flag-OFF byte-identity (no
``citations`` key), NDJSON structure when present, and the text-mode footer.
No em-dashes per the citation feature style rule.
"""
from __future__ import annotations

from magi_agent.cli.headless import _build_result_frame, _text_mode_body
from magi_agent.cli.ndjson import ndjson_dumps
from magi_agent.cli.protocol import ResultFrame
from magi_agent.engine.contracts import EngineResult, Terminal
from magi_agent.evidence.citation_render import render_cli_sources_footer

_PAYLOAD = {
    "markers": [["src_3", 1], ["src_7", 2]],
    "sources": [
        {
            "n": 1,
            "sourceId": "src_3",
            "uri": "https://sec.gov/tsla",
            "title": "Tesla Q1 2026 10-Q",
            "kind": "web_fetch",
            "trustTier": "official",
            "inspected": True,
        },
        {
            "n": 2,
            "sourceId": "src_7",
            "uri": "https://reuters.com/x",
            "title": "Reuters: Tesla cash position",
            "kind": "web_fetch",
            "trustTier": "secondary",
            "inspected": True,
        },
    ],
    "danglingRefs": [],
    "verdict": "cited",
}


def _terminal() -> EngineResult:
    return EngineResult(terminal=Terminal.completed, session_id="s1")


def test_footer_lists_cited_only_in_display_order() -> None:
    footer = render_cli_sources_footer(_PAYLOAD)
    assert footer == (
        "Sources:\n"
        "  [1] Tesla Q1 2026 10-Q - sec.gov (src_3)\n"
        "  [2] Reuters: Tesla cash position - reuters.com (src_7)"
    )


def test_footer_empty_when_no_cited_sources() -> None:
    assert render_cli_sources_footer(None) == ""
    assert render_cli_sources_footer({"sources": [], "verdict": "uncited"}) == ""


def test_result_frame_flag_off_byte_identical() -> None:
    # No citations passed: the serialized NDJSON must not carry the key at all.
    frame = _build_result_frame(
        session_id="s1", assistant_text="hello", terminal=_terminal()
    )
    line = ndjson_dumps(frame)
    assert "citations" not in line
    # A plain ResultFrame serializes identically (no citations field leaks in).
    plain = ResultFrame(session_id="s1", result="hello")
    assert "citations" not in ndjson_dumps(plain)


def test_result_frame_carries_citations_when_present() -> None:
    frame = _build_result_frame(
        session_id="s1",
        assistant_text="Revenue [src_3].",
        terminal=_terminal(),
        citations=_PAYLOAD,
    )
    line = ndjson_dumps(frame)
    assert '"citations"' in line
    assert '"verdict":"cited"' in line
    assert '"sourceId":"src_3"' in line


def test_text_mode_appends_footer_when_citations_present() -> None:
    frame = _build_result_frame(
        session_id="s1",
        assistant_text="Revenue was 12.77B [src_3].",
        terminal=_terminal(),
        citations=_PAYLOAD,
    )
    body = _text_mode_body(frame)
    assert body.startswith("Revenue was 12.77B [src_3].")
    assert "\n\nSources:\n  [1] Tesla Q1 2026 10-Q - sec.gov (src_3)" in body


def test_text_mode_no_footer_when_no_citations() -> None:
    frame = _build_result_frame(
        session_id="s1", assistant_text="plain reply", terminal=_terminal()
    )
    assert _text_mode_body(frame) == "plain reply"
