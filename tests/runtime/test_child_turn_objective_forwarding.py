"""Track 17 PR1 — the real child turn must forward the parent's actual objective.

`_run_real_child` previously sent a hardcoded ``Part(text="child-turn")`` skeleton
(objective forwarding "deferred to a later Track 17 PR"). Without the real
objective the child cannot do the delegated work. These cover the pure helpers
that build the child's user-message text and its typed context refs from the
``ChildTaskRequest`` — kept pure (no google.genai import) so the boundary's
module-import-isolation contract holds and so they are unit-testable.
"""
from __future__ import annotations

from magi_agent.runtime.child_runner_boundary import (
    ChildTaskRequest,
    _child_turn_context_refs,
    _child_turn_message_text,
)


def _req(**over: object) -> ChildTaskRequest:
    base: dict[str, object] = {
        "parentExecutionId": "parent-1",
        "turnId": "turn-1",
        "taskId": "task-1",
        "objective": "Write the bear case for NAEOE DISTILLERY.",
    }
    base.update(over)
    return ChildTaskRequest(**base)


def test_message_text_forwards_objective_verbatim():
    text = _child_turn_message_text(_req())
    assert "Write the bear case for NAEOE DISTILLERY." in text
    # No longer the redaction-safe skeleton.
    assert text.strip() != "child-turn"


def test_message_text_frames_non_general_role():
    text = _child_turn_message_text(_req(role="research"))
    assert "research" in text.lower()
    assert "Write the bear case for NAEOE DISTILLERY." in text


def test_message_text_is_str_and_nonempty():
    text = _child_turn_message_text(_req())
    assert isinstance(text, str) and text.strip()


def test_context_refs_empty_when_no_metadata():
    input_refs, evidence_refs, ctx = _child_turn_context_refs(_req())
    assert input_refs == ()
    assert evidence_refs == ()
    assert ctx is None


def test_context_refs_extracted_from_metadata():
    req = _req(
        metadata={
            "inputRefs": ["artifact:a", "artifact:b"],
            "evidenceRefs": ["claim:c"],
            "contextPlanDigest": "sha256:deadbeef",
        }
    )
    input_refs, evidence_refs, ctx = _child_turn_context_refs(req)
    assert input_refs == ("artifact:a", "artifact:b")
    assert evidence_refs == ("claim:c",)
    assert ctx == "sha256:deadbeef"


def test_context_refs_filters_non_string_and_empty():
    req = _req(metadata={"inputRefs": ["ok", "", 5, None, "  ", "ok2"]})
    input_refs, _evidence_refs, _ctx = _child_turn_context_refs(req)
    assert input_refs == ("ok", "ok2")
