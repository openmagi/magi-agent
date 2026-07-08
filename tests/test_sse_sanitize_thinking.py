"""Tests for thinking_delta redaction in magi_agent.transport.sse.

Covers the MAGI_STREAM_THINKING feature flag:
  - OFF (default) → strip to None (backwards compat)
  - ON → redact-and-pass, matching text_delta redaction exactly
"""
from __future__ import annotations

from magi_agent.transport import sse


def test_thinking_stripped_under_safe_profile(monkeypatch):
    # Promoted to profile-aware default-ON: an unset flag strips only under a
    # safe runtime profile (the full profile now surfaces thinking).
    monkeypatch.delenv("MAGI_STREAM_THINKING", raising=False)
    monkeypatch.setenv("MAGI_RUNTIME_PROFILE", "safe")
    assert sse._sanitize_agent_event({"type": "thinking_delta", "delta": "plan"}) is None


def test_thinking_redacted_when_flag_on(monkeypatch):
    monkeypatch.setenv("MAGI_STREAM_THINKING", "1")
    out = sse._sanitize_agent_event({"type": "thinking_delta", "delta": "just reasoning"})
    assert out is not None and out["type"] == "thinking_delta"
    assert "delta" in out


def test_thinking_redaction_matches_text_delta(monkeypatch):
    monkeypatch.setenv("MAGI_STREAM_THINKING", "1")
    sample = "reasoning about /home/ocuser/.openclaw/secret and the plan"
    thinking = sse._sanitize_agent_event({"type": "thinking_delta", "delta": sample})
    text = sse._sanitize_agent_event({"type": "text_delta", "delta": sample})
    assert thinking is not None
    assert thinking["delta"] == text["delta"]  # identical redaction to visible text


def test_thinking_private_marker_redacted(monkeypatch):
    monkeypatch.setenv("MAGI_STREAM_THINKING", "1")
    # "chain of thought" triggers _has_private_text_marker (matches _PRIVATE_TEXT_RE)
    private_input = "chain of thought: the user wants X"
    out = sse._sanitize_agent_event({"type": "thinking_delta", "delta": private_input})
    assert out is not None
    assert out["delta"] == "[redacted-private]"


def test_thinking_flag_case_insensitive_true(monkeypatch):
    monkeypatch.setenv("MAGI_STREAM_THINKING", "True")
    sensitive = "reasoning about /home/ocuser/.openclaw/secret"
    out = sse._sanitize_agent_event({"type": "thinking_delta", "delta": sensitive})
    assert out is not None and out["type"] == "thinking_delta"
    assert "delta" in out
    assert out["delta"] != sensitive


def test_thinking_flag_yes(monkeypatch):
    monkeypatch.setenv("MAGI_STREAM_THINKING", "yes")
    sensitive = "reasoning about /home/ocuser/.openclaw/secret"
    out = sse._sanitize_agent_event({"type": "thinking_delta", "delta": sensitive})
    assert out is not None and out["type"] == "thinking_delta"
    assert "delta" in out
    assert out["delta"] != sensitive


def test_thinking_flag_on(monkeypatch):
    monkeypatch.setenv("MAGI_STREAM_THINKING", "on")
    sensitive = "reasoning about /home/ocuser/.openclaw/secret"
    out = sse._sanitize_agent_event({"type": "thinking_delta", "delta": sensitive})
    assert out is not None and out["type"] == "thinking_delta"
    assert "delta" in out
    assert out["delta"] != sensitive


def test_thinking_flag_empty_string_strips(monkeypatch):
    monkeypatch.setenv("MAGI_STREAM_THINKING", "")
    assert sse._sanitize_agent_event({"type": "thinking_delta", "delta": "plan"}) is None


def test_thinking_flag_zero_strips(monkeypatch):
    monkeypatch.setenv("MAGI_STREAM_THINKING", "0")
    assert sse._sanitize_agent_event({"type": "thinking_delta", "delta": "plan"}) is None


def test_thinking_no_delta_key_returns_type_only(monkeypatch):
    """When the delta field is missing, return {"type": "thinking_delta"} with no delta key."""
    monkeypatch.setenv("MAGI_STREAM_THINKING", "1")
    out = sse._sanitize_agent_event({"type": "thinking_delta"})
    assert out is not None
    assert out["type"] == "thinking_delta"
    assert "delta" not in out


def test_thinking_falls_back_to_text_key(monkeypatch):
    """When delta is absent but text is present, use the text value."""
    monkeypatch.setenv("MAGI_STREAM_THINKING", "1")
    out = sse._sanitize_agent_event({"type": "thinking_delta", "text": "fallback content"})
    assert out is not None
    assert "delta" in out


def test_thinking_non_string_delta_returns_type_only(monkeypatch):
    """Non-string delta (e.g. int) is not a valid string value; return type-only dict."""
    monkeypatch.setenv("MAGI_STREAM_THINKING", "1")
    out = sse._sanitize_agent_event({"type": "thinking_delta", "delta": 42})
    assert out is not None
    assert out["type"] == "thinking_delta"
    assert "delta" not in out


def test_citation_turn_phase_survives_wire_with_status(monkeypatch):
    """GAP #4 wire contract: the driver's citation repair turn_phase frame
    (phase=verifying + citation status + eventId) survives the public SSE
    allowlist with its status field intact so the client can label the
    mid-turn intervention."""
    out = sse._sanitize_agent_event(
        {
            "type": "turn_phase",
            "turnId": "turn_x",
            "phase": "verifying",
            "status": "citation_attribution",
            "eventId": "citation-repair-turn_x-1",
        }
    )
    assert out is not None
    assert out["type"] == "turn_phase"
    assert out["phase"] == "verifying"
    assert out["status"] == "citation_attribution"
    assert out["eventId"] == "citation-repair-turn_x-1"


def test_turn_phase_status_dropped_without_event_id(monkeypatch):
    """Without an eventId the status field is dropped (allowlist invariant), so
    the citation frame MUST carry an eventId to be usable downstream."""
    out = sse._sanitize_agent_event(
        {
            "type": "turn_phase",
            "turnId": "turn_x",
            "phase": "verifying",
            "status": "citation_attribution",
        }
    )
    assert out is not None
    assert "status" not in out
