"""Round-trip: a run-bookend record lands on the durable evidence ledger and
reads back through the same reader the control plane uses.

This proves the bookend uses the EXISTING durable sink (no second writer): it is
written by ``write_evidence_records`` to ``<dir>/<session>.jsonl`` next to the
per-tool evidence, and surfaces via ``EvidenceLedgerReader.read``.
"""
from __future__ import annotations

from pathlib import Path

from magi_agent.evidence.ledger_store import (
    EvidenceLedgerReader,
    write_evidence_records,
)
from magi_agent.evidence.run_bookend import (
    RUN_BOOKEND_SCHEMA_VERSION,
    RUN_BOOKEND_TOOL_NAME,
    build_run_bookend_record,
)


def test_bookend_round_trips_through_durable_ledger(tmp_path: Path) -> None:
    record = build_run_bookend_record(
        session_id="cli-session",
        turn_id="turn-7",
        goal="Fix lint and open a PR",
        result="Fixed 12 issues, opened PR #1234",
        status="ok",
        model="claude-opus-4-8",
        provider="anthropic",
        input_tokens=1500,
        output_tokens=800,
        cost_usd=0.0421,
    )

    write_evidence_records(
        tmp_path,
        session_id="cli-session",
        turn_id="turn-7",
        records=[record],
    )

    rows = EvidenceLedgerReader(tmp_path).read("cli-session")
    bookends = [r for r in rows if r.get("toolName") == RUN_BOOKEND_TOOL_NAME]
    assert len(bookends) == 1
    line = bookends[0]
    assert line["sessionId"] == "cli-session"
    assert line["turnId"] == "turn-7"
    assert line["status"] == "ok"
    payload = line["record"]
    assert payload["schemaVersion"] == RUN_BOOKEND_SCHEMA_VERSION
    assert payload["goal"] == "Fix lint and open a PR"
    assert payload["result"] == "Fixed 12 issues, opened PR #1234"
    assert payload["model"] == {"label": "claude-opus-4-8", "provider": "anthropic"}
    assert payload["usage"] == {"inputTokens": 1500, "outputTokens": 800}
    assert payload["costUsd"] == 0.0421


def test_bookend_coexists_with_tool_records_on_same_file(tmp_path: Path) -> None:
    # A pre-existing tool record (the shape the collector already writes).
    write_evidence_records(
        tmp_path,
        session_id="s",
        turn_id="t",
        records=[{"toolName": "Bash", "status": "ok", "record": {"x": 1}}],
    )
    write_evidence_records(
        tmp_path,
        session_id="s",
        turn_id="t",
        records=[
            build_run_bookend_record(
                session_id="s",
                turn_id="t",
                goal="do a thing",
                result=None,
                status="ok",
                model=None,
                provider=None,
                input_tokens=None,
                output_tokens=None,
                cost_usd=None,
            )
        ],
    )

    rows = EvidenceLedgerReader(tmp_path).read("s")
    tool_names = [r.get("toolName") for r in rows]
    assert "Bash" in tool_names
    assert RUN_BOOKEND_TOOL_NAME in tool_names
