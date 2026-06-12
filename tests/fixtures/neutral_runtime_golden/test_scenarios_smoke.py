"""Smoke test for the 4 scenario drivers — each must return a non-empty trace
whose events match the expected seam signature (the REAL seam, per HEAD code):

* loop guard  -> an ``after_tool`` event carrying an override (hard stop).
* compaction  -> a ``compaction`` event with ``fired: true``.
* edit retry  -> a ``tool_error`` event carrying an override (corrective reflect).
* GA constraint -> a ``reinject`` event whose source contains ``ga``/``constraint``.
"""

from __future__ import annotations

from tests.fixtures.neutral_runtime_golden.scenarios import (
    run_compaction_scenario,
    run_edit_retry_scenario,
    run_ga_constraint_scenario,
    run_loop_guard_scenario,
)


def test_loop_guard_scenario_emits_after_tool_override() -> None:
    trace = run_loop_guard_scenario()
    assert trace
    overrides = [
        e for e in trace if e["kind"] == "after_tool" and e["override"] is not None
    ]
    assert overrides, trace


def test_compaction_scenario_emits_compaction_fired() -> None:
    trace = run_compaction_scenario()
    assert trace
    fired = [e for e in trace if e["kind"] == "compaction" and e["fired"]]
    assert fired, trace


def test_edit_retry_scenario_emits_tool_error_override() -> None:
    trace = run_edit_retry_scenario()
    assert trace
    overrides = [
        e for e in trace if e["kind"] == "tool_error" and e["override"] is not None
    ]
    assert overrides, trace


def test_ga_constraint_scenario_emits_reinject() -> None:
    trace = run_ga_constraint_scenario()
    assert trace
    reinjects = [
        e
        for e in trace
        if e["kind"] == "reinject" and ("ga" in e["source"] or "constraint" in e["source"])
    ]
    assert reinjects, trace
