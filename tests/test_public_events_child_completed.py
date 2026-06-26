"""Tests for ``child_completed_event`` summary preview.

PR3/3 of the subagent-rich-emission series.

WHY
---
After PR1 (chip label) + PR2 (live tool progress), the chip still went blank
when a subagent finished — the user could see the agent doing things, but not
what it ultimately CAME BACK WITH.  The legacy TS runtime surfaced the
child's answer as a short preview in the chip detail; the Python runtime did
not.

The child's ``envelope.summary`` is the natural surface for this — it's the
sanitised string the boundary already produces and that the parent LLM
consumes via the tool result.  Forwarding it (truncated + redacted) to the
public ``child_completed`` event lets the UI render the same hint without
disclosing anything the parent agent doesn't already see.

CONTRACT
--------
- ``summary`` is OPTIONAL on the event.  When absent the event shape is
  byte-identical to today.
- The same ``_public_text`` sanitisation + length cap is applied.
- A symmetrical ``summary`` field is offered for ``child_failed`` /
  ``child_cancelled`` so the chip can show "failed at X" instead of going
  back to a generic placeholder.
"""
from __future__ import annotations

from magi_agent.runtime.public_events import (
    child_cancelled_event,
    child_completed_event,
    child_failed_event,
)


# ---------------------------------------------------------------------------
# child_completed
# ---------------------------------------------------------------------------


def test_completed_event_carries_task_and_receipt_ref() -> None:
    evt = child_completed_event(
        task_id="task-1",
        child_receipt_ref="receipt:sha256:abc",
    )
    assert evt["type"] == "child_completed"
    assert evt["taskId"] == "task-1"
    assert evt["childReceiptRef"] == "receipt:sha256:abc"


def test_completed_event_omits_summary_when_none() -> None:
    evt = child_completed_event(
        task_id="task-1",
        child_receipt_ref="receipt:sha256:abc",
    )
    assert "summary" not in evt


def test_completed_event_carries_summary_when_provided() -> None:
    evt = child_completed_event(
        task_id="task-1",
        child_receipt_ref="receipt:sha256:abc",
        summary="Found three relevant SEC filings.",
    )
    assert evt["summary"] == "Found three relevant SEC filings."


def test_completed_event_truncates_oversize_summary() -> None:
    big = "x" * 5000
    evt = child_completed_event(
        task_id="task-1",
        child_receipt_ref="receipt:sha256:abc",
        summary=big,
    )
    assert isinstance(evt["summary"], str)
    # The shared ``_public_text`` cap keeps the preview readable.
    assert len(evt["summary"]) <= 320


# ---------------------------------------------------------------------------
# child_failed
# ---------------------------------------------------------------------------


def test_failed_event_carries_error_message() -> None:
    evt = child_failed_event(
        task_id="task-1",
        child_receipt_ref="receipt:sha256:abc",
        error_message="child_runner_error",
    )
    assert evt["type"] == "child_failed"
    assert evt["errorMessage"] == "child_runner_error"


def test_failed_event_carries_summary_when_provided() -> None:
    evt = child_failed_event(
        task_id="task-1",
        child_receipt_ref="receipt:sha256:abc",
        error_message="child_llm_empty_response",
        summary="Partial: only gathered 2 of 5 sources before timing out.",
    )
    assert "summary" in evt
    assert "Partial" in evt["summary"]


# ---------------------------------------------------------------------------
# child_cancelled
# ---------------------------------------------------------------------------


def test_cancelled_event_carries_reason() -> None:
    evt = child_cancelled_event(
        task_id="task-1",
        child_receipt_ref="receipt:sha256:abc",
        reason="child_runner_blocked",
    )
    assert evt["type"] == "child_cancelled"
    assert evt["reason"] == "child_runner_blocked"


def test_cancelled_event_carries_summary_when_provided() -> None:
    evt = child_cancelled_event(
        task_id="task-1",
        child_receipt_ref="receipt:sha256:abc",
        reason="child_spawn_depth_exceeded",
        summary="Did not run — depth cap.",
    )
    assert evt["summary"] == "Did not run — depth cap."
