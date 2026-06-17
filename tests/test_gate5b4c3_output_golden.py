"""Golden matrix for gate5b4c3 live-runner boundary output (Phase 0 Task 3).

Locks the three observable output streams (public_events, transcript_records,
result) across three representative scenarios.  Goldens are written on first
run and compared on subsequent runs.  Set UPDATE_GOLDEN=1 to refresh all
goldens after an intentional behaviour change.

Volatile fields
---------------
``latency_ms`` in ``turn_end`` transcript records is a wall-clock measurement
and therefore not deterministic.  ``_normalize`` replaces its value with the
sentinel string ``"<normalized>"`` before comparison so that timing jitter
cannot cause spurious failures.  No other field required normalization:
  - tool IDs (``id`` / ``call_id``) are deterministic SHA-based digests
  - ``output_preview`` is a content-addressed hash of the tool result payload
  - ``sessionId`` and ``turnId`` are fixed by the fake request fixtures

Config requirement
------------------
``capture_boundary`` short-circuits to ``status="skipped"`` when no
``config`` is passed.  All scenarios here pass ``_enabled_config()`` from the
boundary test module so that goldens reflect real completed runs.

Tool-scenario notes
-------------------
The ``tool_then_final`` scenario uses ``_FunctionCallThenFinalRunner`` rather
than a plain ``_FakeRunner([function_call_event, final_event])`` because the
``_FakeRunner`` event-list mode replays ALL events on every ``run_async``
invocation, causing the boundary to loop until ``max_steps`` is exhausted.
``_FunctionCallThenFinalRunner`` uses a class-level call counter to switch
from "emit function call" to "emit final answer" on the second invocation —
which is the production-representative two-round-trip flow.

The ``readonly_text`` scenario requires a non-empty ``adk_tools`` to satisfy
the ``shadow_readonly`` tool policy check (the boundary drops with
``tool_policy_mismatch`` when ``tools_enabled=True`` but ``adk_tools`` is
empty).  A bare ``object()`` sentinel suffices because the runner never emits
a function call, so the tool is never dispatched.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest

from tests.support.gate5b4c3_capture import capture_boundary
from tests.support.gate5b4c3_fakes import (
    _FakeRunner,
    _FunctionCallOnlyEvent,
    _FunctionCallThenFinalRunner,
    _ManualCalculationTool,
    final_event,
)
from tests.test_gate5b4c3_live_runner_boundary import (
    _enabled_config,
    _readonly_request,
    _request,
    _selected_full_toolhost_request,
)

_GOLDEN_DIR = Path(__file__).parent / "golden" / "gate5b4c3"

# Sentinel value substituted for volatile timing values.
_LATENCY_SENTINEL = "<normalized>"


def _normalize(snap: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of *snap* with volatile fields replaced by stable sentinels.

    Currently normalises:
    - ``latency_ms`` in transcript records (wall-clock timing, always volatile)
    - ``durationMs`` in public_events (wall-clock tool-execution duration, always volatile)
    """
    records = []
    for rec in snap.get("transcript_records", []):
        rec = dict(rec)
        if "latency_ms" in rec:
            rec["latency_ms"] = _LATENCY_SENTINEL
        records.append(rec)
    public_events = []
    for evt in snap.get("public_events", []):
        evt = dict(evt)
        if "durationMs" in evt:
            evt["durationMs"] = _LATENCY_SENTINEL
        public_events.append(evt)
    return {**snap, "transcript_records": records, "public_events": public_events}


def _scenarios() -> dict[str, tuple[Any, Any, dict[str, Any]]]:
    """Return the three base golden scenarios as (request, runner, capture_kwargs)."""
    _FunctionCallThenFinalRunner.calls = []
    _FunctionCallThenFinalRunner.event_factory = _FunctionCallOnlyEvent

    return {
        # Plain text response — no tools.
        "text_only": (
            _request(),
            _FakeRunner([final_event("done")]),
            {},
        ),
        # One tool call (Calculation) followed by a final text answer.
        "tool_then_final": (
            _selected_full_toolhost_request(),
            _FunctionCallThenFinalRunner(),
            {"adk_tools": (_ManualCalculationTool,)},
        ),
        # Readonly-tool policy, runner returns plain text with no tool dispatch.
        "readonly_text": (
            _readonly_request(),
            _FakeRunner([final_event("ro")]),
            # A non-empty adk_tools is required to satisfy shadow_readonly policy.
            {"adk_tools": (object(),)},
        ),
    }


@pytest.mark.parametrize("name", ["text_only", "tool_then_final", "readonly_text"])
def test_gate5b4c3_output_matches_golden(name: str) -> None:
    """Compare live boundary output to a stored golden snapshot.

    On first run (or when UPDATE_GOLDEN=1) the golden is written; on
    subsequent runs the serialised snapshot must match byte-for-byte.
    """
    scenarios = _scenarios()
    request, runner, extra = scenarios[name]

    snap = asyncio.run(
        capture_boundary(request, runner, config=_enabled_config(), **extra)
    )

    # Confirm a real (non-skipped) run.
    assert snap["result"]["status"] not in {"skipped", "dropped"}, (
        f"Golden scenario {name!r} produced status={snap['result']['status']!r}; "
        "expected a completed run.  Check config and adk_tools."
    )

    normalised = _normalize(snap)
    blob = json.dumps(normalised, indent=2, sort_keys=True, default=str) + "\n"

    golden_path = _GOLDEN_DIR / f"{name}.json"

    if not golden_path.exists() or os.environ.get("UPDATE_GOLDEN") == "1":
        golden_path.parent.mkdir(parents=True, exist_ok=True)
        golden_path.write_text(blob, encoding="utf-8")
        # First write — test passes by definition on the write path.
        return

    stored = golden_path.read_text(encoding="utf-8")
    assert blob == stored, (
        f"gate5b4c3 output drifted for scenario {name!r}.\n"
        "If the change is intentional, regenerate with:  UPDATE_GOLDEN=1 pytest tests/test_gate5b4c3_output_golden.py\n"
        f"Golden path: {golden_path}"
    )
