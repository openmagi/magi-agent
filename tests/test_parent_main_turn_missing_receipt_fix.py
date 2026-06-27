"""PR-J / Issue C: the parent main turn must not be persisted as
``aborted/missing_runtime_receipt`` when the local-serve runner produced a
fully successful turn (text + tools).

Kevin's 0.1.86 Gemini 3.5 Flash + Tesla 10-K repro:

* 58 ``text_delta`` events streamed normally (the full Korean summary,
  including the literal "총 매출액 948억..." with the correct numbers).
* 21 ``tool_end`` events fired normally (PersistentPython / Bash / BrowserTask).
* DB ``turn_end`` record: ``{"status": "aborted", "reason":
  "missing_runtime_receipt"}``.
* Dashboard rendered: "Work started, but no final answer text arrived.
  Please try again."

Root cause: the SSE sanitizer (``transport.sse._sanitize_turn_end_event``)
re-applied the hosted strict-receipt downgrade to the projection emitted by
``project_runner_end_event(expect_receipt=False)``. The local-serve projection
correctly emits ``status="committed"`` without a ``receiptRef`` (the local OSS
runner has no receipt infrastructure), but the sanitizer treated the
absent receipt as a protocol violation and rewrote the turn back to
``aborted/missing_runtime_receipt``. The streaming-chat wire reconciler patched
the SSE output, but the persisted DB record (read upstream of the reconciler)
still saw the downgraded shape.

This is distinct from the child-runner silent-empty failure mode tracked under
issues A/B: that one is a child turn that emits no text; this one is the OUTER
parent main turn whose finalize path emits an aborted turn_end despite a fully
successful turn.

Fix shape: Shape A (caller-side). ``project_runner_end_event`` now propagates
the ``expect_receipt=False`` decision as an explicit ``expectReceipt: False``
marker on the projected event. ``_sanitize_turn_end_event`` reads the marker
and skips the strict-receipt downgrade when present. The marker is internal
only; the sanitizer rebuilds its output from scratch so ``expectReceipt`` never
appears on the public wire. The hosted path leaves the marker absent so the
strict-receipt safety net is preserved byte-identically.
"""

from __future__ import annotations

from magi_agent.adk_bridge.event_adapter import project_runner_end_event
from magi_agent.transport.sse import InMemorySseWriter, _sanitize_agent_event

import json


def _data_payloads(sse_body: str) -> list[dict[str, object]]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in sse_body.splitlines()
        if line.startswith("data: ") and line != "data: [DONE]"
    ]


def test_local_serve_successful_turn_emits_committed_turn_end() -> None:
    """Reproduces Kevin's 0.1.86 shape: a local-serve projection of a
    successful turn (committed, no receiptRef, expect_receipt=False) must
    survive the SSE sanitizer with ``status="committed"`` — not be silently
    rewritten back to ``aborted/missing_runtime_receipt``."""
    # Step 1: the local-serve projection layer attests committed without a
    # receipt (the local OSS runner has no receipt infrastructure).
    projection = project_runner_end_event(
        turn_id="turn-tesla-10k",
        status="committed",
        stop_reason="end_turn",
        expect_receipt=False,
    )
    [projected_event] = projection.agent_events
    assert projected_event["type"] == "turn_end"
    assert projected_event["status"] == "committed"
    assert projected_event.get("receiptRef") is None
    assert projected_event.get("reason") is None
    # The projection carries the explicit local-serve marker.
    assert projected_event.get("expectReceipt") is False

    # Step 2: round-trip through the SSE sanitizer (the path that previously
    # re-downgraded committed-without-receipt to aborted/missing_runtime_receipt).
    sanitized = _sanitize_agent_event(dict(projected_event))
    assert sanitized is not None
    assert sanitized["type"] == "turn_end"
    assert sanitized["status"] == "committed", (
        f"expected committed (local-serve invariant), got {sanitized!r}"
    )
    assert sanitized.get("reason") != "missing_runtime_receipt"
    # No bogus receiptRef synthesized when none was supplied.
    assert "receiptRef" not in sanitized
    # The internal marker must NOT leak onto the public wire.
    assert "expectReceipt" not in sanitized

    # Step 3: full SSE writer round-trip (what the chat-proxy actually reads).
    writer = InMemorySseWriter()
    writer.agent(dict(projected_event))
    [payload] = _data_payloads(writer.body)
    assert payload["type"] == "turn_end"
    assert payload["status"] == "committed"
    assert payload.get("reason") != "missing_runtime_receipt"


def test_hosted_path_still_downgrades_when_receipt_missing() -> None:
    """Hosted strict-receipt invariant: a committed-without-receipt turn_end
    WITHOUT the ``expectReceipt: False`` marker must still downgrade to
    ``aborted/missing_runtime_receipt`` on the SSE wire. Without this safety
    net a buggy hosted code path could silently emit half-baked turns."""
    writer = InMemorySseWriter()
    writer.agent(
        {
            "type": "turn_end",
            "turnId": "turn-hosted",
            "status": "committed",
            # no receiptRef and no expectReceipt marker - hosted protocol violation
        }
    )
    [payload] = _data_payloads(writer.body)
    assert payload["status"] == "aborted"
    assert payload.get("reason") == "missing_runtime_receipt"


def test_no_regression_on_real_abort_keeps_original_reason() -> None:
    """A genuinely aborted turn (e.g. cancelled) on the local-serve path must
    stay aborted with its real reason. The local-serve carve-out must not
    silently rewrite genuine aborts to a missing-receipt label."""
    projection = project_runner_end_event(
        turn_id="turn-cancelled",
        status="aborted",
        reason="cancelled",
        expect_receipt=False,
    )
    [projected_event] = projection.agent_events
    assert projected_event["status"] == "aborted"
    assert projected_event.get("reason") == "cancelled"

    sanitized = _sanitize_agent_event(dict(projected_event))
    assert sanitized is not None
    assert sanitized["status"] == "aborted"
    assert sanitized.get("reason") == "cancelled", (
        f"real abort must keep its reason, got {sanitized!r}"
    )
    # Genuine aborts are NOT mislabeled as missing_runtime_receipt.
    assert sanitized.get("reason") != "missing_runtime_receipt"


def test_local_serve_marker_does_not_smuggle_in_extra_fields_on_committed() -> None:
    """Defense-in-depth: a hand-crafted committed turn_end carrying
    ``expectReceipt: False`` AND a usage block does not result in
    ``receiptRef: null`` or other surprising shape changes."""
    writer = InMemorySseWriter()
    writer.agent(
        {
            "type": "turn_end",
            "turnId": "turn-local",
            "status": "committed",
            "stopReason": "end_turn",
            "expectReceipt": False,
            "usage": {"inputTokens": 100, "outputTokens": 50, "costUsd": 0.01},
        }
    )
    [payload] = _data_payloads(writer.body)
    assert payload["type"] == "turn_end"
    assert payload["status"] == "committed"
    assert payload.get("reason") != "missing_runtime_receipt"
    # ``receiptRef`` must NOT appear with a null/None value just because the
    # local-serve carve-out fired; it must be omitted entirely when absent.
    assert "receiptRef" not in payload
    # The internal marker must NOT be propagated to the public wire.
    assert "expectReceipt" not in payload
    # Usage survives sanitization on a committed turn.
    assert payload.get("usage") == {
        "inputTokens": 100,
        "outputTokens": 50,
        "costUsd": 0.01,
    }
