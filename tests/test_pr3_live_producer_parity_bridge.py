from __future__ import annotations

import json

from magi_agent.runtime.events import (
    NormalizedEvent,
    normalized_events_to_agent_events,
)
from magi_agent.runtime.public_events import (
    authorize_rule_check_event,
    authorize_rule_check_metadata,
)
from magi_agent.transport.sse import InMemorySseWriter


def _sse_payloads(events: list[dict[str, object]]) -> list[dict[str, object]]:
    writer = InMemorySseWriter()
    for event in events:
        writer.agent(event)
    return [
        json.loads(line.removeprefix("data: "))
        for line in writer.body.splitlines()
        if line.startswith("data: ")
    ]


def test_pr3_normalized_runtime_events_project_to_sanitized_work_console_families() -> None:
    receipt = "receipt:sha256:" + ("a" * 64)
    source_digest = "sha256:" + ("b" * 64)
    child_receipt = "receipt:sha256:" + ("c" * 64)
    events = [
        NormalizedEvent(
            type="runtime.phase",
            eventId="evt-phase-1",
            ts=1,
            turnId="turn-pr3",
            source="runtime",
            payload={
                "phase": "executing",
                "label": "Running tools",
                "detail": "safe status detail",
            },
        ),
        NormalizedEvent(
            type="tool.call.progress",
            eventId="evt-tool-progress-1",
            ts=2,
            turnId="turn-pr3",
            callId="call-pr3",
            source="tool_kernel",
            toolName="Search",
            payload={
                "label": "Searching",
                "status": "running",
                "detail": "Authorization: Bearer private-token path=/Users/kevin/private",
            },
            metadata={"receiptRef": receipt},
        ),
        NormalizedEvent(
            type="source.inspected",
            eventId="evt-source-1",
            ts=3,
            turnId="turn-pr3",
            callId="call-pr3",
            source="tool_kernel",
            toolName="Search",
            payload={
                "sourceId": "src_pr3_1",
                "kind": "web_fetch",
                "uri": "https://example.test/docs",
                "title": "Public docs",
                "snippet": "public excerpt token=ghp_private",
            },
            metadata={"contentHash": source_digest},
        ),
        NormalizedEvent(
            type="rule.check",
            eventId="evt-rule-1",
            ts=4,
            turnId="turn-pr3",
            source="runtime",
            payload={
                "ruleId": "citation.required",
                "verdict": "ok",
                "detail": "cited with evidence",
            },
            metadata=authorize_rule_check_metadata({"evidenceRef": receipt}),
        ),
        NormalizedEvent(
            type="child.progress",
            eventId="evt-child-1",
            ts=5,
            turnId="turn-pr3",
            source="runtime",
            payload={
                "taskId": "child-pr3",
                "detail": "child boundary reported status",
            },
            metadata={"childReceiptRef": child_receipt},
        ),
        NormalizedEvent(
            type="runtime.trace",
            eventId="evt-trace-1",
            ts=6,
            turnId="turn-pr3",
            source="runtime",
            payload={
                "phase": "verifier_blocked",
                "severity": "warning",
                "title": "Verifier blocked",
                "detail": "private payload /workspace/secret was rejected",
                "requiredAction": "add source",
            },
            metadata={"reasonCode": receipt, "ruleId": "sha256:" + ("d" * 64)},
        ),
        NormalizedEvent(
            type="runtime.heartbeat",
            eventId="evt-heartbeat-1",
            ts=7,
            turnId="turn-pr3",
            source="runtime",
            payload={"iter": 3, "elapsedMs": 1200, "lastEventAt": 7},
        ),
    ]

    agent_events = normalized_events_to_agent_events(events)
    payloads = _sse_payloads(agent_events)

    assert [event["type"] for event in payloads] == [
        "turn_phase",
        "tool_progress",
        "source_inspected",
        "rule_check",
        "child_progress",
        "runtime_trace",
        "heartbeat",
    ]
    for payload in payloads:
        assert isinstance(payload.get("eventId"), str)
        assert payload["eventId"]

    tool_progress = payloads[1]
    assert tool_progress["id"] == "call-pr3"
    assert tool_progress["receiptRef"] == receipt
    assert "private-token" not in json.dumps(tool_progress)
    assert "/Users/kevin" not in json.dumps(tool_progress)

    source = payloads[2]["source"]
    assert isinstance(source, dict)
    assert source["sourceId"] == "src_pr3_1"
    assert source["contentHash"] == source_digest
    assert source["toolUseId"] == "call-pr3"
    assert "ghp_private" not in json.dumps(source)

    rule_check = payloads[3]
    assert rule_check["evidenceRef"] == receipt
    assert rule_check["verdict"] == "ok"

    child_progress = payloads[4]
    assert child_progress["childReceiptRef"] == child_receipt

    runtime_trace = payloads[5]
    assert runtime_trace["reasonCode"] == receipt
    assert runtime_trace["ruleId"] == "sha256:" + ("d" * 64)
    assert "/workspace/secret" not in json.dumps(runtime_trace)


def test_pr3_source_refs_remain_public_but_rule_checks_require_receipt_authority() -> None:
    evidence_ref = "evidence:web:src_1"
    events = [
        NormalizedEvent(
            type="source.inspected",
            eventId="evt-source-existing-ref",
            ts=1_779_206_400_000,
            turnId="turn-pr3",
            callId="call-pr3",
            source="tool_kernel",
            toolName="Search",
            payload={
                "sourceId": "src_1",
                "kind": "web_fetch",
                "uri": "https://example.test/source",
            },
            metadata={"evidenceRef": evidence_ref},
        ),
        NormalizedEvent(
            type="rule.check",
            eventId="evt-rule-existing-ref",
            ts=1_779_206_401_000,
            turnId="turn-pr3",
            source="runtime",
            payload={
                "ruleId": "claim-citation-gate",
                "verdict": "ok",
                "detail": "cited",
            },
            metadata={"evidenceRef": evidence_ref},
        ),
    ]

    payloads = _sse_payloads(normalized_events_to_agent_events(events))

    assert payloads[0]["type"] == "source_inspected"
    source = payloads[0]["source"]
    assert isinstance(source, dict)
    assert source["sourceId"] == "src_1"
    assert source["contentHash"] == evidence_ref
    assert source["inspectedAt"] == 1_779_206_400_000

    assert payloads[1] == {
        "type": "runtime_trace",
        "eventId": "evt-rule-existing-ref:blocked",
        "turnId": "turn-pr3",
        "phase": "verifier_blocked",
        "severity": "warning",
        "title": "Public event omitted",
        "detail": "rule.check omitted: missing public evidence receipt",
        "reasonCode": "public_projection_missing_receipt",
        "requiredAction": "retain_typescript_fallback",
    }


