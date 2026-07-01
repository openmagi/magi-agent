"""Live research final-gate wiring (WS6 PR6a).

Design: WS6 deterministic-verification activation, PR6a (research-governance
promote to soft ``local_block_intent``).

These tests pin the CRITICAL live-path normalization (design 3.3 / 3.6): the
``ResearchFinalGateRequest`` field validators RAISE on the raw values a live
turn carries (a digit/hex ``turn_id`` fails ``_SAFE_GATE_ID_RE``; raw non-
``src_N`` cited refs fail ``_SOURCE_ID_RE``). The live builder MUST normalize
the ``turn_id``/``contract_id`` via ``_safe_public_ref`` and build
``src_N``-shaped cited refs via the source ledger BEFORE constructing the
request, so a valid request reaches the real ``local_block_intent`` verdict
instead of a swallowed ``ValueError`` degrading to the hard refuse.
"""
from __future__ import annotations

import re

from magi_agent.evidence.research_final_gate import (
    _SAFE_GATE_ID_RE,
    _SOURCE_ID_RE,
)
from magi_agent.research.live_research_final_gate import (
    build_live_research_final_gate_request,
    evaluate_live_research_final_gate,
)


def _source_record(tool_name: str = "WebFetch") -> dict[str, object]:
    return {
        "type": "SourceInspection",
        "status": "ok",
        "observedAt": 1000.0,
        "source": {"kind": "tool_trace", "toolName": tool_name},
        "preview": "fetched source body",
    }


def test_hex_turn_id_is_normalized_to_safe_public_ref() -> None:
    # A digit/hex-starting turn_id FAILS _SAFE_GATE_ID_RE on the raw value;
    # the builder must sanitize it so construction does not raise.
    request = build_live_research_final_gate_request(
        contract_id="live-research-governance",
        turn_id="9f3a1b2c4d5e",
        session_id="session-xyz",
        final_text="According to https://example.com/report the launch shipped.",
        evidence_records=(_source_record(),),
    )

    assert _SAFE_GATE_ID_RE.fullmatch(request.turn_id) is not None
    # The raw hex value would have failed the gate id regex.
    assert _SAFE_GATE_ID_RE.fullmatch("9f3a1b2c4d5e") is None


def test_cited_refs_are_src_n_shaped_and_unique() -> None:
    request = build_live_research_final_gate_request(
        contract_id="live-research-governance",
        turn_id="9f3a1b2c4d5e",
        session_id="session-xyz",
        final_text="According to https://example.com/report the launch shipped.",
        evidence_records=(_source_record("WebFetch"), _source_record("WebFetch")),
    )

    assert request.cited_refs  # non-empty: sources were recorded
    assert all(_SOURCE_ID_RE.fullmatch(ref) is not None for ref in request.cited_refs)
    assert len(set(request.cited_refs)) == len(request.cited_refs)


def test_ledger_records_stored_under_same_sanitized_turn_id() -> None:
    # The sanitized turnId on the request MUST equal the turn_id the source
    # ledger records are stored under, or sources_for_turn drops every source.
    request = build_live_research_final_gate_request(
        contract_id="live-research-governance",
        turn_id="9f3a1b2c4d5e",
        session_id="session-xyz",
        final_text="According to https://example.com/report the launch shipped.",
        evidence_records=(_source_record(),),
    )

    stored = request.source_ledger.sources_for_turn(request.turn_id)
    assert stored  # not silently dropped
    assert {record.source_id for record in stored} == set(request.cited_refs)


def test_evaluate_blocks_on_unrepresented_output_link() -> None:
    result = evaluate_live_research_final_gate(
        contract_id="live-research-governance",
        turn_id="9f3a1b2c4d5e",
        session_id="session-xyz",
        final_text="According to https://example.com/report the launch shipped.",
        evidence_records=(_source_record(),),
    )

    assert result.block_intent is True
    assert result.mode == "local_block_intent"
    assert "output_link_not_in_source_ledger" in result.reason_codes


def test_builder_does_not_raise_on_raw_url_refs_in_answer() -> None:
    # Raw URLs in the candidate answer are NOT passed as cited refs; they are
    # detected as unrepresented links. Construction must not raise.
    request = build_live_research_final_gate_request(
        contract_id="live-research-governance",
        turn_id="0000deadbeef",
        session_id="s",
        final_text="See https://a.example/x and https://b.example/y for details.",
        evidence_records=(),
    )

    # cited_refs empty is valid (no sources); turn_id sanitized.
    assert request.cited_refs == ()
    assert re.match(r"^[A-Za-z]", request.turn_id) is not None
