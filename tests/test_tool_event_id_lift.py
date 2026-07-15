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


def test_tool_event_id_byte_identity_with_wire_format() -> None:
    """Lifted fn must produce exactly the id the retired gate5b4c3 fn produced.

    The retired boundary's ``_manual_tool_event_id`` already delegated to this
    shared ``tool_event_id`` (P5-M1b removed the thin wrapper), so this locks
    the wire-shape against a recomputed reference of the exact ``tu_<12-hex>``
    format the hosted wire consumers depend on rather than the deleted symbol.
    """
    import hashlib
    import json

    from magi_agent.runtime.public_events import tool_event_id  # type: ignore[attr-defined]

    def _reference_tool_event_id(
        *, name: str, args: dict, call_id: object, index: int
    ) -> str:
        # Mirrors the retired gate5b4c3 id scheme byte-for-byte:
        # tu_ + first-12-hex of sha256(canonical-json of the bounded struct).
        def _json_dumps(value: object) -> str:
            return json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                default=repr,
            )

        def _digest(value: object) -> str:
            return "sha256:" + hashlib.sha256(_json_dumps(value).encode("utf-8")).hexdigest()

        def _bounded(value: object, *, max_bytes: int) -> object:
            if len(_json_dumps(value).encode("utf-8")) <= max_bytes:
                return value
            return {"truncated": True, "digest": _digest(value)}

        return "tu_" + _digest(
            {
                "name": name,
                "args": _bounded(args, max_bytes=512),
                "id": str(call_id or ""),
                "index": index,
            }
        )[7:19]

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
        reference = _reference_tool_event_id(**kwargs)
        assert lifted == reference, (
            f"byte-identity FAILED for {kwargs!r}: lifted={lifted!r}, reference={reference!r}"
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