def test_pr3_source_and_child_projection_require_receipts_before_public_emit() -> None:
    events = [
        NormalizedEvent(
            type="source.inspected",
            eventId="evt-source-missing-receipt",
            ts=1,
            turnId="turn-pr3",
            source="tool_kernel",
            payload={
                "sourceId": "src_pr3_missing",
                "kind": "web_fetch",
                "uri": "https://example.test/no-receipt",
            },
        ),
        NormalizedEvent(
            type="child.progress",
            eventId="evt-child-missing-receipt",
            ts=2,
            turnId="turn-pr3",
            source="runtime",
            payload={"taskId": "child-pr3", "detail": "would be a false child claim"},
        ),
        NormalizedEvent(
            type="rule.check",
            eventId="evt-rule-missing-evidence",
            ts=3,
            turnId="turn-pr3",
            source="runtime",
            payload={
                "ruleId": "citation.required",
                "verdict": "ok",
                "detail": "would be a false verifier claim",
            },
        ),
        NormalizedEvent(
            type="rule.check",
            eventId="evt-rule-pending-no-evidence",
            ts=4,
            turnId="turn-pr3",
            source="runtime",
            payload={
                "ruleId": "citation.pending",
                "verdict": "pending",
                "detail": "waiting for evidence",
            },
        ),
    ]

    agent_events = normalized_events_to_agent_events(events)

    assert agent_events == [
        {
            "type": "runtime_trace",
            "eventId": "evt-source-missing-receipt:blocked",
            "turnId": "turn-pr3",
            "phase": "verifier_blocked",
            "severity": "warning",
            "title": "Public event omitted",
            "detail": "source.inspected omitted: missing public evidence receipt",
            "reasonCode": "public_projection_missing_receipt",
            "requiredAction": "retain_typescript_fallback",
        },
        {
            "type": "runtime_trace",
            "eventId": "evt-child-missing-receipt:blocked",
            "turnId": "turn-pr3",
            "phase": "verifier_blocked",
            "severity": "warning",
            "title": "Public event omitted",
            "detail": "child.progress omitted: missing public child receipt",
            "reasonCode": "public_projection_missing_receipt",
            "requiredAction": "retain_typescript_fallback",
        },
        {
            "type": "runtime_trace",
            "eventId": "evt-rule-missing-evidence:blocked",
            "turnId": "turn-pr3",
            "phase": "verifier_blocked",
            "severity": "warning",
            "title": "Public event omitted",
            "detail": "rule.check omitted: missing public evidence receipt",
            "reasonCode": "public_projection_missing_receipt",
            "requiredAction": "retain_typescript_fallback",
        },
        {
            "type": "rule_check",
            "eventId": "evt-rule-pending-no-evidence",
            "turnId": "turn-pr3",
            "ruleId": "citation.pending",
            "verdict": "pending",
            "checkedAt": 4,
            "detail": "waiting for evidence",
        },
    ]
    assert [payload["type"] for payload in _sse_payloads(agent_events)] == [
        "runtime_trace",
        "runtime_trace",
        "runtime_trace",
        "rule_check",
    ]


def test_pr3_terminal_tool_projection_carries_public_digests_and_transcript_refs() -> None:
    output_digest = "sha256:" + ("d" * 64)
    receipt = "receipt:sha256:" + ("e" * 64)
    source_ref = "source:web:src_1"
    evidence_ref = "evidence:web:src_1"
    events = [
        NormalizedEvent(
            type="tool.call.completed",
            eventId="evt-tool-end-1",
            ts=1,
            turnId="turn-pr3",
            callId="call-pr3",
            source="tool_kernel",
            toolName="Search",
            payload={
                "outputPreview": (
                    "safe summary Authorization: Bearer private-token "
                    "path=/workspace/private"
                ),
                "status": "ok",
            },
                metadata={
                    "outputDigest": output_digest,
                    "receiptRef": receipt,
                    "toolResultRefs": [receipt],
                    "sourceRefs": [source_ref, evidence_ref],
                },
        ),
        NormalizedEvent(
            type="tool.call.needs_approval",
            eventId="evt-tool-approval-1",
            ts=2,
            turnId="turn-pr3",
            callId="call-approval-pr3",
            source="tool_kernel",
            toolName="PatchApply",
            payload={"reasonPreview": "needs approval for public patch preview"},
        ),
        NormalizedEvent(
            type="tool.call.denied",
            eventId="evt-tool-denied-1",
            ts=3,
            turnId="turn-pr3",
            callId="call-denied-pr3",
            source="tool_kernel",
            toolName="PatchApply",
            payload={"reasonPreview": "denied by public policy"},
        ),
    ]

    agent_events = normalized_events_to_agent_events(events)
    payloads = _sse_payloads(agent_events)

    assert payloads == [
        {
            "type": "tool_end",
            "eventId": "evt-tool-end-1",
            "id": "call-pr3",
            "status": "ok",
            "output_preview": (
                "safe summary Authorization: Bearer [redacted] path=[redacted-path]"
            ),
            "outputDigest": output_digest,
            "receiptRef": receipt,
            "transcriptRefs": [receipt, source_ref, evidence_ref],
        },
        {
            "type": "tool_end",
            "eventId": "evt-tool-approval-1",
            "id": "call-approval-pr3",
            "status": "needs_approval",
            "output_preview": "needs approval for public patch preview",
            "outputDigest": payloads[1]["outputDigest"],
        },
        {
            "type": "tool_end",
            "eventId": "evt-tool-denied-1",
            "id": "call-denied-pr3",
            "status": "blocked",
            "output_preview": "denied by public policy",
            "outputDigest": payloads[2]["outputDigest"],
        },
    ]
    assert str(payloads[1]["outputDigest"]).startswith("sha256:")
    assert str(payloads[2]["outputDigest"]).startswith("sha256:")


