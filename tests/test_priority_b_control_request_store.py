from __future__ import annotations

import json

from magi_agent.runtime.control import ControlRequestStore


LEAKY_INPUT = {
    "command": "curl -H 'Authorization: Bearer live-token' https://example.test",
    "patch": "*** Begin Patch\n*** Update File: /Users/kevin/private/app.py\n+sk-live-secret\n*** End Patch",
    "headers": {"Cookie": "sid=opaque-cookie"},
    "api_key": "sk-project-openai-secret",
    "path": "/Users/kevin/Desktop/private/repo/.env",
}
CHILD_PROMPT = "child prompt: gather private account context"
CHILD_STDOUT = "secret raw stdout from child task"
CHILD_STDERR = "secret raw stderr from child task"
CHILD_OUTPUT = "secret raw output from child task"
PRIVATE_PATHS = (
    "/workspace/project/.env",
    "/data/bots/bot-123/workspace/secret.txt",
    "/var/lib/kubelet/pods/pod-123/volumes/kubernetes.io~csi/token",
    "/tmp/opencode-inspect/workspace/private.log",
    "/tmp/openmagi-workspace-abc/private.log",
)


def _dump(value: object) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _assert_no_private_payload(value: object) -> None:
    dumped = _dump(value)
    assert "live-token" not in dumped
    assert "opaque-cookie" not in dumped
    assert "sk-live-secret" not in dumped
    assert "sk-project-openai-secret" not in dumped
    assert "/Users/kevin" not in dumped
    assert "*** Begin Patch" not in dumped
    assert CHILD_PROMPT not in dumped
    assert CHILD_STDOUT not in dumped
    assert CHILD_STDERR not in dumped
    assert CHILD_OUTPUT not in dumped
    for private_path in PRIVATE_PATHS:
        assert private_path not in dumped


def test_tool_permission_create_stores_ts_shaped_pending_record_and_dedupes() -> None:
    store = ControlRequestStore()

    created = store.create_tool_permission_request(
        session_key="agent:main:app:default",
        turn_id="turn-1",
        channel_name="app",
        source="turn",
        prompt="Allow Bash with Authorization: Bearer raw-token from /Users/kevin/private?",
        proposed_input=LEAKY_INPUT,
        idempotency_key="idem-tool-1",
        now=1000,
        timeout_ms=30_000,
    )
    duplicate = store.create_tool_permission_request(
        session_key="agent:main:app:default",
        turn_id="turn-1",
        channel_name="app",
        source="turn",
        prompt="different duplicate prompt should not replace the first",
        proposed_input={"command": "rm -rf /Users/kevin/private"},
        idempotency_key="idem-tool-1",
        now=1005,
        timeout_ms=30_000,
    )

    assert store.durable_writes_enabled is False
    assert store.production_writes_enabled is False
    assert created.duplicate is False
    assert duplicate.duplicate is True
    assert duplicate.events == ()
    assert duplicate.record == created.record
    assert store.get_pending(created.record.request_id) == created.record
    assert created.event is not None
    assert created.event.type == "control_request_created"
    assert created.event.idempotency_key == "idem-tool-1"
    assert created.record.model_dump(by_alias=True) == {
        "requestId": created.record.request_id,
        "kind": "tool_permission",
        "state": "pending",
        "sessionKey": "agent:main:app:default",
        "turnId": "turn-1",
        "channelName": "app",
        "source": "turn",
        "prompt": created.record.prompt,
        "proposedInput": created.record.proposed_input,
        "createdAt": 1000,
        "expiresAt": 31_000,
        "resolvedAt": None,
        "decision": None,
        "feedback": None,
        "updatedInput": None,
        "answer": None,
        "cancelReason": None,
        "idempotencyKey": "idem-tool-1",
        "waiterResolution": None,
    }
    _assert_no_private_payload(created.record.model_dump(by_alias=True))
    _assert_no_private_payload(created.event.model_dump(by_alias=True))


def test_terminal_resolve_states_are_idempotent_and_removed_from_pending() -> None:
    store = ControlRequestStore()
    approved = store.create_tool_permission_request(
        session_key="session-1",
        turn_id="turn-1",
        channel_name="telegram",
        source="turn",
        prompt="Allow edit?",
        proposed_input=LEAKY_INPUT,
        idempotency_key="idem-approved",
        now=1,
        timeout_ms=60_000,
    )

    resolved = store.resolve_request(
        approved.record.request_id,
        decision="approved",
        updated_input={"command": "pytest", "Authorization": "Bearer raw-token"},
        now=2,
    )
    duplicate = store.resolve_request(
        approved.record.request_id,
        decision="approved",
        updated_input={"command": "pytest", "Authorization": "Bearer raw-token"},
        now=3,
    )

    assert resolved.record.state == "approved"
    assert resolved.record.decision == "approved"
    assert resolved.record.updated_input == {
        "commandPreview": "[redacted-command]",
        "Authorization": "[redacted]",
    }
    assert store.get_pending(approved.record.request_id) is None
    assert store.get_terminal(approved.record.request_id) == resolved.record
    assert resolved.event is not None
    assert resolved.event.type == "control_request_resolved"
    assert duplicate.duplicate is True
    assert duplicate.events == ()
    assert duplicate.record == resolved.record
    _assert_no_private_payload(resolved.record.model_dump(by_alias=True))

    denied = store.create_tool_permission_request(
        session_key="session-1",
        turn_id="turn-2",
        channel_name="app",
        source="turn",
        prompt="Allow deploy?",
        proposed_input=LEAKY_INPUT,
        idempotency_key="idem-denied",
        now=10,
        timeout_ms=60_000,
    )
    denied_result = store.resolve_request(
        denied.record.request_id,
        decision="denied",
        feedback="No, it contains Cookie: sid=opaque-cookie",
        now=11,
    )
    assert denied_result.record.state == "denied"
    assert denied_result.record.feedback == "No, it contains Cookie: [redacted]"
    _assert_no_private_payload(denied_result.record.model_dump(by_alias=True))

    answered = store.create_tool_permission_request(
        session_key="session-1",
        turn_id="turn-3",
        channel_name="app",
        source="turn",
        prompt="Question?",
        proposed_input={"prompt": "child prompt with sk-project-openai-secret"},
        idempotency_key="idem-answered",
        now=20,
        timeout_ms=60_000,
    )
    answered_result = store.resolve_request(
        answered.record.request_id,
        decision="answered",
        answer="Use the safe read-only path.",
        now=21,
    )
    assert answered_result.record.state == "answered"
    assert answered_result.record.answer == "Use the safe read-only path."


