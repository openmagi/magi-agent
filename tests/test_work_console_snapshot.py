from __future__ import annotations

import json

from magi_agent.runtime.public_events import authorize_rule_check_event
from magi_agent.runtime.work_console_snapshot import (
    build_work_console_snapshot,
)


def _dump(value: object) -> str:
    return json.dumps(value, sort_keys=True)


def test_snapshot_rebuilds_public_work_console_state_deterministically() -> None:
    events: tuple[dict[str, object], ...] = (
        {"type": "turn_start", "eventId": "evt-1", "turnId": "turn-1"},
        {
            "type": "turn_phase",
            "eventId": "evt-2",
            "turnId": "turn-1",
            "phase": "executing",
        },
        {
            "type": "heartbeat",
            "eventId": "evt-3",
            "turnId": "turn-1",
            "elapsedMs": 12000,
        },
        {
            "type": "tool_start",
            "eventId": "evt-4",
            "id": "tool-1",
            "name": "FileRead",
            "input_preview": "public file ref",
            "transcriptRefs": ["sha256:" + "0" * 64],
        },
        {
            "type": "tool_progress",
            "eventId": "evt-5",
            "id": "tool-1",
            "label": "Reading materials",
        },
        {
            "type": "source_inspected",
            "eventId": "evt-6",
            "source": {
                "sourceId": "src-1",
                "kind": "subagent_result",
                "uri": "source://digest-safe",
                "contentHash": "sha256:" + "1" * 64,
            },
        },
        {
            "type": "rule_check",
            "eventId": "evt-7",
            "ruleId": "claim-citation-gate",
            "verdict": "pending",
            "detail": "citation audit status=pending",
            "evidenceRef": "evidence:claim-citation-gate",
        },
        {
            "type": "spawn_started",
            "eventId": "evt-8",
            "taskId": "child-1",
            "persona": "reviewer",
            "deliver": "background",
            "detail": "scheduled receipt=receipt:sha256:" + "2" * 64,
        },
        {
            "type": "child_progress",
            "eventId": "evt-9",
            "taskId": "child-1",
            "detail": "child_result status=retry missingRefs=1",
            "childReceiptRef": "receipt:sha256:" + "3" * 64,
        },
        {
            "type": "task_board",
            "eventId": "evt-10",
            "tasks": [
                {
                    "id": "task-1",
                    "title": "Inspect runtime",
                    "description": "public task",
                    "status": "in_progress",
                }
            ],
        },
        {
            "type": "active_snapshot",
            "eventId": "evt-unsupported",
            "rawSnapshot": "private active snapshot",
        },
    )

    snapshot = build_work_console_snapshot(events)
    projection = snapshot.public_projection()
    rebuilt = build_work_console_snapshot(events).public_projection()

    assert projection == rebuilt
    assert projection["schemaVersion"] == "workConsoleSnapshot.v1"
    assert projection["turnId"] == "turn-1"
    assert projection["status"] == "running"
    assert projection["turnPhase"] == "executing"
    assert projection["heartbeatElapsedMs"] == 12000
    assert projection["activeTools"] == [
        {
            "id": "tool-1",
            "label": "Reading materials",
            "status": "running",
            "startedAt": 4,
            "updatedAt": 5,
        }
    ]
    assert projection["subagents"] == [
        {
            "taskId": "child-1",
            "role": "reviewer",
            "status": "running",
            "detail": "child_result status=retry missingRefs=1",
            "startedAt": 8,
            "updatedAt": 9,
        }
    ]
    assert projection["taskBoard"] == {
        "receivedAt": 10,
        "tasks": [
            {
                "id": "task-1",
                "title": "Inspect runtime",
                "description": "public task",
                "status": "in_progress",
            }
        ],
    }
    assert projection["ruleStatuses"] == [
        {
            "ruleId": "claim-citation-gate",
            "verdict": "pending",
            "detail": "citation audit status=pending",
            "checkedAt": 7,
        }
    ]
    assert projection["evidenceCounts"] == {"evidenceRefs": 5, "sources": 1}
    assert projection["unsupportedEventCounters"] == {"active_snapshot": 1}
    assert str(projection["projectionDigest"]).startswith("sha256:")
    assert "private active snapshot" not in _dump(projection)