def test_pr3_direct_sse_requires_receipts_and_preserves_frontend_timestamps() -> None:
    receipt = "receipt:sha256:" + ("f" * 64)
    payloads = _sse_payloads(
        [
            {
                "type": "source_inspected",
                "eventId": "evt-source-direct-missing-evidence",
                "source": {
                    "sourceId": "src_direct_missing",
                    "kind": "web_fetch",
                    "uri": "https://example.test/no-evidence",
                },
            },
            {
                "type": "source_inspected",
                "eventId": "evt-source-direct",
                "source": {
                    "sourceId": "src_direct",
                    "turnId": "turn-pr3",
                    "kind": "web_fetch",
                    "uri": "https://example.test/source",
                    "contentHash": "evidence:web:src_1",
                },
            },
            {
                "type": "rule_check",
                "eventId": "evt-rule-direct-missing-evidence",
                "turnId": "turn-pr3",
                "ruleId": "claim-citation-gate",
                "verdict": "ok",
                "detail": "would be false authority",
            },
            authorize_rule_check_event(
                {
                    "type": "rule_check",
                    "eventId": "evt-rule-direct",
                    "turnId": "turn-pr3",
                    "ruleId": "claim-citation-gate",
                    "verdict": "ok",
                    "detail": "cited",
                    "evidenceRef": receipt,
                }
            ),
            {
                "type": "child_completed",
                "eventId": "evt-child-direct-missing-receipt",
                "turnId": "turn-pr3",
                "taskId": "child-direct",
            },
            {
                "type": "child_completed",
                "eventId": "evt-child-direct",
                "turnId": "turn-pr3",
                "taskId": "child-direct",
                "childReceiptRef": receipt,
            },
        ]
    )

    assert [payload["type"] for payload in payloads] == [
        "runtime_trace",
        "source_inspected",
        "runtime_trace",
        "rule_check",
        "runtime_trace",
        "child_completed",
    ]
    assert payloads[0]["detail"] == (
        "source_inspected omitted: missing public evidence receipt"
    )
    source = payloads[1]["source"]
    assert isinstance(source, dict)
    assert source["contentHash"] == "evidence:web:src_1"
    assert source["inspectedAt"] == 0
    assert payloads[2]["detail"] == "rule_check omitted: missing public evidence receipt"
    assert payloads[3]["checkedAt"] == 0
    assert payloads[3]["evidenceRef"] == receipt
    assert payloads[4]["detail"] == "child_completed omitted: missing public child receipt"
    assert payloads[5]["childReceiptRef"] == receipt


def test_pr3_direct_sse_rejects_malformed_authority_refs() -> None:
    payloads = _sse_payloads(
        [
            {
                "type": "source_inspected",
                "eventId": "evt-source-direct-bad-ref",
                "source": {
                    "sourceId": "src_direct_bad",
                    "turnId": "turn-pr3",
                    "kind": "web_fetch",
                    "uri": "https://example.test/source",
                    "contentHash": "not actually an evidence receipt",
                },
            },
            {
                "type": "rule_check",
                "eventId": "evt-rule-direct-bad-ref",
                "turnId": "turn-pr3",
                "ruleId": "claim-citation-gate",
                "verdict": "ok",
                "detail": "would be false authority",
                "evidenceRef": "not actually an evidence receipt",
            },
            {
                "type": "child_completed",
                "eventId": "evt-child-direct-bad-ref",
                "turnId": "turn-pr3",
                "taskId": "child-direct",
                "childReceiptRef": "not actually an evidence receipt",
            },
        ]
    )

    assert [payload["type"] for payload in payloads] == [
        "runtime_trace",
        "runtime_trace",
        "runtime_trace",
    ]
    assert payloads[0]["detail"] == (
        "source_inspected omitted: missing public evidence receipt"
    )
    assert payloads[1]["detail"] == "rule_check omitted: missing public evidence receipt"
    assert payloads[2]["detail"] == "child_completed omitted: missing public child receipt"


def test_pr3_direct_sse_rejects_private_shaped_public_refs() -> None:
    payloads = _sse_payloads(
        [
            {
                "type": "source_inspected",
                "eventId": "evt-source-private-shaped-ref",
                "source": {
                    "sourceId": "src_private_ref",
                    "turnId": "turn-pr3",
                    "kind": "web_fetch",
                    "uri": "https://example.test/source",
                    "contentHash": "source:session-abc",
                },
            },
            {
                "type": "rule_check",
                "eventId": "evt-rule-private-shaped-ref",
                "turnId": "turn-pr3",
                "ruleId": "claim-citation-gate",
                "verdict": "ok",
                "detail": "would be false authority",
                "evidenceRef": "source:token-abc",
            },
            {
                "type": "tool_end",
                "eventId": "evt-tool-private-shaped-ref",
                "id": "tool-direct",
                "status": "ok",
                "output_preview": "completed",
                "transcriptRefs": [
                    "file:secret_key",
                    "source:session-abc",
                    "evidence:web:src_1",
                ],
            },
        ]
    )

    assert [payload["type"] for payload in payloads] == [
        "runtime_trace",
        "runtime_trace",
        "tool_end",
    ]
    assert payloads[0]["detail"] == (
        "source_inspected omitted: missing public evidence receipt"
    )
    assert payloads[1]["detail"] == "rule_check omitted: missing public evidence receipt"
    assert payloads[2]["transcriptRefs"] == ["evidence:web:src_1"]
    encoded = json.dumps(payloads)
    assert "session-abc" not in encoded
    assert "token-abc" not in encoded
    assert "secret_key" not in encoded


def test_pr3_turn_completed_projection_preserves_valid_runtime_receipt() -> None:
    receipt = "receipt:sha256:" + ("9" * 64)
    agent_events = normalized_events_to_agent_events(
        [
            NormalizedEvent(
                type="turn.completed",
                eventId="evt-turn-completed-1",
                ts=8,
                turnId="turn-pr3",
                source="runtime",
                payload={"usage": {"inputTokens": 10, "outputTokens": 4}},
                metadata={"receiptRef": receipt},
            )
        ]
    )
    payloads = _sse_payloads(agent_events)

    assert payloads == [
        {
            "type": "turn_end",
            "turnId": "turn-pr3",
            "status": "committed",
            "receiptRef": receipt,
            "usage": {"inputTokens": 10, "outputTokens": 4, "costUsd": 0},
        }
    ]


