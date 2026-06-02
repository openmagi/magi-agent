from __future__ import annotations

import json

from magi_agent.evidence.tool_boundary import (
    ToolEvidenceBoundary,
    build_denied_tool_error_evidence,
    build_tool_call_evidence,
    build_tool_exception_evidence,
    build_tool_result_evidence,
    build_tool_timeout_evidence,
)


RAW_ARGS = {
    "command": "python deploy.py --api_key sk-project-openai-secret",
    "patch": "*** Begin Patch\n*** Update File: /Users/kevin/private/app.py\n+rk_live_stripe_secret\n*** End Patch",
    "headers": {
        "Authorization": "Bearer live-token",
        "Cookie": "sid=opaque-cookie",
    },
    "path": "/Users/kevin/Desktop/secret/repo/.env",
    "prompt": "child prompt with ghp_githubsecret",
}
TOP_LEVEL_RESULT = "secret raw top-level result from child prompt"
EXCEPTION_MESSAGE = "boom secret exception from /workspace/project/.env"
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
    assert "sk-project-openai-secret" not in dumped
    assert "rk_live_stripe_secret" not in dumped
    assert "live-token" not in dumped
    assert "opaque-cookie" not in dumped
    assert "ghp_githubsecret" not in dumped
    assert "/Users/kevin" not in dumped
    assert "*** Begin Patch" not in dumped
    assert "deploy.py --api_key" not in dumped
    assert "secret raw stdout" not in dumped
    assert "child prompt" not in dumped
    assert TOP_LEVEL_RESULT not in dumped
    assert EXCEPTION_MESSAGE not in dumped
    assert "secret exception" not in dumped
    for private_path in PRIVATE_PATHS:
        assert private_path not in dumped


def test_tool_call_and_result_evidence_are_linked_and_redacted() -> None:
    call = build_tool_call_evidence(
        tool_call_id="call-1",
        tool_id="bash",
        tool_name="Bash",
        args=RAW_ARGS,
        observed_at=100,
    )
    result = build_tool_result_evidence(
        tool_call_id="call-1",
        tool_id="bash",
        tool_name="Bash",
        status="ok",
        result={
            "stdout": "secret raw stdout Authorization: Bearer live-token",
            "exitCode": 0,
            "path": "/Users/kevin/Desktop/secret/repo/output.txt",
        },
        duration_ms=42,
        observed_at=142,
    )

    assert call.kind == "tool_call"
    assert call.tool_call_id == "call-1"
    assert call.terminal is False
    assert call.arg_summary["commandPreview"] == "[redacted-command]"
    assert "argsHash" in call.model_dump(by_alias=True)
    assert "args" not in call.model_dump(by_alias=True)
    assert result.kind == "tool_result"
    assert result.tool_call_id == "call-1"
    assert result.terminal is True
    assert result.status == "ok"
    assert result.duration_ms == 42
    assert "resultHash" in result.model_dump(by_alias=True)
    assert "result" not in result.model_dump(by_alias=True)
    _assert_no_private_payload(call.model_dump(by_alias=True))
    _assert_no_private_payload(result.model_dump(by_alias=True))


def test_policy_failures_create_terminal_error_evidence_without_execution() -> None:
    for reason, expected_code, expected_status in [
        ("denied", "tool_denied", "denied"),
        ("not_found", "tool_not_found", "not_found"),
        ("not_exposed", "tool_not_exposed", "not_exposed"),
        ("missing_handler", "tool_missing_handler", "not_found"),
    ]:
        error = build_denied_tool_error_evidence(
            tool_call_id=f"call-{reason}",
            tool_id="dangerous",
            tool_name="DangerousTool",
            reason=reason,
            message="No Authorization: Bearer live-token from /Users/kevin/private",
            observed_at=200,
        )

        assert error.kind == "tool_error"
        assert error.terminal is True
        assert error.executed is False
        assert error.status == expected_status
        assert error.error_code == expected_code
        _assert_no_private_payload(error.model_dump(by_alias=True))


def test_exception_and_timeout_use_stable_codes_and_redacted_summaries() -> None:
    exception = build_tool_exception_evidence(
        tool_call_id="call-throw",
        tool_id="bash",
        tool_name="Bash",
        error=RuntimeError("boom Authorization: Bearer live-token at /Users/kevin/private"),
        duration_ms=55,
        observed_at=300,
    )
    timeout = build_tool_timeout_evidence(
        tool_call_id="call-timeout",
        tool_id="bash",
        tool_name="Bash",
        timeout_ms=1000,
        duration_ms=1001,
        observed_at=400,
    )

    assert exception.kind == "tool_error"
    assert exception.status == "error"
    assert exception.error_code == "tool_threw"
    assert exception.duration_ms == 55
    assert timeout.kind == "tool_timeout"
    assert timeout.status == "error"
    assert timeout.error_code == "tool_timeout"
    assert timeout.duration_ms == 1001
    assert timeout.result_summary == {"timeoutMs": 1000}
    _assert_no_private_payload(exception.model_dump(by_alias=True))
    _assert_no_private_payload(timeout.model_dump(by_alias=True))


