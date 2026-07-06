"""Wave 3a: terminal turn_result frame carries an optional citations payload.

RED-first: byte-identity when no citations are passed (flag-OFF parity), the
exact wire shape when a payload is passed, and secret-marker scrubbing of the
uri/title fields. No em-dashes per the citation feature style rule.
"""
from __future__ import annotations

import json

from magi_agent.engine.contracts import EngineResult, Terminal
from magi_agent.transport.streaming_chat import frame_for_terminal


def _terminal() -> EngineResult:
    return EngineResult(
        terminal=Terminal.completed,
        usage={"input_tokens": 3},
        cost_usd=0.0,
        error=None,
        session_id="s1",
        turn_id="t1",
    )


def _decode_turn_result(chunks: list[bytes]) -> dict:
    head = chunks[0].decode()
    assert head.startswith("event: agent\ndata: ")
    body = head[len("event: agent\ndata: ") :].strip()
    return json.loads(body)


def test_no_citations_is_byte_identical() -> None:
    # Default (no citations kwarg) must be byte-for-byte what today emits.
    default_chunks = list(frame_for_terminal(_terminal()))
    explicit_none = list(frame_for_terminal(_terminal(), citations=None))
    assert default_chunks == explicit_none
    turn_result = _decode_turn_result(default_chunks)
    assert "citations" not in turn_result
    # The raw bytes never mention the field either.
    assert b"citations" not in b"".join(default_chunks)


def test_citations_payload_rides_terminal_frame() -> None:
    payload = {
        "markers": [["src_3", 1]],
        "sources": [
            {
                "n": 1,
                "sourceId": "src_3",
                "uri": "https://sec.gov/tsla",
                "title": "Tesla 10-Q",
                "kind": "web_fetch",
                "trustTier": "official",
                "inspected": True,
            }
        ],
        "danglingRefs": [],
        "verdict": "cited",
    }
    chunks = list(frame_for_terminal(_terminal(), citations=payload))
    turn_result = _decode_turn_result(chunks)
    assert turn_result["type"] == "turn_result"
    assert turn_result["citations"]["markers"] == [["src_3", 1]]
    assert turn_result["citations"]["verdict"] == "cited"
    assert turn_result["citations"]["sources"][0]["uri"] == "https://sec.gov/tsla"


def test_citations_uri_title_scrubbed_for_private_markers() -> None:
    payload = {
        "markers": [["src_1", 1]],
        "sources": [
            {
                "n": 1,
                "sourceId": "src_1",
                "uri": "https://x.example/hidden reasoning dump",
                "title": "raw tool output leak",
                "kind": "web_fetch",
                "trustTier": "secondary",
                "inspected": True,
            }
        ],
        "danglingRefs": [],
        "verdict": "cited",
    }
    chunks = list(frame_for_terminal(_terminal(), citations=payload))
    turn_result = _decode_turn_result(chunks)
    source = turn_result["citations"]["sources"][0]
    assert source["uri"] == "[redacted-private]"
    assert source["title"] == "[redacted-private]"
    # Non-sensitive structural fields survive.
    assert source["sourceId"] == "src_1"
    assert source["kind"] == "web_fetch"