def test_pr3_tool_terminal_refs_keep_runtime_and_sse_ref_grammar_aligned() -> None:
    tool_result_ref = "tool-result:run_1.result_1"
    result_ref = "result:run_1.output_1"
    payloads = _sse_payloads(
        normalized_events_to_agent_events(
            [
                NormalizedEvent(
                    type="tool.call.completed",
                    eventId="evt-terminal-refs",
                    ts=1,
                    turnId="turn-pr3",
                    callId="call-terminal-refs",
                    source="tool_kernel",
                    toolName="Search",
                    payload={
                        "status": "ok",
                        "outputPreview": "completed",
                    },
                    metadata={
                        "toolResultRefs": [tool_result_ref, result_ref],
                    },
                )
            ]
        )
    )

    assert payloads == [
        {
            "type": "tool_end",
            "eventId": "evt-terminal-refs",
            "id": "call-terminal-refs",
            "status": "ok",
            "output_preview": "completed",
            "outputDigest": payloads[0]["outputDigest"],
            "transcriptRefs": [tool_result_ref, result_ref],
        }
    ]


def test_pr3_tool_receipts_survive_normalized_payload_sanitization() -> None:
    progress_receipt = "receipt:sha256:" + ("5" * 64)
    terminal_receipt = "receipt:sha256:" + ("6" * 64)
    payloads = _sse_payloads(
        normalized_events_to_agent_events(
            [
                NormalizedEvent(
                    type="tool.call.progress",
                    eventId="evt-tool-progress-payload-receipt",
                    ts=1,
                    turnId="turn-pr3",
                    callId="call-progress-payload-receipt",
                    source="tool_kernel",
                    toolName="Search",
                    payload={
                        "label": "Searching",
                        "receiptRef": progress_receipt,
                    },
                ),
                NormalizedEvent(
                    type="tool.call.completed",
                    eventId="evt-tool-terminal-payload-receipt",
                    ts=2,
                    turnId="turn-pr3",
                    callId="call-terminal-payload-receipt",
                    source="tool_kernel",
                    toolName="Search",
                    payload={
                        "status": "ok",
                        "outputPreview": "completed",
                        "receiptRef": terminal_receipt,
                    },
                ),
            ]
        )
    )

    assert payloads[0] == {
        "type": "tool_progress",
        "eventId": "evt-tool-progress-payload-receipt",
        "id": "call-progress-payload-receipt",
        "label": "Searching",
        "receiptRef": progress_receipt,
    }
    assert payloads[1] == {
        "type": "tool_end",
        "eventId": "evt-tool-terminal-payload-receipt",
        "id": "call-terminal-payload-receipt",
        "status": "ok",
        "output_preview": "completed",
        "outputDigest": payloads[1]["outputDigest"],
        "receiptRef": terminal_receipt,
    }


def test_pr3_direct_tool_end_drops_malformed_terminal_refs() -> None:
    receipt = "receipt:sha256:" + ("1" * 64)
    digest = "sha256:" + ("2" * 64)
    evidence_ref = "evidence:web:src_tool_1"
    payloads = _sse_payloads(
        [
            {
                "type": "tool_end",
                "eventId": "evt-tool-direct-bad-refs",
                "id": "tool-direct",
                "status": "ok",
                "output_preview": "completed",
                "receiptRef": "abc",
                "outputDigest": "not digest",
                "transcriptRefs": ["abc", "private:secret", receipt, digest, evidence_ref],
            }
        ]
    )

    assert payloads == [
        {
            "type": "tool_end",
            "eventId": "evt-tool-direct-bad-refs",
            "id": "tool-direct",
            "status": "ok",
            "output_preview": "completed",
            "transcriptRefs": [receipt, digest, evidence_ref],
        }
    ]


def test_pr3_direct_tool_start_drops_malformed_input_digest() -> None:
    digest = "sha256:" + ("4" * 64)
    payloads = _sse_payloads(
        [
            {
                "type": "tool_start",
                "eventId": "evt-tool-start-bad-digest",
                "id": "call-direct",
                "name": "Search",
                "input_preview": "safe input",
                "inputDigest": "not actually a digest",
            },
            {
                "type": "tool_start",
                "eventId": "evt-tool-start-good-digest",
                "id": "call-direct-2",
                "name": "Search",
                "input_preview": "safe input",
                "inputDigest": digest,
            },
        ]
    )

    assert payloads == [
        {
            "type": "tool_start",
            "eventId": "evt-tool-start-bad-digest",
            "id": "call-direct",
            "name": "Search",
            "input_preview": "safe input",
        },
        {
            "type": "tool_start",
            "eventId": "evt-tool-start-good-digest",
            "id": "call-direct-2",
            "name": "Search",
            "input_preview": "safe input",
            "inputDigest": digest,
        },
    ]


def test_pr3_direct_sse_hashes_private_shaped_event_ids() -> None:
    unsafe_event_id = "session" + "-token-event"
    path_event_id = "/Users/kevin/private/event-id"
    receipt = "receipt:sha256:" + ("5" * 64)
    source_ref = "evidence:source.public"
    payloads = _sse_payloads(
        [
            {
                "type": "tool_start",
                "eventId": unsafe_event_id,
                "id": "call-public",
                "name": "Search",
                "input_preview": "safe input",
            },
            {
                "type": "runtime_trace",
                "eventId": path_event_id,
                "turnId": "turn-pr3",
                "phase": "retry_scheduled",
                "severity": "warning",
                "title": "Retry scheduled",
            },
            {
                "type": "rule_check",
                "eventId": unsafe_event_id,
                "turnId": "turn-pr3",
                "ruleId": "citation.pending",
                "verdict": "pending",
            },
            {
                "type": "source_inspected",
                "eventId": path_event_id,
                "source": {
                    "sourceId": "source-public",
                    "kind": "web_fetch",
                    "uri": "https://example.test/docs",
                    "contentHash": source_ref,
                },
            },
            {
                "type": "child_started",
                "eventId": unsafe_event_id,
                "taskId": "child-public",
                "parentTurnId": "turn-pr3",
                "childReceiptRef": receipt,
                "detail": "starting",
            },
            {
                "type": "heartbeat",
                "eventId": unsafe_event_id,
                "turnId": "turn-pr3",
                "iter": 1,
            },
            {
                "type": "turn_phase",
                "eventId": unsafe_event_id,
                "turnId": "turn-pr3",
                "phase": "executing",
            },
            {
                "type": "source_inspected",
                "eventId": unsafe_event_id,
                "source": {
                    "sourceId": "source-missing-ref",
                    "kind": "web_fetch",
                    "uri": "https://example.test/docs",
                },
            },
        ]
    )

    event_ids = [payload["eventId"] for payload in payloads]

    assert len(event_ids) == 8
    assert event_ids[-1].endswith(":blocked")
    for event_id in event_ids:
        assert event_id.startswith("event:")
        assert "session" not in event_id
        assert "token" not in event_id
        assert "/Users/kevin" not in event_id
        assert "private" not in event_id
        assert "[redacted" not in event_id