def test_duplicate_events_are_idempotent_by_event_id() -> None:
    events: tuple[dict[str, object], ...] = (
        {"type": "turn_start", "eventId": "evt-turn", "turnId": "turn-1"},
        {"type": "text_delta", "eventId": "evt-text", "delta": "Hello"},
        {"type": "text_delta", "eventId": "evt-text", "delta": "Hello"},
        {
            "type": "tool_start",
            "eventId": "evt-tool",
            "id": "tool-1",
            "name": "Read",
            "inputDigest": "sha256:" + "1" * 64,
        },
        {
            "type": "tool_start",
            "eventId": "evt-tool",
            "id": "tool-1",
            "name": "Read",
            "inputDigest": "sha256:" + "1" * 64,
        },
    )

    projection = build_work_console_snapshot(events).public_projection()

    assert projection["content"] == "Hello"
    assert projection["deduplicatedEventCount"] == 2
    assert projection["processedEventCount"] == 3
    assert len(projection["activeTools"]) == 1


def test_reconnect_snapshot_does_not_replay_hidden_or_raw_data() -> None:
    events: tuple[dict[str, object], ...] = (
        {"type": "turn_start", "eventId": "evt-turn", "turnId": "turn-1"},
        {
            "type": "text_delta",
            "eventId": "evt-text",
            "delta": "raw prompt at /Users/kevin/private with token=sk-test-secret",
        },
        {
            "type": "thinking_delta",
            "eventId": "evt-thinking",
            "delta": "hidden reasoning and chain of thought",
        },
        {
            "type": "tool_start",
            "eventId": "evt-tool",
            "id": "tool-1",
            "name": "Bash",
            "input_preview": "Authorization: Bearer unsafe-token",
            "inputDigest": "sha256:" + "2" * 64,
        },
        {
            "type": "source_inspected",
            "eventId": "evt-source",
            "source": {
                "sourceId": "src-1",
                "kind": "web_fetch",
                "uri": "https://example.test",
                "contentHash": "sha256:" + "4" * 64,
                "rawSourceSnapshot": "private source body",
            },
        },
    )

    projection = build_work_console_snapshot(events).public_projection()
    dumped = _dump(projection)

    assert projection["content"] == ""
    assert projection["thinking"] == ""
    assert projection["activeTools"][0]["label"] == "Bash"
    assert projection["unsupportedEventCounters"] == {"thinking_delta": 1}
    for unsafe in (
        "raw prompt",
        "/Users/kevin/private",
        "sk-test-secret",
        "hidden reasoning",
        "chain of thought",
        "Authorization",
        "Bearer unsafe-token",
        "private source body",
    ):
        assert unsafe not in dumped


def test_reconnect_snapshot_detaches_after_parent_end_with_background_work() -> None:
    events: tuple[dict[str, object], ...] = (
        {"type": "turn_start", "eventId": "evt-turn", "turnId": "turn-1"},
        {
            "type": "spawn_started",
            "eventId": "evt-child",
            "taskId": "child-1",
            "persona": "writer",
            "deliver": "background",
            "detail": "scheduled receipt=receipt:sha256:" + "5" * 64,
        },
        {
            "type": "turn_end",
            "eventId": "evt-end",
            "turnId": "turn-1",
            "status": "committed",
            "stopReason": "complete",
        },
    )

    projection = build_work_console_snapshot(events).public_projection()

    assert projection["detached"] is True
    assert projection["turnPhase"] == "committed"
    assert projection["subagents"][0]["status"] == "running"


def test_snapshot_accepts_one_pass_event_iterables() -> None:
    events = (
        event
        for event in (
            {"type": "turn_start", "eventId": "evt-turn", "turnId": "turn-1"},
            {"type": "text_delta", "eventId": "evt-text", "delta": "streamed"},
        )
    )

    projection = build_work_console_snapshot(events).public_projection()

    assert projection["turnId"] == "turn-1"
    assert projection["content"] == "streamed"


