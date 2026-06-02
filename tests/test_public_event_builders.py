from __future__ import annotations

import json

import pytest

from magi_agent.runtime.public_events import (
    child_progress_event,
    heartbeat_event,
    rule_check_event,
    runtime_trace_event,
    source_inspected_event,
    task_board_event,
    tool_end_event,
    tool_blocked_event,
    tool_progress_event,
    tool_start_event,
    turn_phase_event,
)
from magi_agent.transport.sse import InMemorySseWriter


def _agent_payload(event: dict[str, object]) -> dict[str, object]:
    writer = InMemorySseWriter()
    writer.agent(event)
    payloads = [
        json.loads(line.removeprefix("data: "))
        for line in writer.body.splitlines()
        if line.startswith("data: ")
    ]
    assert len(payloads) == 1
    return payloads[0]


def test_builders_emit_sanitizer_accepted_public_shapes() -> None:
    events = [
        turn_phase_event(turn_id="turn-1", phase="planning"),
        heartbeat_event(turn_id="turn-1", iter=2, elapsed_ms=120),
        runtime_trace_event(
            turn_id="turn-1",
            phase="retry_scheduled",
            severity="warning",
            title="Retry scheduled",
            detail="bounded diagnostic",
        ),
        tool_start_event(
            tool_id="tool-1",
            name="Search",
            input_preview='{"query":"docs"}',
        ),
        tool_progress_event(tool_id="tool-1", label="Search", message="Reading docs"),
        tool_end_event(
            tool_id="tool-1",
            status="ok",
            output_preview="completed",
            receipt_refs=["result:sha256:" + ("e" * 64)],
            duration_ms=4,
        ),
        tool_blocked_event(
            tool_id="tool-1",
            reason="policy denied",
            receipt_refs=["receipt:sha256:" + ("a" * 64)],
        ),
        source_inspected_event(
            source_id="src_public_1",
            kind="kb",
            uri="kb://docs/public",
            title="Docs",
            content_hash="sha256:" + ("b" * 64),
            snippets=["public excerpt"],
        ),
        rule_check_event(
            rule_id="citation.required",
            verdict="pending",
            detail="Checking citation evidence",
        ),
        {
            **child_progress_event(task_id="child-1", detail="checking public fields"),
            "childReceiptRef": "receipt:sha256:" + ("e" * 64),
        },
        task_board_event(
            tasks=[
                {"id": "task-1", "title": "Inspect", "status": "completed"},
                {"id": "task-2", "title": "Verify", "dependsOn": ["task-1"]},
            ]
        ),
    ]

    payloads = [_agent_payload(event) for event in events]

    assert [payload["type"] for payload in payloads] == [
        "turn_phase",
        "heartbeat",
        "runtime_trace",
        "tool_start",
        "tool_progress",
        "tool_end",
        "tool_end",
        "source_inspected",
        "rule_check",
        "child_progress",
        "task_board",
    ]


def test_builders_redact_private_paths_secrets_and_drop_raw_fields() -> None:
    github_token = "gh" + "p_" + "abcdefghijklmnopqrstuvwxyz0123456789"
    event = runtime_trace_event(
        turn_id="turn-1",
        phase="terminal_abort",
        severity="error",
        title="Failed at /Users/kevin/secret.txt",
        detail=(
            "Authorization: Bearer live-secret "
            f"TOKEN={github_token} "
            "path=/data/bots/bot-1/private"
        ),
        metadata={
            "rawArgs": {"token": "secret"},
            "rawResult": "secret result",
            "prompt": "hidden prompt",
            "hiddenReasoning": "private reasoning",
            "sourceSnapshot": "full source",
            "headers": {"Authorization": "Bearer secret"},
            "cookies": "session=secret",
            "receiptRef": "receipt:sha256:" + ("c" * 64),
            "policyDigest": "sha256:" + ("d" * 64),
            "privatePath": "/workspace/private/file",
        },
    )

    encoded = json.dumps(event, sort_keys=True)

    assert "live-secret" not in encoded
    assert github_token not in encoded
    assert "/Users/kevin" not in encoded
    assert "/data/bots" not in encoded
    assert "rawArgs" not in event
    assert "rawResult" not in event
    assert "prompt" not in event
    assert "hiddenReasoning" not in event
    assert "sourceSnapshot" not in event
    assert "headers" not in event
    assert "cookies" not in event
    assert event["reasonCode"] == "receipt:sha256:" + ("c" * 64)
    assert event["ruleId"] == "sha256:" + ("d" * 64)
    assert _agent_payload(event)["type"] == "runtime_trace"