def test_pr3_normalized_tool_start_preserves_top_level_input_digest() -> None:
    digest = "sha256:" + ("6" * 64)
    payloads = _sse_payloads(
        normalized_events_to_agent_events(
            [
                NormalizedEvent(
                    type="tool.call.started",
                    eventId="evt-tool-start-normalized-digest",
                    ts=22,
                    turnId="turn-pr3",
                    callId="call-normalized-digest",
                    source="adk",
                    toolName="Search",
                    payload={"inputPreview": "safe input"},
                    metadata={"inputDigest": digest},
                )
            ]
        )
    )

    assert payloads == [
        {
            "type": "tool_start",
            "eventId": "evt-tool-start-normalized-digest",
            "id": "call-normalized-digest",
            "name": "Search",
            "input_preview": f'{{"digest": "{digest}"}}',
            "inputDigest": digest,
        }
    ]


def test_pr3_normalized_digest_metadata_rehashes_malformed_digest_impostors() -> None:
    impostor = "sha256:customer-email-alice@example.test"
    payloads = _sse_payloads(
        normalized_events_to_agent_events(
            [
                NormalizedEvent(
                    type="tool.call.started",
                    eventId="evt-tool-start-digest-impostor",
                    ts=23,
                    turnId="turn-pr3",
                    callId="call-digest-impostor",
                    source="adk",
                    toolName="Search",
                    payload={"inputPreview": "safe input"},
                    metadata={"inputDigest": impostor},
                )
            ]
        )
    )

    payload = payloads[0]

    assert payload["type"] == "tool_start"
    assert payload["inputDigest"].startswith("sha256:")
    assert payload["inputDigest"] != impostor
    assert impostor not in json.dumps(payload)
    assert "alice@example.test" not in json.dumps(payload)


def test_pr3_direct_runtime_trace_and_rule_check_drop_private_shaped_codes() -> None:
    unsafe_code = "source:" + ("tok" + "en-abc")
    evidence_ref = "receipt:sha256:" + ("6" * 64)
    payloads = _sse_payloads(
        [
            {
                "type": "runtime_trace",
                "eventId": "evt-runtime-trace-unsafe-code",
                "turnId": "turn-pr3",
                "phase": "retry_scheduled",
                "severity": "warning",
                "title": "Retry scheduled",
                "reasonCode": unsafe_code,
                "ruleId": unsafe_code,
            },
            {
                "type": "rule_check",
                "eventId": "evt-rule-unsafe-pending",
                "turnId": "turn-pr3",
                "ruleId": unsafe_code,
                "verdict": "pending",
            },
            authorize_rule_check_event(
                {
                    "type": "rule_check",
                    "eventId": "evt-rule-unsafe-valid-evidence",
                    "turnId": "turn-pr3",
                    "ruleId": unsafe_code,
                    "verdict": "ok",
                    "evidenceRef": evidence_ref,
                }
            ),
        ]
    )

    runtime_trace, pending_rule, receipted_rule = payloads

    assert "reasonCode" not in runtime_trace
    assert "ruleId" not in runtime_trace
    assert pending_rule["ruleId"] == "rule"
    assert receipted_rule["ruleId"] == "rule"
    assert receipted_rule["evidenceRef"] == evidence_ref
    assert unsafe_code not in json.dumps(payloads)


def test_pr3_tool_progress_rejects_malformed_receipt_refs() -> None:
    direct_payloads = _sse_payloads(
        [
            {
                "type": "tool_progress",
                "eventId": "evt-tool-progress-direct-bad-ref",
                "id": "call-direct",
                "label": "Running",
                "receiptRef": "abc",
            }
        ]
    )
    normalized_payloads = _sse_payloads(
        normalized_events_to_agent_events(
            [
                NormalizedEvent(
                    type="tool.call.progress",
                    eventId="evt-tool-progress-normalized-bad-ref",
                    ts=9,
                    turnId="turn-pr3",
                    callId="call-normalized",
                    source="tool_kernel",
                    toolName="Search",
                    payload={"label": "Running"},
                    metadata={"receiptRef": "abc"},
                )
            ]
        )
    )

    assert direct_payloads == [
        {
            "type": "tool_progress",
            "eventId": "evt-tool-progress-direct-bad-ref",
            "id": "call-direct",
            "label": "Running",
        }
    ]
    assert normalized_payloads == [
            {
                "type": "runtime_trace",
                "eventId": "evt-tool-progress-normalized-bad-ref:blocked",
                "turnId": "turn-pr3",
                "phase": "verifier_blocked",
            "severity": "warning",
            "title": "Public event omitted",
            "detail": "tool.call.progress omitted: missing public tool receipt",
            "reasonCode": "public_projection_missing_receipt",
            "requiredAction": "retain_typescript_fallback",
        }
    ]


