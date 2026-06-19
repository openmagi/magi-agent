"""Unit tests for ``magi_agent.adk_bridge.wire_profile``.

Pure unit tests — no ADK imports, no network, no Supabase.

TDD steps:
1. Run → FAIL (module doesn't exist yet).
2. Implement wire_profile.py.
3. Run → PASS.
"""
from __future__ import annotations

import pytest

from magi_agent.adk_bridge.wire_profile import (
    DEFAULT_PROFILE,
    HOSTED_PROFILE,
    WireProfile,
)
from magi_agent.runtime.public_events import (
    tool_end_event,
    tool_event_id,
    tool_progress_event,
    tool_start_event,
    turn_phase_event,
)


# ---------------------------------------------------------------------------
# WireProfile structure
# ---------------------------------------------------------------------------


def test_wire_profile_is_frozen_dataclass() -> None:
    """WireProfile must be immutable (frozen=True)."""
    with pytest.raises((AttributeError, TypeError)):
        DEFAULT_PROFILE.tool_id = lambda *a: "x"  # type: ignore[method-assign]


def test_wire_profile_instances_have_all_builders() -> None:
    for profile in (DEFAULT_PROFILE, HOSTED_PROFILE):
        assert callable(profile.tool_id)
        assert callable(profile.build_tool_start)
        assert callable(profile.build_tool_progress)
        assert callable(profile.build_tool_end)
        assert callable(profile.build_text_delta)
        assert callable(profile.build_turn_phase)


# ---------------------------------------------------------------------------
# HOSTED_PROFILE — tool_id
# ---------------------------------------------------------------------------


def test_hosted_profile_tool_id_equals_public_events_tool_event_id() -> None:
    """HOSTED_PROFILE.tool_id must produce the same tu_<hash> as tool_event_id."""
    name = "Read"
    args = {"path": "x"}
    hosted_id = HOSTED_PROFILE.tool_id(name, args, None, 0)
    expected = tool_event_id(name=name, args=args, call_id=None, index=0)
    assert hosted_id == expected


def test_hosted_profile_tool_id_with_adk_id_equals_public_events() -> None:
    name = "Bash"
    args = {"command": "ls"}
    adk_id = "fc-abc123"
    hosted_id = HOSTED_PROFILE.tool_id(name, args, adk_id, 2)
    expected = tool_event_id(name=name, args=args, call_id=adk_id, index=2)
    assert hosted_id == expected


def test_hosted_profile_tool_id_starts_with_tu_prefix() -> None:
    result = HOSTED_PROFILE.tool_id("Search", {"query": "docs"}, None, 0)
    assert result.startswith("tu_")


def test_hosted_profile_tool_id_deterministic() -> None:
    """Same inputs → same id across two calls."""
    a = HOSTED_PROFILE.tool_id("Write", {"path": "/tmp/f"}, None, 1)
    b = HOSTED_PROFILE.tool_id("Write", {"path": "/tmp/f"}, None, 1)
    assert a == b


def test_hosted_profile_tool_id_changes_with_name() -> None:
    a = HOSTED_PROFILE.tool_id("Read", {}, None, 0)
    b = HOSTED_PROFILE.tool_id("Write", {}, None, 0)
    assert a != b


def test_hosted_profile_tool_id_changes_with_index() -> None:
    a = HOSTED_PROFILE.tool_id("Read", {}, None, 0)
    b = HOSTED_PROFILE.tool_id("Read", {}, None, 1)
    assert a != b


# ---------------------------------------------------------------------------
# HOSTED_PROFILE — tool_start builder
# ---------------------------------------------------------------------------


def test_hosted_profile_tool_start_matches_public_events_builder() -> None:
    """HOSTED_PROFILE.build_tool_start must produce same dict as tool_start_event."""
    tid = HOSTED_PROFILE.tool_id("Read", {"path": "x"}, None, 0)
    hosted_event = HOSTED_PROFILE.build_tool_start(tid, "Read", '{"path":"x"}')
    expected = tool_start_event(
        tool_id=tid,
        name="Read",
        input_preview='{"path":"x"}',
        event_family="tool_progress",
    )
    assert hosted_event == expected


def test_hosted_profile_tool_start_has_correct_type() -> None:
    tid = HOSTED_PROFILE.tool_id("Bash", {"command": "ls"}, None, 0)
    event = HOSTED_PROFILE.build_tool_start(tid, "Bash", None)
    assert event["type"] == "tool_start"


def test_hosted_profile_tool_start_none_preview_omitted() -> None:
    """When input_preview is None, public_events.tool_start_event omits the key."""
    tid = HOSTED_PROFILE.tool_id("Bash", {}, None, 0)
    event = HOSTED_PROFILE.build_tool_start(tid, "Bash", None)
    # public_events._put_text only adds key when value is truthy
    assert "input_preview" not in event


# ---------------------------------------------------------------------------
# HOSTED_PROFILE — tool_progress builder
# ---------------------------------------------------------------------------