def test_tool_builders_redact_private_marker_values_before_sse_sanitizer() -> None:
    start = tool_start_event(
        tool_id="tool-1",
        name="Search",
        input_preview="hidden reasoning: ARG_SECRET",
    )
    end = tool_end_event(
        tool_id="tool-1",
        status="ok",
        output_preview="tool call result RESULT_SECRET",
        receipt_refs=["sha256:" + ("a" * 64)],
    )

    assert start["input_preview"] == "[redacted-private]"
    assert end["output_preview"] == "[redacted-private]"
    assert "ARG_SECRET" not in json.dumps(start)
    assert "RESULT_SECRET" not in json.dumps(end)
    assert _agent_payload(start)["input_preview"] == "[redacted-private]"
    assert _agent_payload(end)["output_preview"] == "[redacted-private]"


def test_builders_redact_common_standalone_secret_shapes() -> None:
    github_pat = "github" + "_pat_" + ("a" * 82)
    slack_token = "xox" + "b-" + ("1" * 12) + "-" + ("2" * 12) + "-" + ("a" * 24)
    aws_key = "AKIA" + ("B" * 16)
    jwt_token = (
        "ey" + "J" + ("a" * 16)
        + "."
        + ("b" * 16)
        + "."
        + ("c" * 16)
    )
    event = runtime_trace_event(
        phase="terminal_abort",
        severity="error",
        detail=(
            f"github {github_pat} slack {slack_token} "
            f"aws {aws_key} jwt {jwt_token}"
        ),
    )

    encoded = json.dumps(event, sort_keys=True)

    assert github_pat not in encoded
    assert slack_token not in encoded
    assert aws_key not in encoded
    assert jwt_token not in encoded
    assert encoded.count("[redacted]") >= 4
    assert _agent_payload(event)["type"] == "runtime_trace"


def test_builders_bound_text_lengths_and_lists() -> None:
    long_text = "x" * 900
    event = tool_progress_event(
        tool_id="tool-1",
        label=long_text,
        message=long_text,
        detail=long_text,
    )
    payload = _agent_payload(event)

    assert len(event["label"]) == 240
    assert len(event["message"]) == 240
    assert len(event["detail"]) == 240
    assert len(payload["label"]) == 240

    board = task_board_event(
        tasks=[
            {
                "id": f"task-{index}",
                "title": long_text,
                "description": long_text,
                "status": "completed",
                "receiptRef": "receipt:sha256:" + ("a" * 64),
            }
            for index in range(60)
        ]
    )

    assert len(board["tasks"]) == 25
    first_task = board["tasks"][0]
    assert isinstance(first_task, dict)
    assert len(first_task["title"]) == 240
    assert len(first_task["description"]) == 240
    assert len(_agent_payload(board)["tasks"]) == 25


def test_turn_phase_builder_matches_existing_sanitizer_public_shape() -> None:
    event = turn_phase_event(
        turn_id="turn-1",
        phase="executing",
        status="running",
        label="ignored by builder",
        message="ignored by builder",
        detail="ignored by builder",
        sequence=7,
        created_at=123,
    )

    assert event == {"type": "turn_phase", "turnId": "turn-1", "phase": "executing"}
    assert _agent_payload(event) == event


def test_child_progress_builder_rejects_deferred_child_family() -> None:
    assert child_progress_event(task_id="child-1", detail="checking") == {
        "type": "child_progress",
        "taskId": "child-1",
        "detail": "checking",
    }
    with pytest.raises(ValueError, match="unsupported event family"):
        child_progress_event(
            task_id="child-1",
            detail="checking",
            event_family="child_execution_and_workspace_default_off",
        )


def test_source_inspected_requires_safe_digest_or_evidence_ref() -> None:
    with pytest.raises(ValueError, match="source inspection requires safe evidence ref"):
        source_inspected_event(
            source_id="src_public_1",
            kind="kb",
            uri="kb://docs/public",
        )

    with pytest.raises(ValueError, match="source inspection requires safe evidence ref"):
        source_inspected_event(
            source_id="src_public_1",
            kind="kb",
            uri="kb://docs/public",
            content_hash="raw evidence body",
        )

    event = source_inspected_event(
        source_id="src_public_1",
        kind="kb",
        uri="kb://docs/public",
        evidence_ref="receipt:sha256:" + ("c" * 64),
    )

    assert event["source"]["contentHash"] == "receipt:sha256:" + ("c" * 64)
    assert _agent_payload(event)["source"]["contentHash"] == event["source"]["contentHash"]


