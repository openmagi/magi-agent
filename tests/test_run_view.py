"""Per-run serializer: turn a session's durable evidence rows into ONE stable
per-run view (the read contract a share page renders).

``build_run_view`` is pure over the rows returned by
``EvidenceLedgerReader.read`` so it is trivially golden-testable. It discriminates
each row by its record ``type`` / ``schemaVersion``:
  - ``openmagi.runBookend.v1``           -> summary (goal/result/model/usage/cost)
  - ``custom:FirstPartyToolCall``        -> a trace step (+ governance if not ok)
  - ``openmagi.localToolEvidenceReceipt.v1`` -> a digest-only receipt (skipped)

NOTE: trace free-text (argsSummary/resultSummary) is surfaced as STORED. This
view is not yet public-safe; the dedicated redaction phase hardens it before any
public link.
"""
from __future__ import annotations

from magi_agent.evidence.run_view import (
    RUN_VIEW_SCHEMA_VERSION,
    build_run_view,
)


def _tool_row(
    *,
    turn_id: str,
    tool_call_id: str,
    name: str,
    status: str = "ok",
    reason: str = "tool_completed",
    duration_ms: int = 12,
    args_summary: object = None,
) -> dict:
    return {
        "sessionId": "s",
        "turnId": turn_id,
        "toolCallId": tool_call_id,
        "toolName": name,
        "status": status,
        "record": {
            "type": "custom:FirstPartyToolCall",
            "status": status,
            "observedAt": 1_700_000_000.0,
            "fields": {
                "actor": "main",
                "name": name,
                "status": status,
                "reason": reason,
                "durationMs": duration_ms,
                "spawnDepth": 0,
                "detail": {
                    "argsSummary": args_summary if args_summary is not None else {"x": 1},
                    "resultSummary": {"exitCode": 0},
                },
            },
        },
    }


def _receipt_row(*, turn_id: str, tool_call_id: str, name: str) -> dict:
    # The every-other-line digest receipt that duplicates a tool call.
    return {
        "sessionId": "s",
        "turnId": turn_id,
        "toolCallId": tool_call_id,
        "toolName": name,
        "status": "ok",
        "record": {"schemaVersion": "openmagi.localToolEvidenceReceipt.v1", "toolCallId": tool_call_id},
    }


def _bookend_row() -> dict:
    return {
        "sessionId": "s",
        "turnId": "t2",
        "toolName": "RunBookend",
        "status": "ok",
        "record": {
            "schemaVersion": "openmagi.runBookend.v1",
            "sessionId": "s",
            "turnId": "t2",
            "status": "ok",
            "goal": "Fix lint and open a PR",
            "result": "Fixed 12, opened PR #1234",
            "model": {"label": "claude-opus-4-8", "provider": "anthropic"},
            "usage": {"inputTokens": 1500, "outputTokens": 800},
            "costUsd": 0.04,
        },
    }


def _rows() -> list[dict]:
    return [
        _tool_row(turn_id="t1", tool_call_id="c1", name="Bash"),
        _receipt_row(turn_id="t1", tool_call_id="c1", name="Bash"),
        _tool_row(
            turn_id="t1",
            tool_call_id="c2",
            name="FileWrite",
            status="needs_approval",
            reason="general_automation_workspace_write_requires_approval",
        ),
        _tool_row(turn_id="t2", tool_call_id="c3", name="Glob"),
        _bookend_row(),
    ]


def test_view_has_schema_version_and_session() -> None:
    view = build_run_view(_rows())
    assert view["schemaVersion"] == RUN_VIEW_SCHEMA_VERSION
    assert view["sessionId"] == "s"


def test_summary_comes_from_bookend() -> None:
    summary = build_run_view(_rows())["summary"]
    assert summary["goal"] == "Fix lint and open a PR"
    assert summary["result"] == "Fixed 12, opened PR #1234"
    assert summary["status"] == "ok"
    assert summary["model"] == {"label": "claude-opus-4-8", "provider": "anthropic"}
    assert summary["usage"] == {"inputTokens": 1500, "outputTokens": 800}
    assert summary["costUsd"] == 0.04


def test_summary_is_none_without_bookend() -> None:
    rows = [r for r in _rows() if r.get("toolName") != "RunBookend"]
    assert build_run_view(rows)["summary"] is None


def test_trace_lists_tool_steps_in_order_skipping_receipts() -> None:
    trace = build_run_view(_rows())["trace"]
    # 3 tool calls (Bash, FileWrite, Glob); the receipt line is NOT a step.
    assert [s["name"] for s in trace] == ["Bash", "FileWrite", "Glob"]
    first = trace[0]
    assert first["turnId"] == "t1"
    assert first["toolCallId"] == "c1"
    assert first["status"] == "ok"
    assert first["durationMs"] == 12
    assert first["actor"] == "main"


def test_governance_is_the_non_ok_decisions() -> None:
    gov = build_run_view(_rows())["governance"]
    assert len(gov) == 1
    assert gov[0]["name"] == "FileWrite"
    assert gov[0]["status"] == "needs_approval"
    assert gov[0]["reason"] == "general_automation_workspace_write_requires_approval"


def test_counts() -> None:
    counts = build_run_view(_rows())["counts"]
    assert counts["stepCount"] == 3
    assert counts["turnCount"] == 2
    assert counts["receiptCount"] == 1