def test_snapshot_does_not_claim_work_state_without_public_receipts() -> None:
    events: tuple[dict[str, object], ...] = (
        {"type": "turn_start", "eventId": "evt-turn", "turnId": "turn-1"},
        {"type": "tool_start", "eventId": "evt-tool", "id": "tool-1", "name": "Read"},
        {
            "type": "tool_progress",
            "eventId": "evt-tool-progress",
            "id": "tool-1",
            "label": "Reading",
        },
        {
            "type": "rule_check",
            "eventId": "evt-rule",
            "ruleId": "claim-citation-gate",
            "verdict": "pending",
            "detail": "citation audit status=pending",
        },
        {
            "type": "spawn_started",
            "eventId": "evt-child",
            "taskId": "child-1",
            "persona": "reviewer",
            "detail": "scheduled without receipt",
        },
        {
            "type": "child_progress",
            "eventId": "evt-child-progress",
            "taskId": "child-1",
            "detail": "child_result status=retry",
        },
    )

    projection = build_work_console_snapshot(events).public_projection()

    assert projection["activeTools"] == []
    assert projection["ruleStatuses"] == []
    assert projection["subagents"] == []


def test_snapshot_rejects_unissued_rule_check_authority() -> None:
    receipt_ref = "receipt:sha256:" + ("a" * 64)
    projection = build_work_console_snapshot(
        (
            {"type": "turn_start", "eventId": "evt-turn", "turnId": "turn-1"},
            {
                "type": "rule_check",
                "eventId": "evt-rule",
                "ruleId": "claim-citation-gate",
                "verdict": "ok",
                "detail": "citation audit status=ok",
                "evidenceRef": receipt_ref,
            },
        )
    ).public_projection()

    assert projection["ruleStatuses"] == []


def test_snapshot_accepts_runtime_authorized_rule_check() -> None:
    receipt_ref = "receipt:sha256:" + ("a" * 64)
    projection = build_work_console_snapshot(
        (
            {"type": "turn_start", "eventId": "evt-turn", "turnId": "turn-1"},
            authorize_rule_check_event(
                {
                    "type": "rule_check",
                    "eventId": "evt-rule",
                    "ruleId": "claim-citation-gate",
                    "verdict": "ok",
                    "detail": "citation audit status=ok",
                    "evidenceRef": receipt_ref,
                }
            ),
        )
    ).public_projection()

    assert projection["ruleStatuses"] == [
        {
            "ruleId": "claim-citation-gate",
            "verdict": "ok",
            "checkedAt": 2,
            "detail": "citation audit status=ok",
        }
    ]
    assert "_openmagiRuleCheckAuthority" not in _dump(projection)


def test_snapshot_counts_only_supported_public_evidence_refs() -> None:
    events: tuple[dict[str, object], ...] = (
        {"type": "turn_start", "eventId": "evt-turn", "turnId": "turn-1"},
        {
            "type": "source_inspected",
            "eventId": "evt-source-missing-ref",
            "source": {
                "sourceId": "src-unsafe",
                "kind": "web_fetch",
                "uri": "https://example.test/raw",
            },
        },
        {
            "type": "active_snapshot",
            "eventId": "evt-active-snapshot",
            "contentHash": "sha256:" + "6" * 64,
            "rawSnapshot": {
                "contentHash": "sha256:" + "6" * 64,
                "privatePrompt": "raw prompt",
            },
        },
        {
            "type": "source_inspected",
            "eventId": "evt-source-safe",
            "source": {
                "sourceId": "src-safe",
                "kind": "web_fetch",
                "uri": "source://digest-safe",
                "contentHash": "sha256:" + "7" * 64,
            },
        },
    )

    projection = build_work_console_snapshot(events).public_projection()

    assert projection["evidenceCounts"] == {"evidenceRefs": 1, "sources": 1}
    assert projection["unsupportedEventCounters"] == {"active_snapshot": 1}