def test_pr3_normalized_projection_redacts_private_markers_before_sse() -> None:
    digest = "sha256:" + ("3" * 64)
    agent_events = normalized_events_to_agent_events(
        [
            NormalizedEvent(
                type="model.message.delta",
                eventId="evt-redacted-text-delta",
                ts=10,
                turnId="turn-pr3",
                source="adk",
                payload={"textPreview": "hidden reasoning: do not show"},
            ),
            NormalizedEvent(
                type="tool.call.completed",
                eventId="evt-redacted-tool-output",
                ts=11,
                turnId="turn-pr3",
                callId="call-redacted-output",
                source="tool_kernel",
                toolName="Search",
                payload={
                    "status": "ok",
                    "outputPreview": "raw tool output: do not show",
                },
                metadata={"outputDigest": digest},
            ),
        ]
    )
    encoded = json.dumps(agent_events)

    assert "hidden reasoning" not in encoded
    assert "raw tool output" not in encoded
    assert agent_events == [
        {
            "type": "text_delta",
            "eventId": "evt-redacted-text-delta",
            "delta": "[redacted-private]",
        },
        {
            "type": "tool_end",
            "eventId": "evt-redacted-tool-output",
            "id": "call-redacted-output",
            "status": "ok",
            "output_preview": "[redacted-private]",
            "outputDigest": digest,
        },
    ]


def test_pr3_normalized_projection_rejects_malformed_authority_refs_before_sse() -> None:
    agent_events = normalized_events_to_agent_events(
        [
            NormalizedEvent(
                type="turn.completed",
                eventId="evt-turn-bad-receipt",
                ts=12,
                turnId="turn-pr3",
                source="runtime",
                metadata={"receiptRef": "not-a-receipt"},
            ),
            NormalizedEvent(
                type="child.completed",
                eventId="evt-child-bad-receipt",
                ts=13,
                turnId="turn-pr3",
                source="runtime",
                payload={
                    "taskId": "child-pr3",
                    "childReceiptRef": "not-a-receipt",
                },
            ),
            NormalizedEvent(
                type="source.inspected",
                eventId="evt-source-bad-ref",
                ts=14,
                turnId="turn-pr3",
                source="runtime",
                payload={
                    "sourceId": "src-pr3",
                    "uri": "https://example.test/source",
                    "evidenceRef": "not-a-receipt",
                },
            ),
            NormalizedEvent(
                type="rule.check",
                eventId="evt-rule-bad-ref",
                ts=15,
                turnId="turn-pr3",
                source="runtime",
                payload={
                    "ruleId": "citation.required",
                    "verdict": "ok",
                    "evidenceRef": "not-a-receipt",
                },
            ),
            NormalizedEvent(
                type="tool.call.completed",
                eventId="evt-tool-bad-refs",
                ts=16,
                turnId="turn-pr3",
                callId="call-bad-refs",
                source="tool_kernel",
                toolName="Search",
                payload={
                    "status": "ok",
                    "outputPreview": "safe output",
                    "receiptRef": "not-a-receipt",
                },
                metadata={
                    "toolResultRefs": ["not-a-receipt"],
                    "sourceRefs": ["not-a-source-ref"],
                },
            ),
        ]
    )

    assert agent_events[:4] == [
        {
            "type": "runtime_trace",
            "eventId": "evt-turn-bad-receipt:blocked",
            "turnId": "turn-pr3",
            "phase": "verifier_blocked",
            "severity": "warning",
            "title": "Public event omitted",
            "detail": "turn.completed omitted: missing public runtime receipt",
            "reasonCode": "public_projection_missing_receipt",
            "requiredAction": "retain_typescript_fallback",
        },
        {
            "type": "runtime_trace",
            "eventId": "evt-child-bad-receipt:blocked",
            "turnId": "turn-pr3",
            "phase": "verifier_blocked",
            "severity": "warning",
            "title": "Public event omitted",
            "detail": "child.completed omitted: missing public child receipt",
            "reasonCode": "public_projection_missing_receipt",
            "requiredAction": "retain_typescript_fallback",
        },
        {
            "type": "runtime_trace",
            "eventId": "evt-source-bad-ref:blocked",
            "turnId": "turn-pr3",
            "phase": "verifier_blocked",
            "severity": "warning",
            "title": "Public event omitted",
            "detail": "source.inspected omitted: missing public evidence receipt",
            "reasonCode": "public_projection_missing_receipt",
            "requiredAction": "retain_typescript_fallback",
        },
        {
            "type": "runtime_trace",
            "eventId": "evt-rule-bad-ref:blocked",
            "turnId": "turn-pr3",
            "phase": "verifier_blocked",
            "severity": "warning",
            "title": "Public event omitted",
            "detail": "rule.check omitted: missing public evidence receipt",
            "reasonCode": "public_projection_missing_receipt",
            "requiredAction": "retain_typescript_fallback",
        },
    ]
    assert agent_events[4] == {
        "type": "tool_end",
        "eventId": "evt-tool-bad-refs",
        "id": "call-bad-refs",
        "status": "ok",
        "output_preview": "safe output",
        "outputDigest": agent_events[4]["outputDigest"],
    }
    assert str(agent_events[4]["outputDigest"]).startswith("sha256:")
    assert "receiptRef" not in agent_events[4]
    assert "transcriptRefs" not in agent_events[4]