def test_top_level_string_results_emit_metadata_without_raw_text() -> None:
    result = build_tool_result_evidence(
        tool_call_id="call-string-result",
        tool_id="bash",
        tool_name="Bash",
        status="ok",
        result=TOP_LEVEL_RESULT,
        duration_ms=42,
        observed_at=600,
    )

    assert result.result_summary["type"] == "str"
    assert result.result_summary["size"] == len(TOP_LEVEL_RESULT)
    assert result.result_summary["preview"] == "[redacted-output]"
    assert str(result.result_summary["sha256"]).startswith("sha256:")
    _assert_no_private_payload(result.model_dump(by_alias=True))


def test_top_level_non_mapping_call_args_emit_preview_without_crashing() -> None:
    call = build_tool_call_evidence(
        tool_call_id="call-scalar",
        tool_id="bash",
        tool_name="Bash",
        args="plain argument",
        observed_at=650,
    )

    assert call.arg_summary == {"preview": "plain argument"}
    _assert_no_private_payload(call.model_dump(by_alias=True))


def test_exception_evidence_emits_metadata_without_raw_message() -> None:
    exception = build_tool_exception_evidence(
        tool_call_id="call-exception-redacted",
        tool_id="bash",
        tool_name="Bash",
        error=RuntimeError(EXCEPTION_MESSAGE),
        duration_ms=55,
        observed_at=700,
    )

    assert exception.error_message == "[redacted-error]"
    assert exception.result_summary["exceptionType"] == "RuntimeError"
    assert exception.result_summary["messageSize"] == len(EXCEPTION_MESSAGE)
    assert str(exception.result_summary["messageHash"]).startswith("sha256:")
    _assert_no_private_payload(exception.model_dump(by_alias=True))


def test_private_runtime_paths_are_redacted_in_args_results_and_errors() -> None:
    call = build_tool_call_evidence(
        tool_call_id="call-private-paths",
        tool_id="bash",
        tool_name="Bash",
        args={
            "workspacePath": PRIVATE_PATHS[0],
            "botDataPath": PRIVATE_PATHS[1],
            "kubeletPath": PRIVATE_PATHS[2],
            "inspectionPath": PRIVATE_PATHS[3],
            "tempWorkspacePath": PRIVATE_PATHS[4],
        },
        observed_at=800,
    )
    result = build_tool_result_evidence(
        tool_call_id="call-private-paths",
        tool_id="bash",
        tool_name="Bash",
        status="ok",
        result={"path": PRIVATE_PATHS[0], "stdout": f"wrote {PRIVATE_PATHS[1]}"},
        duration_ms=10,
        observed_at=810,
    )
    denied = build_denied_tool_error_evidence(
        tool_call_id="call-private-paths",
        tool_id="bash",
        tool_name="Bash",
        reason="denied",
        message=f"denied read at {PRIVATE_PATHS[2]}",
        observed_at=820,
    )

    _assert_no_private_payload(call.model_dump(by_alias=True))
    _assert_no_private_payload(result.model_dump(by_alias=True))
    _assert_no_private_payload(denied.model_dump(by_alias=True))


def test_tool_evidence_redacts_key_named_result_credentials() -> None:
    result = build_tool_result_evidence(
        tool_call_id="call-key-credentials",
        tool_id="echo",
        tool_name="Echo",
        status="ok",
        result={
            "metadata": {
                "serviceKey": "plain-service-secret",
                "service_key": "plain-service-secret-snake",
                "credentialId": "plain-credential-id",
                "apiKey": "plain-api-key",
                "safeCount": 1,
            }
        },
        duration_ms=10,
        observed_at=830,
    )
    dumped = _dump(result.model_dump(by_alias=True))

    for forbidden in (
        "plain-service-secret",
        "plain-service-secret-snake",
        "plain-credential-id",
        "plain-api-key",
    ):
        assert forbidden not in dumped
    assert result.result_summary["metadata"]["safeCount"] == 1


def test_boundary_serialization_never_copies_raw_args_results_or_logs() -> None:
    boundary = ToolEvidenceBoundary()
    records = boundary.record_pair(
        tool_call_id="call-serial",
        tool_id="bash",
        tool_name="Bash",
        args=RAW_ARGS,
        status="ok",
        result={
            "stdout": "secret raw stdout",
            "logs": ["child prompt", "Authorization: Bearer live-token"],
        },
        duration_ms=10,
        observed_at=500,
    )

    serialized = [record.model_dump(by_alias=True) for record in records]

    assert [record["kind"] for record in serialized] == ["tool_call", "tool_result"]
    assert all(record["toolCallId"] == "call-serial" for record in serialized)
    assert all("args" not in record for record in serialized)
    assert all("result" not in record for record in serialized)
    _assert_no_private_payload(serialized)
