"""Task 1: DRY-lift tu_<hash> tool-event id into runtime/public_events.

TDD RED/GREEN test.  The byte-identity assertion (assert lifted == gate5b4c3)
locks that the lift does not change gate5b4c3 output.
"""

from __future__ import annotations

import re


# ---------------------------------------------------------------------------
# RED gate: tool_event_id must not exist yet when this test is first written.
# Once the implementation lands it becomes GREEN.
# ---------------------------------------------------------------------------

def test_tool_event_id_prefix_and_length() -> None:
    """Lifted fn returns a tu_<12-hex-chars> string."""
    from magi_agent.runtime.public_events import tool_event_id  # type: ignore[attr-defined]

    result = tool_event_id(name="Read", args={"path": "x"}, call_id="c1", index=0)
    assert result.startswith("tu_"), f"expected tu_ prefix, got {result!r}"
    suffix = result[3:]
    assert re.fullmatch(r"[0-9a-f]{12}", suffix), (
        f"expected 12 lowercase hex chars after tu_, got {suffix!r}"
    )


def test_tool_event_id_byte_identity_with_gate5b4c3() -> None:
    """Lifted fn must produce exactly the same string as gate5b4c3's private fn."""
    from magi_agent.runtime.public_events import tool_event_id  # type: ignore[attr-defined]
    from magi_agent.shadow.gate5b4c3_live_runner_boundary import (  # type: ignore[attr-defined]
        _manual_tool_event_id,
    )

    cases = [
        dict(name="Read", args={"path": "x"}, call_id="c1", index=0),
        dict(name="Write", args={"path": "/tmp/f", "content": "hello"}, call_id="c2", index=1),
        dict(name="Bash", args={"command": "ls -la"}, call_id=None, index=0),
        dict(name="SpawnAgent", args={}, call_id="", index=3),
        # Large args (triggers _bounded_json_value truncation)
        dict(name="Tool", args={"data": "x" * 2000}, call_id="big", index=0),
    ]
    for kwargs in cases:
        lifted = tool_event_id(**kwargs)
        original = _manual_tool_event_id(**kwargs)
        assert lifted == original, (
            f"byte-identity FAILED for {kwargs!r}: lifted={lifted!r}, original={original!r}"
        )


def test_tool_event_id_deterministic() -> None:
    """Same inputs always produce the same id."""
    from magi_agent.runtime.public_events import tool_event_id  # type: ignore[attr-defined]

    a = tool_event_id(name="Read", args={"path": "x"}, call_id="c1", index=0)
    b = tool_event_id(name="Read", args={"path": "x"}, call_id="c1", index=0)
    assert a == b


def test_tool_event_id_differs_on_different_inputs() -> None:
    """Different inputs produce different ids."""
    from magi_agent.runtime.public_events import tool_event_id  # type: ignore[attr-defined]

    a = tool_event_id(name="Read", args={"path": "x"}, call_id="c1", index=0)
    b = tool_event_id(name="Write", args={"path": "x"}, call_id="c1", index=0)
    assert a != b
