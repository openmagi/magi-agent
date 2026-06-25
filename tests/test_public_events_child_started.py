"""Tests for ``child_started_event`` enriched fields.

Why these matter
----------------
The local-dashboard Work pane uses the AGENTS section to show a chip per
spawned subagent.  Before this enrichment, ``child_started`` only carried
``taskId``/``parentTurnId``/``childReceiptRef`` plus the fixed string
``"Delegated child started"``.  That left chips with no model, no human-
meaningful task label, and only an index-derived placeholder name.

This builder adds three opt-in fields that the UI consumes:

- ``agentName``  — deterministic human label (Halley/Meitner/…).
- ``model``      — ``"<provider>:<model>"`` when the parent passed both.
- ``taskTitle``  — short, public-safe brief the LLM provides via SpawnAgent
                   args (NOT the prompt body — privacy contract preserved).
"""
from __future__ import annotations

from magi_agent.runtime.public_events import child_started_event


def test_event_carries_task_id_and_parent_turn_id() -> None:
    evt = child_started_event(
        task_id="task-1",
        parent_turn_id="turn-1",
        child_receipt_ref="receipt:sha256:abc",
    )
    assert evt["type"] == "child_started"
    assert evt["taskId"] == "task-1"
    assert evt["parentTurnId"] == "turn-1"
    assert evt["childReceiptRef"] == "receipt:sha256:abc"


def test_event_default_detail_string() -> None:
    evt = child_started_event(
        task_id="task-1",
        parent_turn_id="turn-1",
        child_receipt_ref="receipt:sha256:abc",
    )
    assert evt["detail"] == "Delegated child started"


def test_event_includes_agent_name_when_provided() -> None:
    evt = child_started_event(
        task_id="task-1",
        parent_turn_id="turn-1",
        child_receipt_ref="receipt:sha256:abc",
        agent_name="Halley",
    )
    assert evt["agentName"] == "Halley"


def test_event_includes_model_when_provided() -> None:
    evt = child_started_event(
        task_id="task-1",
        parent_turn_id="turn-1",
        child_receipt_ref="receipt:sha256:abc",
        model="anthropic:claude-opus-4-8",
    )
    assert evt["model"] == "anthropic:claude-opus-4-8"


def test_event_includes_task_title_when_provided() -> None:
    evt = child_started_event(
        task_id="task-1",
        parent_turn_id="turn-1",
        child_receipt_ref="receipt:sha256:abc",
        task_title="Cross-validate 1+1 across 3 SOTA models",
    )
    assert evt["taskTitle"] == "Cross-validate 1+1 across 3 SOTA models"


def test_event_omits_optional_fields_when_none() -> None:
    evt = child_started_event(
        task_id="task-1",
        parent_turn_id="turn-1",
        child_receipt_ref="receipt:sha256:abc",
    )
    assert "agentName" not in evt
    assert "model" not in evt
    assert "taskTitle" not in evt


def test_event_truncates_task_title_to_safe_length() -> None:
    long_title = "x" * 500
    evt = child_started_event(
        task_id="task-1",
        parent_turn_id="turn-1",
        child_receipt_ref="receipt:sha256:abc",
        task_title=long_title,
    )
    # The same redaction/length cap used elsewhere keeps the chip readable.
    assert isinstance(evt["taskTitle"], str)
    assert len(evt["taskTitle"]) <= 240
