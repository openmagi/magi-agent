"""Tests for ADK error-message over-redaction fix (event_adapter.py).

Design doc: docs/plans/2026-07-12-adk-error-over-redaction-and-abort-diagnosis.md

Section 6 test plan, 9 cases:

1. Readable error with "prompt" trigger no longer whole-digests (partial redaction OK).
2. Readable error with tool-call trigger no longer whole-digests.
3. Secrets inside error messages are still scrubbed by _public_text.
4. Whole-JSON error body still key-digests via _parse_json_container branch.
5. Long error message is length-capped with "...".
6. Benign finish signals (STOP / completed) do NOT project terminal_abort.
7. Blast-radius pin: tool_start input_preview with a prompt key still digests.
8. Observability projector composes summary as "code: message" for error events.
9. SSE second-layer sanitize_error_event handles readable error messages.
"""

from __future__ import annotations

import os
from typing import Any
from unittest import mock

import pytest
from google.adk.events import Event
from google.genai import types

from magi_agent.adk_bridge import event_adapter as ea
from magi_agent.adk_bridge.event_adapter import OpenMagiEventBridge
from magi_agent.observability.projector import project_public_event
from magi_agent.transport import sse


# ---------------------------------------------------------------------------
# Helper: build an ADK error event
# ---------------------------------------------------------------------------


def _error_event(*, error_code: str | None = None, error_message: str | None = None) -> Event:
    return Event(
        id="ev-err-1",
        author="model",
        error_code=error_code,
        error_message=error_message,
        invocation_id="turn-err",
    )


def _bridge(*, live: bool = True) -> OpenMagiEventBridge:
    return OpenMagiEventBridge(live_compatible=live)


# ---------------------------------------------------------------------------
# Case 1: "prompt" keyword in error message no longer whole-string digests
# ---------------------------------------------------------------------------


def test_prompt_keyword_error_not_whole_digested() -> None:
    """An error containing 'prompt' must NOT be whole-string digested.

    After the fix _public_error_text calls _public_text (not _public_preview),
    so partial vocabulary matches cause targeted redaction of the matching
    term only. The surrounding non-private text must survive.
    'prompt' is matched by _PRIVATE_TEXT_RE in event_adapter, so it becomes
    '[redacted-private]'. The rest ("invalid", "too long") survives.
    """
    bridge = _bridge()
    event = _error_event(error_message="invalid prompt: too long")
    proj = bridge.project_adk_event(event, turn_id="turn-1")

    runtime_trace = next(e for e in proj.agent_events if e["type"] == "runtime_trace")
    error_event_item = next(e for e in proj.agent_events if e["type"] == "error")
    turn_end = next(e for e in proj.agent_events if e["type"] == "turn_end")

    # Must NOT be a whole-string digest
    assert not str(runtime_trace["detail"]).startswith('{"digest"'), (
        f"detail was whole-digest: {runtime_trace['detail']}"
    )
    assert not str(error_event_item["message"]).startswith('{"digest"'), (
        f"error message was whole-digest: {error_event_item['message']}"
    )
    assert not str(turn_end["reason"]).startswith('{"digest"'), (
        f"turn_end reason was whole-digest: {turn_end['reason']}"
    )

    # Phase must be terminal_abort
    assert runtime_trace["phase"] == "terminal_abort"

    # Non-private surrounding text must survive (vocabulary match redacts only the term)
    assert "invalid" in str(runtime_trace["detail"]), (
        f"surrounding text 'invalid' missing from detail: {runtime_trace['detail']}"
    )
    assert "too long" in str(runtime_trace["detail"]), (
        f"surrounding text 'too long' missing from detail: {runtime_trace['detail']}"
    )


# ---------------------------------------------------------------------------
# Case 2: tool-call keyword in error message no longer whole-digests
# ---------------------------------------------------------------------------


def test_function_call_keyword_error_not_whole_digested() -> None:
    """An error containing 'function call' must NOT be whole-string digested.

    'function call' matches _PRIVATE_TEXT_RE so it gets partial redaction,
    but the surrounding terms survive readable.
    """
    bridge = _bridge()
    event = _error_event(error_message="malformed function call arguments: bad shape")
    proj = bridge.project_adk_event(event, turn_id="turn-2")

    runtime_trace = next(e for e in proj.agent_events if e["type"] == "runtime_trace")
    error_event_item = next(e for e in proj.agent_events if e["type"] == "error")

    # Must not be whole-digest
    assert not str(runtime_trace["detail"]).startswith('{"digest"'), (
        f"detail was whole-digest: {runtime_trace['detail']}"
    )
    assert not str(error_event_item["message"]).startswith('{"digest"'), (
        f"error message was whole-digest: {error_event_item['message']}"
    )

    # Non-matching surrounding words survive
    assert "malformed" in str(runtime_trace["detail"]), (
        f"'malformed' missing from detail: {runtime_trace['detail']}"
    )
    assert "bad shape" in str(runtime_trace["detail"]), (
        f"'bad shape' missing from detail: {runtime_trace['detail']}"
    )