def test_pr3_normalized_rule_check_requires_explicit_public_evidence_refs() -> None:
    evidence_rule = "evidence:sha256:" + ("7" * 64)
    verifier_rule = "verifier:sha256:" + ("8" * 64)
    agent_events = normalized_events_to_agent_events(
        [
            NormalizedEvent(
                type="rule.check",
                eventId="evt-rule-evidence-public",
                ts=17,
                turnId="turn-pr3",
                source="runtime",
                payload={
                    "ruleId": evidence_rule,
                    "verdict": "ok",
                    "detail": "evidence verdict state=pass",
                },
            ),
            NormalizedEvent(
                type="rule.check",
                eventId="evt-rule-verifier-public",
                ts=18,
                turnId="turn-pr3",
                source="runtime",
                payload={
                    "ruleId": verifier_rule,
                    "verdict": "violation",
                    "detail": "verifier status=failed",
                },
            ),
            NormalizedEvent(
                type="rule.check",
                eventId="evt-rule-citation-public",
                ts=19,
                turnId="turn-pr3",
                source="runtime",
                payload={
                    "ruleId": "claim-citation-gate",
                    "verdict": "ok",
                    "detail": "citation audit status=ok: checked=1 passed=1",
                },
            ),
            NormalizedEvent(
                type="rule.check",
                eventId="evt-rule-explicit-bad-ref",
                ts=20,
                turnId="turn-pr3",
                source="runtime",
                payload={
                    "ruleId": evidence_rule,
                    "verdict": "ok",
                    "detail": "evidence verdict state=pass",
                    "evidenceRef": "not-a-receipt",
                },
            ),
        ]
    )

    assert agent_events == [
        {
            "type": "runtime_trace",
            "eventId": "evt-rule-evidence-public:blocked",
            "turnId": "turn-pr3",
            "phase": "verifier_blocked",
            "severity": "warning",
            "title": "Public event omitted",
            "detail": "rule.check omitted: missing public evidence receipt",
            "reasonCode": "public_projection_missing_receipt",
            "requiredAction": "retain_typescript_fallback",
        },
        {
            "type": "runtime_trace",
            "eventId": "evt-rule-verifier-public:blocked",
            "turnId": "turn-pr3",
            "phase": "verifier_blocked",
            "severity": "warning",
            "title": "Public event omitted",
            "detail": "rule.check omitted: missing public evidence receipt",
            "reasonCode": "public_projection_missing_receipt",
            "requiredAction": "retain_typescript_fallback",
        },
        {
            "type": "runtime_trace",
            "eventId": "evt-rule-citation-public:blocked",
            "turnId": "turn-pr3",
            "phase": "verifier_blocked",
            "severity": "warning",
            "title": "Public event omitted",
            "detail": "rule.check omitted: missing public evidence receipt",
            "reasonCode": "public_projection_missing_receipt",
            "requiredAction": "retain_typescript_fallback",
        },
        {
            "type": "runtime_trace",
            "eventId": "evt-rule-explicit-bad-ref:blocked",
            "turnId": "turn-pr3",
            "phase": "verifier_blocked",
            "severity": "warning",
            "title": "Public event omitted",
            "detail": "rule.check omitted: missing public evidence receipt",
            "reasonCode": "public_projection_missing_receipt",
            "requiredAction": "retain_typescript_fallback",
        },
    ]

    assert _sse_payloads(agent_events) == agent_events


def test_pr3_sse_source_rule_check_without_explicit_evidence_ref_is_blocked() -> None:
    source_rule = "source:sha256:" + ("9" * 64)

    payloads = _sse_payloads(
        [
            {
                "type": "rule_check",
                "eventId": "evt-rule-source-direct",
                "turnId": "turn-pr3",
                "ruleId": source_rule,
                "verdict": "violation",
                "detail": "source proof status=blocked",
                "checkedAt": 12,
            }
        ]
    )

    assert payloads == [
        {
            "type": "runtime_trace",
            "eventId": "evt-rule-source-direct:blocked",
            "turnId": "turn-pr3",
            "phase": "verifier_blocked",
            "severity": "warning",
            "title": "Public event omitted",
            "detail": "rule_check omitted: missing public evidence receipt",
            "reasonCode": "public_projection_missing_receipt",
            "requiredAction": "retain_typescript_fallback",
        }
    ]


def test_pr3_normalized_runtime_trace_preserves_public_reason_codes() -> None:
    payloads = _sse_payloads(
        normalized_events_to_agent_events(
            [
                NormalizedEvent(
                    type="runtime.trace",
                    eventId="evt-runtime-trace-reason",
                    ts=21,
                    turnId="turn-pr3",
                    source="runtime",
                    payload={
                        "phase": "retry_scheduled",
                        "severity": "warning",
                        "title": "Provider fallback",
                        "detail": "retrying with fallback provider",
                        "reasonCode": "provider_fallback",
                        "ruleId": "claim-citation-gate",
                    },
                )
            ]
        )
    )

    assert payloads == [
        {
            "type": "runtime_trace",
            "eventId": "evt-runtime-trace-reason",
            "turnId": "turn-pr3",
            "phase": "retry_scheduled",
            "severity": "warning",
            "title": "Provider fallback",
            "detail": "retrying with fallback provider",
            "reasonCode": "provider_fallback",
            "ruleId": "claim-citation-gate",
        }
    ]


def test_pr3_sse_rule_check_preserves_event_id_and_turn_id_without_evidence_ref() -> None:
    payloads = _sse_payloads(
        [
            {
                "type": "rule_check",
                "eventId": "evt-rule-direct",
                "turnId": "turn-pr3",
                "ruleId": "citation.pending",
                "verdict": "pending",
                "detail": "waiting for evidence",
                "checkedAt": 12,
            }
        ]
    )

    assert payloads == [
        {
            "type": "rule_check",
            "eventId": "evt-rule-direct",
            "turnId": "turn-pr3",
            "ruleId": "citation.pending",
            "verdict": "pending",
            "detail": "waiting for evidence",
            "checkedAt": 12,
        }
    ]


def test_pr3_sse_citation_gate_ok_without_evidence_ref_is_blocked() -> None:
    payloads = _sse_payloads(
        [
            {
                "type": "rule_check",
                "eventId": "evt-rule-citation-direct",
                "turnId": "turn-pr3",
                "ruleId": "claim-citation-gate",
                "verdict": "ok",
                "detail": "citation audit status=ok: checked=1 passed=1",
                "checkedAt": 12,
            }
        ]
    )

    assert payloads == [
        {
            "type": "runtime_trace",
            "eventId": "evt-rule-citation-direct:blocked",
            "turnId": "turn-pr3",
            "phase": "verifier_blocked",
            "severity": "warning",
            "title": "Public event omitted",
            "detail": "rule_check omitted: missing public evidence receipt",
            "reasonCode": "public_projection_missing_receipt",
            "requiredAction": "retain_typescript_fallback",
        }
    ]


