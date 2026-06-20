"""TDD: a committed turn from the LOCAL OSS runner must not be downgraded to
``aborted/missing_runtime_receipt``.

The local OSS engine has no runtime-receipt infrastructure (receipt-refs are a
hosted concept). When ``_project_content_parts`` reaches an ADK final-response
event on the live-compatible (local CLI / dashboard) path it calls
``project_runner_end_event(status="committed", stop_reason=...)`` WITHOUT a
``receipt_ref`` because there is none to supply. The projector then forces the
status down to ``aborted`` with reason ``missing_runtime_receipt``.

A transport-layer reconciler (``streaming_chat._reconcile_missing_receipt_turn_end``)
flips that back to ``committed`` for the SSE wire — but the downgrade still
propagates into observability and any downstream consumer that reads the raw
projection. The observability database then records every local turn as
``aborted`` even when the user got a perfectly normal reply, and any code path
that inspects the projected turn_end (vs. the transport-reconciled SSE frame)
treats the turn as a failure.

The fix exposes an explicit ``expect_receipt`` switch so the local emitter can
say "I know there is no receipt, do not downgrade". The default is still
``True`` to preserve hosted behavior.
"""

from __future__ import annotations

from magi_agent.adk_bridge.event_adapter import project_runner_end_event


def test_committed_without_receipt_stays_committed_when_receipt_not_expected() -> None:
    projection = project_runner_end_event(
        turn_id="turn-local",
        status="committed",
        stop_reason="end_turn",
        expect_receipt=False,
    )
    [event] = projection.agent_events
    assert event["type"] == "turn_end"
    assert event["status"] == "committed"
    assert event.get("stopReason") == "end_turn"
    assert "reason" not in event
    # No receipt is fine on the local path; do not synthesize one.
    assert event.get("receiptRef") is None


def test_committed_without_receipt_still_downgrades_by_default_for_hosted() -> None:
    # Default: hosted contract is unchanged — a committed turn without a
    # receipt is the protocol violation it has always been.
    projection = project_runner_end_event(
        turn_id="turn-hosted",
        status="committed",
        stop_reason="end_turn",
    )
    [event] = projection.agent_events
    assert event["status"] == "aborted"
    assert event.get("reason") == "missing_runtime_receipt"


def test_aborted_status_still_passes_through_when_not_expecting_receipt() -> None:
    # A genuinely aborted turn (the runner itself reported aborted) remains
    # aborted regardless of expect_receipt. The reason text is normalized by
    # the public stop_reason redactor; what matters is that the missing-receipt
    # downgrade does NOT silently turn this into "missing_runtime_receipt".
    projection = project_runner_end_event(
        turn_id="turn-local",
        status="aborted",
        reason="user_cancelled",
        expect_receipt=False,
    )
    [event] = projection.agent_events
    assert event["status"] == "aborted"
    assert event.get("reason") != "missing_runtime_receipt"


def test_committed_with_receipt_stays_committed_in_either_mode() -> None:
    # Real receipts make the question moot.
    ref = "receipt:sha256:" + ("a" * 64)
    for expect in (True, False):
        projection = project_runner_end_event(
            turn_id="turn-hosted",
            status="committed",
            stop_reason="end_turn",
            receipt_ref=ref,
            expect_receipt=expect,
        )
        [event] = projection.agent_events
        assert event["status"] == "committed", expect
        assert event.get("receiptRef") == ref, expect