def test_child_prompt_logs_and_private_paths_are_hard_redacted_everywhere() -> None:
    store = ControlRequestStore()
    created = store.create_tool_permission_request(
        session_key="session-1",
        turn_id="turn-child",
        channel_name="app",
        source="child-agent",
        prompt=CHILD_PROMPT,
        proposed_input={
            "prompt": CHILD_PROMPT,
            "logs": [CHILD_STDOUT, CHILD_STDERR],
            "stdout": CHILD_STDOUT,
            "stderr": CHILD_STDERR,
            "output": {"nested": CHILD_OUTPUT},
            "records": [{"path": PRIVATE_PATHS[0]}],
            "workspacePath": PRIVATE_PATHS[1],
            "kubeletPath": PRIVATE_PATHS[2],
            "inspectionPath": PRIVATE_PATHS[3],
            "tempWorkspacePath": PRIVATE_PATHS[4],
        },
        idempotency_key="idem-child-redaction",
        now=1,
        timeout_ms=60_000,
    )
    resolved = store.resolve_request(
        created.record.request_id,
        decision="approved",
        updated_input={
            "prompt": CHILD_PROMPT,
            "logs": CHILD_STDOUT,
            "stdout": CHILD_STDOUT,
            "stderr": CHILD_STDERR,
            "output": CHILD_OUTPUT,
            "path": PRIVATE_PATHS[0],
        },
        now=2,
    )

    assert created.record.prompt == "[redacted-prompt]"
    assert created.record.proposed_input["promptPreview"] == "[redacted-prompt]"
    assert created.record.proposed_input["logsPreview"] == "[redacted-output]"
    assert created.record.proposed_input["stdoutPreview"] == "[redacted-output]"
    assert created.record.proposed_input["stderrPreview"] == "[redacted-output]"
    assert created.record.proposed_input["outputPreview"] == "[redacted-output]"
    assert resolved.record.updated_input["promptPreview"] == "[redacted-prompt]"
    assert resolved.record.updated_input["logsPreview"] == "[redacted-output]"
    assert resolved.record.updated_input["stdoutPreview"] == "[redacted-output]"
    assert resolved.record.updated_input["stderrPreview"] == "[redacted-output]"
    assert resolved.record.updated_input["outputPreview"] == "[redacted-output]"
    _assert_no_private_payload(created.record.model_dump(by_alias=True))
    _assert_no_private_payload(created.event.model_dump(by_alias=True))
    _assert_no_private_payload(resolved.record.model_dump(by_alias=True))
    _assert_no_private_payload(resolved.event.model_dump(by_alias=True))


def test_timeout_and_cancel_emit_terminal_events_without_durable_writes() -> None:
    store = ControlRequestStore()
    request = store.create_tool_permission_request(
        session_key="session-1",
        turn_id="turn-timeout",
        channel_name="app",
        source="turn",
        prompt="Allow command?",
        proposed_input=LEAKY_INPUT,
        idempotency_key="idem-timeout",
        now=100,
        timeout_ms=5,
    )

    early = store.expire_request(request.record.request_id, now=104)
    timed_out = store.expire_request(request.record.request_id, now=105)

    assert early is None
    assert timed_out is not None
    assert timed_out.record.state == "timed_out"
    assert timed_out.record.waiter_resolution == {
        "decision": "denied",
        "execute": False,
        "reason": "control_request_timed_out",
    }
    assert timed_out.event is not None
    assert timed_out.event.type == "control_request_timed_out"
    assert store.get_pending(request.record.request_id) is None
    assert store.get_terminal(request.record.request_id) == timed_out.record

    cancel_request = store.create_tool_permission_request(
        session_key="session-1",
        turn_id="turn-cancel",
        channel_name="app",
        source="turn",
        prompt="Allow cancel?",
        proposed_input=LEAKY_INPUT,
        idempotency_key="idem-cancel",
        now=200,
        timeout_ms=60_000,
    )
    cancelled = store.cancel_request(
        cancel_request.record.request_id,
        reason="turn_cancelled",
        now=201,
    )

    assert cancelled.record.state == "cancelled"
    assert cancelled.record.cancel_reason == "turn_cancelled"
    assert cancelled.event is not None
    assert cancelled.event.type == "control_request_cancelled"
    assert store.get_pending(cancel_request.record.request_id) is None
    assert store.get_terminal(cancel_request.record.request_id) == cancelled.record
    assert store.durable_writes_enabled is False
    assert store.production_writes_enabled is False