# ---------------------------------------------------------------------------
# Case 3: real secret tokens are still scrubbed by _public_text
# ---------------------------------------------------------------------------


def test_github_pat_in_error_is_redacted() -> None:
    """A GitHub PAT embedded in an error message is redacted by _public_text."""
    token = "ghp_" + "A" * 36  # synthetic token shape (ghp_ prefix triggers _GITHUB_PAT_RE)
    bridge = _bridge()
    event = _error_event(error_message=f"upstream 401: token {token} rejected")
    proj = bridge.project_adk_event(event, turn_id="turn-3")

    runtime_trace = next(e for e in proj.agent_events if e["type"] == "runtime_trace")
    assert token not in str(runtime_trace["detail"]), (
        f"GitHub PAT leaked in detail: {runtime_trace['detail']}"
    )
    assert "[redacted]" in str(runtime_trace["detail"])
    # surrounding text survives
    assert "upstream 401" in str(runtime_trace["detail"])
    assert "rejected" in str(runtime_trace["detail"])


def test_rate_limit_error_no_secrets_fully_readable() -> None:
    """A plain rate-limit error with no private keywords stays fully readable."""
    bridge = _bridge()
    event = _error_event(
        error_code="rate_limit_exceeded",
        error_message="litellm.RateLimitError: Fireworks 429 too many requests",
    )
    proj = bridge.project_adk_event(event, turn_id="turn-3b")

    runtime_trace = next(e for e in proj.agent_events if e["type"] == "runtime_trace")
    detail = str(runtime_trace["detail"])
    assert not detail.startswith('{"digest"')
    # None of the terms trigger private-key or credential regexes
    assert "RateLimitError" in detail or "rate" in detail.lower()


# ---------------------------------------------------------------------------
# Case 4: Whole-JSON error body still key-digests
# ---------------------------------------------------------------------------


def test_json_error_body_key_digests_private_keys() -> None:
    """A JSON-string error body with a 'prompt' key gets key-digested, not whole-digested."""
    bridge = _bridge()
    event = _error_event(error_message='{"prompt": "SECRET", "code": "bad_request"}')
    proj = bridge.project_adk_event(event, turn_id="turn-4")

    runtime_trace = next(e for e in proj.agent_events if e["type"] == "runtime_trace")
    detail = str(runtime_trace["detail"])
    # SECRET must not appear
    assert "SECRET" not in detail, f"SECRET leaked in detail: {detail}"
    # "code" key value is safe and must appear (not digested)
    assert "bad_request" in detail, f"'bad_request' missing from detail: {detail}"
    # prompt key is digested (key-level digesting via _public_json_safe_preview_value)
    assert "Digest" in detail, f"promptDigest missing from detail: {detail}"


# ---------------------------------------------------------------------------
# Case 5: Long error message is capped with "..."
# ---------------------------------------------------------------------------


def test_long_error_message_is_capped() -> None:
    """A very long error message (>MAX_TOOL_PREVIEW) is truncated."""
    from magi_agent.shared.tool_preview import MAX_TOOL_PREVIEW

    # Use a message with no private keywords so we see the cap directly
    long_msg = "rate limit exceeded: " + ("x" * (MAX_TOOL_PREVIEW + 100))
    bridge = _bridge()
    event = _error_event(error_message=long_msg)
    proj = bridge.project_adk_event(event, turn_id="turn-5")

    runtime_trace = next(e for e in proj.agent_events if e["type"] == "runtime_trace")
    detail = str(runtime_trace["detail"])
    assert len(detail) <= MAX_TOOL_PREVIEW, (
        f"detail length {len(detail)} exceeds MAX_TOOL_PREVIEW {MAX_TOOL_PREVIEW}"
    )
    assert detail.endswith("..."), f"detail does not end with '...': {detail[-10:]!r}"


# ---------------------------------------------------------------------------
# Case 6: Benign finish signals do NOT produce terminal_abort / error events
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("benign", ["STOP", "completed", "stop_sequence", "end_turn"])
def test_benign_error_code_no_abort(benign: str) -> None:
    """A benign finish signal must not project terminal_abort."""
    bridge = _bridge()
    event = _error_event(error_code=benign)
    proj = bridge.project_adk_event(event, turn_id="turn-6")

    types_emitted = [e["type"] for e in proj.agent_events]
    assert "runtime_trace" not in types_emitted, (
        f"terminal_abort emitted for benign={benign!r}, events={types_emitted}"
    )
    assert "error" not in types_emitted, (
        f"error event emitted for benign={benign!r}, events={types_emitted}"
    )


# ---------------------------------------------------------------------------
# Case 7: Blast-radius pin for tool_start input_preview
# ---------------------------------------------------------------------------


