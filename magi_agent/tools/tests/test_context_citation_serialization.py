"""Fix 3: the ToolContext citation live-object fields serialize as null.

``serialize_citation_registry`` returns ``None``, so ``model_dump`` emits
``citationRegistry: null`` (the key is RETAINED with a null value, not dropped).
The point is honesty about the shape plus the guarantee that the live registry /
sink objects NEVER leak into serialization. ``citation_evidence_sink`` (Fix 2)
gets the same posture.
"""
from __future__ import annotations

import json

from magi_agent.tools.context import ToolContext


class _LiveRegistry:
    """Stand-in for a live SessionSourceRegistry (not JSON-serializable)."""

    def snapshot(self) -> tuple:
        return ()


def _live_sink(turn_id: str, record: object) -> None:  # pragma: no cover - identity
    return None


def test_citation_fields_serialize_as_null_and_never_leak_live_objects() -> None:
    registry = _LiveRegistry()
    ctx = ToolContext(
        bot_id="b",
        citation_registry=registry,
        citation_evidence_sink=_live_sink,
    )

    dumped = ctx.model_dump(by_alias=True)

    # The keys are RETAINED with null values (serialize_* returns None), and the
    # live objects never appear in the dump.
    assert "citationRegistry" in dumped
    assert dumped["citationRegistry"] is None
    assert "citationEvidenceSink" in dumped
    assert dumped["citationEvidenceSink"] is None
    assert registry not in dumped.values()
    assert _live_sink not in dumped.values()


def test_serialized_context_is_json_safe() -> None:
    ctx = ToolContext(
        bot_id="b",
        citation_registry=_LiveRegistry(),
        citation_evidence_sink=_live_sink,
    )
    # model_dump_json must not choke on the live objects (they serialize as null).
    payload = json.loads(ctx.model_dump_json(by_alias=True))
    assert payload["citationRegistry"] is None
    assert payload["citationEvidenceSink"] is None
