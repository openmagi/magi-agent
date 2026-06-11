"""Self-test for the ControlPlaneRecorder decision-trace capture.

Verifies that the recorder content-addresses tool args (stable + secret-free)
and normalizes to a deterministic, order-preserving event list.
"""

from __future__ import annotations

from tests.fixtures.neutral_runtime_golden.recorder import (
    ControlPlaneRecorder,
    normalize_trace,
)


def _is_sha(value: object) -> bool:
    return isinstance(value, str) and value.startswith("sha256:") and len(value) > len(
        "sha256:"
    )


def test_recorder_captures_a_deny_then_normalizes_stable() -> None:
    rec = ControlPlaneRecorder()
    rec.record_before_tool(
        tool_name="FileEdit",
        tool_args={"path": "a.py"},
        decision={"action": "deny", "reason": "loop guard"},
    )
    rec.record_reinject(role="user", text_digest="sha256:deadbeef", source="max_steps")
    trace = normalize_trace(rec.events)

    # args are digested (not stored raw) so the golden is stable and secret-free.
    assert _is_sha(trace[0]["args_digest"])
    args_digest = trace[0]["args_digest"]
    assert trace == [
        {
            "kind": "before_tool",
            "tool": "FileEdit",
            "args_digest": args_digest,
            "decision": "deny",
            "reason": "loop guard",
        },
        {
            "kind": "reinject",
            "role": "user",
            "text_digest": "sha256:deadbeef",
            "source": "max_steps",
        },
    ]


def test_digest_is_deterministic_for_same_args() -> None:
    rec_a = ControlPlaneRecorder()
    rec_b = ControlPlaneRecorder()
    rec_a.record_before_tool(
        tool_name="Read", tool_args={"b": 2, "a": 1}, decision={"action": "allow"}
    )
    rec_b.record_before_tool(
        tool_name="Read", tool_args={"a": 1, "b": 2}, decision={"action": "allow"}
    )
    # sort_keys digest => key order does not change the digest.
    assert rec_a.events[0]["args_digest"] == rec_b.events[0]["args_digest"]


def test_after_tool_override_is_digested() -> None:
    rec = ControlPlaneRecorder()
    rec.record_after_tool(tool_name="Search", override={"status": "loop_guard_stop"})
    rec.record_after_tool(tool_name="Search", override=None)
    assert _is_sha(rec.events[0]["override"])
    assert rec.events[1]["override"] is None


def test_compaction_and_before_model_events() -> None:
    rec = ControlPlaneRecorder()
    rec.record_before_model(mutated=True, tools_cleared=False)
    rec.record_compaction(fired=True, kept_tail=16)
    trace = normalize_trace(rec.events)
    assert trace[0] == {"kind": "before_model", "mutated": True, "tools_cleared": False}
    assert trace[1] == {"kind": "compaction", "fired": True, "kept_tail": 16}