def test_source_inspected_builder_redacts_auth_callback_uri() -> None:
    for uri in [
        "https://example.test/oauth/callback?code=abc123&state=secretstate#session-frag",
        "https://example.test/oauth-callback?code=abc123&state=secretstate#session-frag",
        "https://example.test/oauth_callback?code=abc123&state=secretstate#session-frag",
        "https://example.test/oauth%2Dcallback?code=abc123&state=secretstate",
        "https://example.test/oauth%252Dcallback?code=abc123&state=secretstate",
        "https://example.test/public?c%6f%64%65=abc123",
        "https://example.test/public%253Fc%256f%2564%2565=abc123",
        "https://example.test/public?c%6f%64%65=abc123&state=secretstate",
        "https://example.test/public%253Fc%256f%2564%2565=abc123%2526state=secretstate",
        "https://example.test/public%3Fcode=abc123&state=secretstate",
        "https://example.test/public?callback=abc123&next=/safe",
        "https://example.test/public#callback=abc123",
        "https://example.test/public%23callback=abc123",
        "https://example.test/public%2523callback=abc123",
        "https://example.test/oauth%252Fcallback%253Fcode=abc123%2526state=secretstate",
        "https://example.test/public%253Fcode=abc123%2526state=secretstate",
    ]:
        event = source_inspected_event(
            source_id="src_public_1",
            kind="browser",
            uri=uri,
            evidence_ref="receipt:sha256:" + ("c" * 64),
        )

        assert "abc123" not in event["source"]["uri"]
        assert "secretstate" not in event["source"]["uri"]
        encoded = json.dumps(event, sort_keys=True)
        assert "abc123" not in encoded
        assert "secretstate" not in encoded
        if "public" in uri:
            assert event["source"]["uri"] == "https://example.test/public[redacted-query]"
        else:
            assert event["source"]["uri"] == "[redacted-path]"
            assert "oauth" not in encoded
            assert "callback" not in encoded
        assert _agent_payload(event)["source"]["uri"] == event["source"]["uri"]


def test_source_inspected_builder_redacts_callback_uri_from_public_text_fields() -> None:
    event = source_inspected_event(
        source_id="src_public_1",
        kind="browser",
        uri="https://example.test/public",
        title="Opened https://example.test/oauth/callback?code=abc123&state=secretstate",
        snippets=[
            "Read https://example.test/public?c%6f%64%65=abc123&state=secretstate",
        ],
        evidence_ref="receipt:sha256:" + ("c" * 64),
    )

    encoded = json.dumps(event, sort_keys=True)

    assert "abc123" not in encoded
    assert "secretstate" not in encoded
    assert "oauth" not in encoded
    assert "callback" not in encoded
    assert event["source"]["title"] == "Opened [redacted-path]"
    assert event["source"]["snippets"] == [
        "Read https://example.test/public[redacted-query]",
    ]
    assert _agent_payload(event)["source"]["title"] == event["source"]["title"]
    assert _agent_payload(event)["source"]["snippets"] == event["source"]["snippets"]


def test_task_board_downgrades_completed_tasks_even_with_safe_receipt_ref() -> None:
    event = task_board_event(
        tasks=[
            {"id": "task-1", "title": "Missing evidence", "status": "completed"},
            {
                "id": "task-2",
                "title": "Unsafe evidence",
                "status": "completed",
                "receiptRef": "raw evidence body",
            },
            {
                "id": "task-3",
                "title": "Safe evidence",
                "status": "completed",
                "evidenceRef": "sha256:" + ("d" * 64),
            },
        ]
    )

    assert event["tasks"] == [
        {
            "id": "task-1",
            "title": "Missing evidence",
            "description": "",
            "status": "in_progress",
        },
        {
            "id": "task-2",
            "title": "Unsafe evidence",
            "description": "",
            "status": "in_progress",
        },
        {
            "id": "task-3",
            "title": "Safe evidence",
            "description": "",
            "status": "in_progress",
        },
    ]
    assert _agent_payload(event) == event


def test_builders_keep_digest_and_ref_only_metadata() -> None:
    event = tool_blocked_event(
        tool_id="tool-1",
        reason="blocked",
        receipt_refs=[
            "receipt:sha256:" + ("a" * 64),
            "raw receipt payload with Authorization: Bearer token",
            "sha256:" + ("b" * 64),
        ],
    )

    assert event == {
        "type": "tool_end",
        "id": "tool-1",
        "status": "error",
        "error": "blocked",
        "output_preview": "blocked",
        "transcriptRefs": [
            "receipt:sha256:" + ("a" * 64),
            "sha256:" + ("b" * 64),
        ],
    }
    payload = _agent_payload(event)
    assert payload["transcriptRefs"] == event["transcriptRefs"]


def test_invalid_event_family_is_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported event family"):
        turn_phase_event(
            turn_id="turn-1",
            phase="planning",
            event_family="raw_provider_event",
        )