def test_hosted_profile_tool_progress_matches_public_events_builder() -> None:
    tid = HOSTED_PROFILE.tool_id("Search", {"query": "q"}, None, 0)
    hosted_event = HOSTED_PROFILE.build_tool_progress(tid, "Searching…")
    expected = tool_progress_event(
        tool_id=tid,
        label="Searching…",
        event_family="tool_progress",
    )
    assert hosted_event == expected


def test_hosted_profile_tool_progress_has_correct_type() -> None:
    tid = HOSTED_PROFILE.tool_id("Fetch", {}, None, 0)
    event = HOSTED_PROFILE.build_tool_progress(tid, None)
    assert event["type"] == "tool_progress"


# ---------------------------------------------------------------------------
# HOSTED_PROFILE — tool_end builder
# ---------------------------------------------------------------------------


def test_hosted_profile_tool_end_matches_public_events_builder() -> None:
    tid = HOSTED_PROFILE.tool_id("Read", {"path": "x"}, None, 0)
    hosted_event = HOSTED_PROFILE.build_tool_end(tid, "ok", "file contents")
    expected = tool_end_event(
        tool_id=tid,
        status="ok",
        output_preview="file contents",
        event_family="tool_progress",
    )
    assert hosted_event == expected


def test_hosted_profile_tool_end_error_status() -> None:
    tid = HOSTED_PROFILE.tool_id("Bash", {}, None, 0)
    event = HOSTED_PROFILE.build_tool_end(tid, "error", "exit code 1")
    assert event["status"] == "error"
    assert event["type"] == "tool_end"


# ---------------------------------------------------------------------------
# HOSTED_PROFILE — text_delta + turn_phase builders
# ---------------------------------------------------------------------------


def test_hosted_profile_text_delta_shape() -> None:
    event = HOSTED_PROFILE.build_text_delta("hello world")
    assert event == {"type": "text_delta", "delta": "hello world"}


def test_hosted_profile_turn_phase_matches_public_events_builder() -> None:
    hosted_event = HOSTED_PROFILE.build_turn_phase("turn-1", "planning")
    expected = turn_phase_event(
        turn_id="turn-1",
        phase="planning",
        event_family="turn_lifecycle_public_stream",
    )
    assert hosted_event == expected


def test_hosted_profile_turn_phase_unknown_falls_back_to_pending() -> None:
    event = HOSTED_PROFILE.build_turn_phase("turn-2", "UNKNOWN_PHASE")
    assert event["phase"] == "pending"


# ---------------------------------------------------------------------------
# DEFAULT_PROFILE — tool_id scheme
# ---------------------------------------------------------------------------


def test_default_profile_tool_id_has_adk_tool_call_prefix_with_adk_id() -> None:
    """With adk_id present, DEFAULT scheme prefixes with adk-tool-call:."""
    result = DEFAULT_PROFILE.tool_id("Read", {"path": "x"}, "fc-adk-123", 0)
    assert result.startswith("adk-tool-call:")


def test_default_profile_tool_id_has_adk_tool_call_prefix_fallback() -> None:
    """Without adk_id, DEFAULT scheme produces adk-tool-call-<sha1hash>."""
    result = DEFAULT_PROFILE.tool_id("Read", {"path": "x"}, None, 0)
    assert result.startswith("adk-tool-call-")


def test_default_profile_tool_id_never_starts_with_tu() -> None:
    """DEFAULT profile must NOT produce tu_ ids (those are HOSTED-only)."""
    result = DEFAULT_PROFILE.tool_id("Search", {"query": "q"}, None, 0)
    assert not result.startswith("tu_")


def test_default_profile_tool_id_with_adk_id_embeds_it() -> None:
    """The adk_id must appear in the generated id."""
    adk_id = "fc-uniqueid-999"
    result = DEFAULT_PROFILE.tool_id("Bash", {}, adk_id, 0)
    assert adk_id in result


def test_default_profile_tool_id_fallback_deterministic() -> None:
    a = DEFAULT_PROFILE.tool_id("Write", {"path": "/tmp/f"}, None, 0)
    b = DEFAULT_PROFILE.tool_id("Write", {"path": "/tmp/f"}, None, 0)
    assert a == b


# ---------------------------------------------------------------------------
# DEFAULT_PROFILE — tool_start builder
# ---------------------------------------------------------------------------


def test_default_profile_tool_start_has_correct_type() -> None:
    tid = DEFAULT_PROFILE.tool_id("Read", {"path": "x"}, "fc-1", 0)
    event = DEFAULT_PROFILE.build_tool_start(tid, "Read", '{"path":"x"}')
    assert event["type"] == "tool_start"


def test_default_profile_tool_start_matches_event_adapter_shape() -> None:
    """DEFAULT tool_start must match the dict event_adapter builds today."""
    tool_use_id = "adk-tool-call:fc-test-id"
    event = DEFAULT_PROFILE.build_tool_start(tool_use_id, "Read", '{"path":"x"}')
    expected = {
        "type": "tool_start",
        "id": tool_use_id,
        "name": "Read",
        "input_preview": '{"path":"x"}',
    }
    assert event == expected