def test_spawn_agent_prompt_still_digested_flag_off() -> None:
    """SpawnAgent with a top-level 'prompt' arg still digests that arg (flag off)."""
    env = {**os.environ, "MAGI_RICH_TOOL_PREVIEW": "0"}
    with mock.patch.dict(os.environ, env, clear=True):
        args = {"prompt": "Analyze Tesla 10-K", "persona": "bull"}
        preview = ea._public_preview(args, safe_keys=ea._rich_preview_safe_keys("SpawnAgent"))
    assert "Analyze Tesla 10-K" not in preview, (
        f"prompt value leaked in preview: {preview}"
    )
    assert "Digest" in preview, f"Digest missing from preview: {preview}"


def test_nested_prompt_digested_even_with_rich_flag_on() -> None:
    """Even with MAGI_RICH_TOOL_PREVIEW=1, a nested 'prompt' key is still digested."""
    env = {**os.environ, "MAGI_RICH_TOOL_PREVIEW": "1"}
    with mock.patch.dict(os.environ, env, clear=True):
        args = {"task": {"summary": "do it", "systemPrompt": "leak me"}}
        preview = ea._public_preview(args, safe_keys=ea._rich_preview_safe_keys("SpawnAgent"))
    assert "leak me" not in preview, f"systemPrompt value leaked: {preview}"
    assert "systemPromptDigest" in preview, f"systemPromptDigest missing: {preview}"


def test_is_private_preview_key_unchanged_for_prompt() -> None:
    """_is_private_preview_key('prompt') must still return True after the fix.

    This is the key blast-radius guard: the fragment list is byte-identical.
    """
    assert ea._is_private_preview_key("prompt") is True
    assert ea._is_private_preview_key("toolCall") is True
    assert ea._is_private_preview_key("functionCall") is True
    assert ea._is_private_preview_key("toolArgs") is True
    # Sanity: non-private keys must not match
    assert ea._is_private_preview_key("query") is False
    assert ea._is_private_preview_key("status") is False


# ---------------------------------------------------------------------------
# Case 8: Observability projector summary composition
# ---------------------------------------------------------------------------


def test_projector_error_summary_includes_code_and_message() -> None:
    """project_public_event for kind=error composes summary as 'code: message'."""
    payload: dict[str, Any] = {
        "type": "error",
        "code": "adk_error",
        "message": "Fireworks 429 rate limit",
    }
    ev = project_public_event(payload, session_id="s1", turn_id="t1")
    assert ev is not None
    assert ev.kind == "error"
    assert ev.summary is not None
    assert ev.summary == "adk_error: Fireworks 429 rate limit", (
        f"summary was: {ev.summary!r}"
    )


def test_projector_error_summary_code_only_when_no_message() -> None:
    """When only code is present (no message), summary is just the code."""
    payload: dict[str, Any] = {"type": "error", "code": "adk_error"}
    ev = project_public_event(payload, session_id="s1", turn_id="t1")
    assert ev is not None
    assert ev.summary == "adk_error", f"summary was: {ev.summary!r}"


def test_projector_error_summary_message_only_when_no_code() -> None:
    """When only message is present (no code), summary is just the message."""
    payload: dict[str, Any] = {"type": "error", "message": "something went wrong"}
    ev = project_public_event(payload, session_id="s1", turn_id="t1")
    assert ev is not None
    assert ev.summary == "something went wrong", f"summary was: {ev.summary!r}"


def test_projector_error_payload_contains_both_fields() -> None:
    """The stored payload dict includes both code and message fields."""
    payload: dict[str, Any] = {
        "type": "error",
        "code": "adk_error",
        "message": "Fireworks 429 rate limit",
    }
    ev = project_public_event(payload, session_id="s1", turn_id="t1")
    assert ev is not None
    assert ev.payload.get("code") == "adk_error"
    assert ev.payload.get("message") == "Fireworks 429 rate limit"


# ---------------------------------------------------------------------------
# Case 9: SSE second-layer sanitize_error_event behavior
# ---------------------------------------------------------------------------


def test_sse_sanitize_error_event_passes_plain_rate_limit_through() -> None:
    """SSE layer keeps a rate-limit error message (no private keywords) readable."""
    event_dict = {
        "type": "error",
        "code": "adk_error",
        "message": "litellm.RateLimitError: Fireworks 429 too many requests",
    }
    result = sse._sanitize_error_event(event_dict)
    assert result.get("code") == "adk_error"
    msg = result.get("message", "")
    assert isinstance(msg, str)
    # No whole digest
    assert not msg.startswith('{"digest"')
    # Rate limit text survives (no private markers)
    assert "RateLimitError" in msg or "rate" in msg.lower() or "429" in msg


def test_sse_sanitize_error_event_returns_str_not_digest() -> None:
    """SSE sanitize_error_event always returns string values, not digest objects."""
    event_dict = {
        "type": "error",
        "code": "adk_error",
        "message": "unexpected error occurred",
    }
    result = sse._sanitize_error_event(event_dict)
    assert isinstance(result.get("message"), str)
    assert not str(result.get("message", "")).startswith('{"digest"')