def test_pr3_sse_citation_gate_rejects_synthetic_evidence_ref_authority() -> None:
    direct_payloads = _sse_payloads(
        [
            {
                "type": "rule_check",
                "eventId": "evt-rule-citation-direct-synthetic-ref",
                "turnId": "turn-pr3",
                "ruleId": "claim-citation-gate",
                "verdict": "ok",
                "detail": "citation audit status=ok: checked=1 passed=1",
                "evidenceRef": "evidence:claim-citation-gate",
            }
        ]
    )
    normalized_payloads = _sse_payloads(
        normalized_events_to_agent_events(
            [
                NormalizedEvent(
                    type="rule.check",
                    eventId="evt-rule-citation-normalized-synthetic-ref",
                    ts=12,
                    turnId="turn-pr3",
                    source="runtime",
                    payload={
                        "ruleId": "claim-citation-gate",
                        "verdict": "ok",
                        "detail": "citation audit status=ok: checked=1 passed=1",
                        "evidenceRef": "evidence:claim-citation-gate",
                    },
                )
            ]
        )
    )

    assert direct_payloads == [
        {
            "type": "runtime_trace",
            "eventId": "evt-rule-citation-direct-synthetic-ref:blocked",
            "turnId": "turn-pr3",
            "phase": "verifier_blocked",
            "severity": "warning",
            "title": "Public event omitted",
            "detail": "rule_check omitted: missing public evidence receipt",
            "reasonCode": "public_projection_missing_receipt",
            "requiredAction": "retain_typescript_fallback",
        }
    ]
    assert normalized_payloads == [
        {
            "type": "runtime_trace",
            "eventId": "evt-rule-citation-normalized-synthetic-ref:blocked",
            "turnId": "turn-pr3",
            "phase": "verifier_blocked",
            "severity": "warning",
            "title": "Public event omitted",
            "detail": "rule.check omitted: missing public evidence receipt",
            "reasonCode": "public_projection_missing_receipt",
            "requiredAction": "retain_typescript_fallback",
        }
    ]


def test_pr3_sse_rule_check_rejects_unissued_digest_and_receipt_authority() -> None:
    digest_ref = "sha256:" + ("a" * 64)
    receipt_ref = "receipt:sha256:" + ("b" * 64)

    direct_payloads = _sse_payloads(
        [
            {
                "type": "rule_check",
                "eventId": "evt-rule-direct-digest",
                "turnId": "turn-pr3",
                "ruleId": "claim-citation-gate",
                "verdict": "ok",
                "detail": "citation audit status=ok: checked=1 passed=1",
                "evidenceRef": digest_ref,
            },
            {
                "type": "rule_check",
                "eventId": "evt-rule-direct-receipt",
                "turnId": "turn-pr3",
                "ruleId": "claim-citation-gate",
                "verdict": "ok",
                "detail": "citation audit status=ok: checked=1 passed=1",
                "evidenceRef": receipt_ref,
            },
        ]
    )
    normalized_payloads = _sse_payloads(
        normalized_events_to_agent_events(
            [
                NormalizedEvent(
                    type="rule.check",
                    eventId="evt-rule-normalized-digest",
                    ts=12,
                    turnId="turn-pr3",
                    source="runtime",
                    payload={
                        "ruleId": "claim-citation-gate",
                        "verdict": "ok",
                        "detail": "citation audit status=ok: checked=1 passed=1",
                        "evidenceRef": digest_ref,
                    },
                ),
                NormalizedEvent(
                    type="rule.check",
                    eventId="evt-rule-normalized-receipt",
                    ts=13,
                    turnId="turn-pr3",
                    source="runtime",
                    payload={
                        "ruleId": "claim-citation-gate",
                        "verdict": "ok",
                        "detail": "citation audit status=ok: checked=1 passed=1",
                        "evidenceRef": receipt_ref,
                    },
                ),
            ]
        )
    )

    assert [payload["type"] for payload in direct_payloads] == [
        "runtime_trace",
        "runtime_trace",
    ]
    assert [payload["reasonCode"] for payload in direct_payloads] == [
        "public_projection_missing_receipt",
        "public_projection_missing_receipt",
    ]
    assert [payload["type"] for payload in normalized_payloads] == [
        "runtime_trace",
        "runtime_trace",
    ]
    assert [payload["reasonCode"] for payload in normalized_payloads] == [
        "public_projection_missing_receipt",
        "public_projection_missing_receipt",
    ]


def test_pr3_authorized_rule_check_can_project_receipt_authority() -> None:
    receipt_ref = "receipt:sha256:" + ("c" * 64)
    payloads = _sse_payloads(
        [
            authorize_rule_check_event(
                {
                    "type": "rule_check",
                    "eventId": "evt-rule-authorized",
                    "turnId": "turn-pr3",
                    "ruleId": "claim-citation-gate",
                    "verdict": "ok",
                    "detail": "citation audit status=ok: checked=1 passed=1",
                    "evidenceRef": receipt_ref,
                }
            )
        ]
    )

    assert len(payloads) == 1
    assert payloads[0] == {
        "type": "rule_check",
        "eventId": payloads[0]["eventId"],
        "turnId": "turn-pr3",
        "ruleId": "claim-citation-gate",
        "verdict": "ok",
        "detail": "citation audit status=ok: checked=1 passed=1",
        "evidenceRef": receipt_ref,
        "checkedAt": 0,
    }
    assert str(payloads[0]["eventId"]).startswith("event:")


def test_pr3_sse_redacts_browser_and_document_auth_route_locators() -> None:
    payloads = _sse_payloads(
        [
            {
                "type": "browser_frame",
                "action": "observe",
                "imageBase64": "dGlueS1mcmFtZQ==",
                "contentType": "image/png",
                "url": (
                    "https://example.com/auth/callback/public-code"
                    "?state=public-state ?code=bare-code&state=bare-state"
                ),
            },
            {
                "type": "document_draft",
                "id": "draft-auth-route",
                "filename": "exports/sessions/sess_public_123.md",
                "format": "md",
                "contentPreview": (
                    "redirect=/sessions/sess_public_123 "
                    "callback=/callback?code=public-code#state "
                    "callback?code=relative-code#state=relative-state "
                    "redirect=callback?code=redirect-code state=redirect-state"
                ),
            },
        ]
    )

    dumped = json.dumps(payloads, sort_keys=True)

    assert "public-code" not in dumped
    assert "public-state" not in dumped
    assert "bare-code" not in dumped
    assert "bare-state" not in dumped
    assert "sess_public_123" not in dumped
    assert "relative-code" not in dumped
    assert "relative-state" not in dumped
    assert "redirect-code" not in dumped
    assert "redirect-state" not in dumped
    assert "/auth/callback" not in dumped
    assert "/sessions/" not in dumped
    assert "/callback" not in dumped
    assert "[redacted-ref]" in dumped
