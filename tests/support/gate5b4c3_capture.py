"""Capture helper for gate5b4c3 boundary tests (Task 2).

``capture_boundary`` drives the gate5b4c3 serving boundary through the
fake-ADK harness and returns a JSON-safe snapshot of all three observable
output streams:

  * ``public_events``      — payloads emitted via ``public_event_sink``
  * ``transcript_records`` — records written to the process-global transcript
                             sink (requires MAGI_SESSION_TRANSCRIPT_ENABLED=1
                             to be non-empty, as the boundary is fail-open)
  * ``result``             — public-safe scalar fields off the boundary result
                             (status / reason / event_count / selected_provider
                             / selected_model)

The helper is intentionally narrow: it does NOT expose raw output text or usage
internals so that callers cannot accidentally snapshot private data in golden
files.
"""
from __future__ import annotations

from typing import Any

from magi_agent.observability.transcript import set_active_transcript_sink
from magi_agent.shadow.gate5b4c3_live_runner_boundary import (
    run_gate5b4c3_live_runner_boundary_async,
)
from tests.support.gate5b4c3_fakes import make_primitives

# Public-safe result fields to include in the snapshot.  ``getattr(…, f, None)``
# is used below so a missing attribute yields None rather than AttributeError,
# keeping the helper forward-compatible with future result model changes.
_RESULT_PUBLIC_FIELDS = (
    "status",
    "reason",
    "event_count",
    "selected_provider",
    "selected_model",
)


async def capture_boundary(
    request: Any,
    runner: Any,
    *,
    config: Any = None,
    adk_tools: tuple = (),
) -> dict:
    """Drive the gate5b4c3 boundary with a fake runner and return a snapshot.

    Parameters
    ----------
    request:
        A ``Gate5B4C3ShadowGenerationRequest`` instance (typically from
        ``tests.test_gate5b4c3_live_runner_boundary._request()``).
    runner:
        A ``_FakeRunner`` instance (or compatible) pre-loaded with events.
    config:
        Optional ``Gate5B4C3ShadowGenerationConfig``.  Pass
        ``_enabled_config()`` from the boundary test module to get a
        ``"completed"`` result; ``None`` (default) causes the boundary to
        short-circuit with ``status="skipped"``.
    adk_tools:
        Optional sequence of ADK tool objects to pass through to the boundary.

    Returns
    -------
    dict
        ``{"public_events": [...], "transcript_records": [...], "result": {...}}``
        All values are JSON-safe (dict/list/str/int/float/None).
    """
    public_events: list[dict] = []
    transcript_records: list[dict] = []

    def _public_sink(payload: dict) -> None:
        public_events.append(dict(payload))

    def _transcript_sink(event: dict, session_id: Any, turn_id: Any) -> None:
        transcript_records.append(
            {"sessionId": session_id, "turnId": turn_id, **dict(event)}
        )

    set_active_transcript_sink(_transcript_sink)
    try:
        result = await run_gate5b4c3_live_runner_boundary_async(
            request,
            config=config,
            adk_primitives_loader=lambda: make_primitives(runner),
            adk_tools=adk_tools,
            public_event_sink=_public_sink,
        )
    finally:
        set_active_transcript_sink(None)

    return {
        "public_events": public_events,
        "transcript_records": transcript_records,
        "result": {f: getattr(result, f, None) for f in _RESULT_PUBLIC_FIELDS},
    }
