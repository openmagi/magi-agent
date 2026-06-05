"""Tests for control_request sanitization in magi_agent.transport.sse.

TDD Step 1 (RED): written before the control_request branch is added to
_sanitize_agent_event. All tests here must fail until that branch exists.
"""
from __future__ import annotations

from magi_agent.transport import sse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _control_request_event(
    *,
    request_id: str = "req-001",
    tool_name: str = "Bash",
    arguments: dict | None = None,
    reason: str = "Need to run a command",
) -> dict:
    return {
        "type": "control_request",
        "request_id": request_id,
        "tool_name": tool_name,
        "arguments": arguments or {"cmd": "ls -la"},
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# Test 1: control_request event passes through (not dropped)
# ---------------------------------------------------------------------------

def test_control_request_event_survives_sanitize():
    """_sanitize_agent_event must NOT return None for a control_request event."""
    event = _control_request_event()
    result = sse._sanitize_agent_event(event)
    assert result is not None, "control_request must not be dropped"
    assert result["type"] == "control_request"


# ---------------------------------------------------------------------------
# Test 2: request_id and tool_name are preserved
# ---------------------------------------------------------------------------

def test_control_request_fields_preserved():
    """request_id and tool_name must survive sanitization unchanged."""
    event = _control_request_event(
        request_id="corr-123",
        tool_name="FileWrite",
    )
    result = sse._sanitize_agent_event(event)
    assert result is not None
    assert result["request_id"] == "corr-123"
    assert result["tool_name"] == "FileWrite"


# ---------------------------------------------------------------------------
# Test 3: arguments with a sensitive path are redacted
# ---------------------------------------------------------------------------

def test_control_request_sensitive_path_in_arguments_redacted():
    """Sensitive filesystem paths inside arguments must be redacted."""
    event = _control_request_event(
        tool_name="Bash",
        arguments={"cmd": "cat /home/ocuser/.openclaw/secret.key"},
    )
    result = sse._sanitize_agent_event(event)
    assert result is not None
    # The raw sensitive path must NOT appear verbatim in the sanitized output
    import json
    serialized = json.dumps(result)
    assert "/home/ocuser/.openclaw/secret.key" not in serialized


# ---------------------------------------------------------------------------
# Test 4: reason field is sanitized when present
# ---------------------------------------------------------------------------

def test_control_request_reason_sanitized():
    """reason must be included (sanitized) when it is safe public text."""
    event = _control_request_event(reason="Run the list command")
    result = sse._sanitize_agent_event(event)
    assert result is not None
    # A benign reason must survive
    assert "reason" in result
    assert result["reason"]  # non-empty


# ---------------------------------------------------------------------------
# Test 5: reason is omitted or redacted when it contains private markers
# ---------------------------------------------------------------------------

def test_control_request_private_reason_redacted():
    """reason containing a private-text marker must be redacted or dropped."""
    event = _control_request_event(reason="raw tool arguments: very private stuff")
    result = sse._sanitize_agent_event(event)
    assert result is not None
    # If the reason key is present, it must not contain the private phrase verbatim
    if "reason" in result:
        assert "raw tool arguments" not in result["reason"]