def test_default_profile_tool_start_empty_preview_as_empty_string() -> None:
    """event_adapter passes _public_preview(args) which always returns a str."""
    tid = DEFAULT_PROFILE.tool_id("Bash", {}, "fc-2", 0)
    event = DEFAULT_PROFILE.build_tool_start(tid, "Bash", "")
    assert event["input_preview"] == ""


# ---------------------------------------------------------------------------
# DEFAULT_PROFILE — tool_end builder
# ---------------------------------------------------------------------------


def test_default_profile_tool_end_has_duration_ms() -> None:
    """event_adapter always includes durationMs: 0."""
    tid = DEFAULT_PROFILE.tool_id("Read", {"path": "x"}, "fc-3", 0)
    event = DEFAULT_PROFILE.build_tool_end(tid, "ok", "result")
    assert event["durationMs"] == 0


def test_default_profile_tool_end_matches_event_adapter_shape() -> None:
    tool_use_id = "adk-tool-call:fc-test-id"
    event = DEFAULT_PROFILE.build_tool_end(tool_use_id, "ok", "some output")
    expected = {
        "type": "tool_end",
        "id": tool_use_id,
        "status": "ok",
        "output_preview": "some output",
        "durationMs": 0,
    }
    assert event == expected


# ---------------------------------------------------------------------------
# DEFAULT_PROFILE — text_delta + turn_phase builders
# ---------------------------------------------------------------------------


def test_default_profile_text_delta_shape() -> None:
    event = DEFAULT_PROFILE.build_text_delta("streamed text")
    assert event == {"type": "text_delta", "delta": "streamed text"}


def test_default_profile_turn_phase_shape() -> None:
    event = DEFAULT_PROFILE.build_turn_phase("turn-1", "executing")
    assert event == {"type": "turn_phase", "turnId": "turn-1", "phase": "executing"}


# ---------------------------------------------------------------------------
# Profile isolation — DEFAULT and HOSTED produce different ids for same inputs
# ---------------------------------------------------------------------------


def test_default_and_hosted_produce_different_ids() -> None:
    """The two profiles must produce different id schemes for the same call."""
    default_id = DEFAULT_PROFILE.tool_id("Read", {"path": "x"}, None, 0)
    hosted_id = HOSTED_PROFILE.tool_id("Read", {"path": "x"}, None, 0)
    assert default_id != hosted_id


def test_default_and_hosted_produce_different_tool_start_dicts() -> None:
    args = {"path": "x"}
    default_id = DEFAULT_PROFILE.tool_id("Read", args, None, 0)
    hosted_id = HOSTED_PROFILE.tool_id("Read", args, None, 0)
    # ids differ so the dicts must differ
    default_evt = DEFAULT_PROFILE.build_tool_start(default_id, "Read", '{"path":"x"}')
    hosted_evt = HOSTED_PROFILE.build_tool_start(hosted_id, "Read", '{"path":"x"}')
    assert default_evt["id"] != hosted_evt["id"]


# ---------------------------------------------------------------------------
# Task 1: build_tool_end receipt_refs + duration_ms forwarding
# ---------------------------------------------------------------------------


def test_hosted_profile_tool_end_forwards_receipt_refs_and_duration_ms() -> None:
    """HOSTED_PROFILE.build_tool_end must forward receipt_refs + duration_ms.

    The result must equal what public_events.tool_end_event produces with the
    same arguments — so transcriptRefs and durationMs are present in the dict.

    Uses a valid ref:… token (matches _PUBLIC_REF_RE) so _safe_refs keeps it
    and transcriptRefs appears in the output.
    """
    _valid_ref = "ref:result-sha256-abc"
    result = HOSTED_PROFILE.build_tool_end(
        "tu_x",
        "ok",
        "result:sha256:abc",
        receipt_refs=(_valid_ref,),
        duration_ms=12,
    )
    expected = tool_end_event(
        tool_id="tu_x",
        status="ok",
        output_preview="result:sha256:abc",
        receipt_refs=(_valid_ref,),
        duration_ms=12,
        event_family="tool_progress",
    )
    assert result == expected
    assert "transcriptRefs" in result
    assert result["transcriptRefs"] == [_valid_ref]
    assert result["durationMs"] == 12


def test_default_profile_tool_end_unchanged_with_new_kwargs() -> None:
    """DEFAULT_PROFILE.build_tool_end must accept new kwargs but ignore them.

    Its produced dict must be EXACTLY equal to the current snapshot — guards
    the CLI-doc profile against accidental mutation.
    """
    # Snapshot of current DEFAULT dict (must stay byte-identical).
    expected_snapshot = {
        "type": "tool_end",
        "id": "tu_x",
        "status": "ok",
        "output_preview": "p",
        "durationMs": 0,
    }
    result = DEFAULT_PROFILE.build_tool_end("tu_x", "ok", "p")
    assert result == expected_snapshot

    # New kwargs must be accepted without error and must NOT affect the output.
    result_with_kwargs = DEFAULT_PROFILE.build_tool_end(
        "tu_x",
        "ok",
        "p",
        receipt_refs=("result:sha256:abc",),
        duration_ms=99,
    )
    assert result_with_kwargs == expected_snapshot