def _spawn_row(*, turn_id: str, tool_call_id: str, child: str) -> dict:
    return {
        "sessionId": "s",
        "turnId": turn_id,
        "toolCallId": tool_call_id,
        "toolName": "SpawnAgent",
        "status": "ok",
        "record": {
            "type": "custom:FirstPartySubagentSpawn",
            "status": "ok",
            "fields": {
                "actor": "main",
                "evidenceType": "SubagentSpawn",
                "name": "SpawnAgent",
                "status": "ok",
                "reason": "spawned",
                "durationMs": 30,
                "spawnDepth": 0,
                "detail": {"argsSummary": {"model": child}},
            },
        },
    }


def test_subagent_spawn_and_skill_load_surface_in_trace() -> None:
    rows = [
        _tool_row(turn_id="t1", tool_call_id="c1", name="Bash"),
        _spawn_row(turn_id="t1", tool_call_id="c2", child="claude-opus-4-8"),
    ]
    trace = build_run_view(rows)["trace"]
    assert [s["name"] for s in trace] == ["Bash", "SpawnAgent"]
    spawn = trace[1]
    assert spawn["activityType"] == "SubagentSpawn"
    assert spawn["argsSummary"] == {"model": "claude-opus-4-8"}


def test_tool_step_carries_activity_type() -> None:
    rows = [_tool_row(turn_id="t1", tool_call_id="c1", name="Bash")]
    # The synthetic tool row has no evidenceType; falls back to the type suffix
    # only when present. Here it is None, which is acceptable for the contract.
    assert "activityType" in build_run_view(rows)["trace"][0]


def test_turn_count_includes_bookend_only_final_turn() -> None:
    rows = [
        _tool_row(turn_id="t1", tool_call_id="c1", name="Bash"),
        _bookend_row(),  # turnId t2, no tool call
    ]
    assert build_run_view(rows)["counts"]["turnCount"] == 2


def test_latest_bookend_wins() -> None:
    first = _bookend_row()
    first["record"] = {**first["record"], "turnId": "t1", "goal": "first goal"}
    second = _bookend_row()
    second["record"] = {**second["record"], "turnId": "t2", "goal": "second goal"}
    summary = build_run_view([first, second])["summary"]
    assert summary["goal"] == "second goal"


def test_governance_kind_splits_error_from_policy() -> None:
    rows = [
        _tool_row(
            turn_id="t1", tool_call_id="c1", name="Bash",
            status="blocked", reason="complex shell requires approval",
        ),
        _tool_row(
            turn_id="t1", tool_call_id="c2", name="FileRead",
            status="error", reason="boom",
        ),
    ]
    gov = build_run_view(rows)["governance"]
    kinds = {g["name"]: g["kind"] for g in gov}
    assert kinds == {"Bash": "policy", "FileRead": "error"}


def test_record_given_as_python_repr_string_is_parsed() -> None:
    # The collector sometimes stores record as a python-repr string, not JSON.
    row = _tool_row(turn_id="t1", tool_call_id="c9", name="Bash")
    row["record"] = repr(row["record"])  # single quotes, not JSON
    trace = build_run_view([row])["trace"]
    assert len(trace) == 1
    assert trace[0]["name"] == "Bash"


def test_unparseable_record_is_skipped_not_raised() -> None:
    bad = {"sessionId": "s", "turnId": "t", "toolName": "X", "record": "<<<not parseable>>>"}
    view = build_run_view([bad])
    assert view["trace"] == []
    assert view["summary"] is None


def test_empty_rows_yields_empty_view() -> None:
    view = build_run_view([])
    assert view["summary"] is None
    assert view["trace"] == []
    assert view["governance"] == []
    assert view["counts"]["stepCount"] == 0


def test_read_run_view_round_trips_through_durable_ledger(tmp_path, monkeypatch) -> None:
    from magi_agent.evidence.ledger_store import write_evidence_records
    from magi_agent.evidence.run_bookend import build_run_bookend_record
    from magi_agent.evidence.run_view import read_run_view

    monkeypatch.setenv("MAGI_EVIDENCE_LEDGER_DIR", str(tmp_path))

    write_evidence_records(
        tmp_path,
        session_id="sess",
        turn_id="t1",
        records=[_tool_row(turn_id="t1", tool_call_id="c1", name="Bash")],
    )
    write_evidence_records(
        tmp_path,
        session_id="sess",
        turn_id="t1",
        records=[
            build_run_bookend_record(
                session_id="sess",
                turn_id="t1",
                goal="do the thing",
                result="did it",
                status="ok",
                model="claude-opus-4-8",
                provider="anthropic",
                input_tokens=10,
                output_tokens=5,
                cost_usd=0.0,
            )
        ],
    )

    view = read_run_view("sess")
    assert view is not None
    assert view["sessionId"] == "sess"
    assert view["summary"]["goal"] == "do the thing"
    assert [s["name"] for s in view["trace"]] == ["Bash"]


def test_read_run_view_none_when_sink_disabled(monkeypatch) -> None:
    from magi_agent.evidence.run_view import read_run_view

    monkeypatch.setenv("MAGI_EVIDENCE_LEDGER_DIR", "off")
    assert read_run_view("whatever") is None


def test_read_run_view_none_for_missing_session(tmp_path, monkeypatch) -> None:
    from magi_agent.evidence.run_view import read_run_view

    monkeypatch.setenv("MAGI_EVIDENCE_LEDGER_DIR", str(tmp_path))
    assert read_run_view("nope") is None
