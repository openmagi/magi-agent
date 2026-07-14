"""Characterization + byte-equivalence tests for the P5-M1a input-drop shim.

The governed serving path (``transport/gate5b_serving.py``) used to delegate a
dropped runner input to ``run_gate5b4c3_live_runner_boundary_async`` purely so the
legacy boundary's error-result path would build the drop response — the last
governed -> legacy-engine call. P5-M1a replaces that call with
``build_gate5b4c3_input_drop_boundary_result`` so the boundary class can retire
(M1b) without the governed path losing its refusal behavior.

These tests LOCK the equivalence: for every distinct input-adapter drop reason,
the shim must produce a boundary result whose serialized wire shape is identical
to what the legacy boundary produces for the same request+diagnostic, and it must
emit the same turn-completion transcript record (a single ``turn_end`` for a drop;
no ``turn_start``, no ``message``) through the same process-global sink.
"""
from __future__ import annotations

from typing import Any

import pytest

from magi_agent.observability.transcript import (
    set_active_transcript_sink,
)
from magi_agent.shadow.gate5b4c3_live_runner_boundary import (
    Gate5B4C3LiveRunnerBoundary,
    Gate5B4C3LiveRunnerBoundaryResult,
    build_gate5b4c3_input_drop_boundary_result,
)
from magi_agent.shadow.gate5b4c3_runner_input_adapter import (
    build_gate5b4c3_runner_input,
)
from magi_agent.shadow.gate5b4c3_shadow_generation_contract import (
    Gate5B4C3ShadowGenerationDiagnostic,
    Gate5B4C3ShadowGenerationRequest,
    build_gate5b4c3_shadow_generation_diagnostic,
)

# Reuse the canonical request payload builder + the enabled config from the
# boundary test module so the two paths are compared against the SAME fixtures
# the legacy boundary is tested with.
from tests.test_gate5b4c3_live_runner_boundary import (  # noqa: E402
    _enabled_config,
    _payload,
)


# ---------------------------------------------------------------------------
# Drop-triggering requests (each a DISTINCT input-adapter drop reason that
# passes request-model validation and is dropped inside the input adapter).
# ---------------------------------------------------------------------------


def _input_token_budget_drop_request() -> Gate5B4C3ShadowGenerationRequest:
    """Long input, tiny per-turn input-token budget -> input_token_budget_exceeded."""
    return Gate5B4C3ShadowGenerationRequest.model_validate(
        _payload(
            turn={
                **_payload()["turn"],  # type: ignore[arg-type]
                "sanitizedCurrentTurnText": "x" * 80,
            },
            budgets={"maxEstimatedInputTokens": 10},
        )
    )


def _unsafe_policy_drop_request() -> Gate5B4C3ShadowGenerationRequest:
    """A policy-shape inconsistency the request model tolerates but the input
    adapter rejects -> unsafe_policy (a DISTINCT drop reason and code path from
    the token-budget drop). ``toolHostDispatchAllowed=True`` under a ``disabled``
    tools policy fails ``disabled_tools_policy_valid`` in the adapter."""
    return Gate5B4C3ShadowGenerationRequest.model_validate(
        _payload(
            policy={
                **_payload()["policy"],  # type: ignore[arg-type]
                "toolHostDispatchAllowed": True,
            },
        )
    )


# Both cases pass the shadow-generation DIAGNOSTIC (accepted=True) and then drop
# inside the input adapter -- i.e. exactly the class of request that reaches the
# governed drop fallback in transport/gate5b_serving.py (which returns 503 before
# the fallback whenever diagnostic.accepted is False). A total-token-budget
# overflow is deliberately NOT used here: it makes the diagnostic reject with
# ``budget_exhausted`` (accepted=False), so it never reaches the drop fallback and
# the legacy boundary would return skipped/not_accepted, not dropped.
_DROP_CASES = {
    "input_token_budget_exceeded": _input_token_budget_drop_request,
    "unsafe_policy": _unsafe_policy_drop_request,
}


def _diagnostic(
    request: Gate5B4C3ShadowGenerationRequest,
) -> Gate5B4C3ShadowGenerationDiagnostic:
    return build_gate5b4c3_shadow_generation_diagnostic(
        request, config=_enabled_config()
    )


def _loader_that_must_not_run() -> Any:
    raise AssertionError("ADK primitives loader must not run on the drop path")


def _wire(result: Gate5B4C3LiveRunnerBoundaryResult) -> dict[str, object]:
    """Serialize both internal (excluded) and wire fields for full comparison."""
    dumped = result.model_dump(by_alias=True, mode="python", warnings=False)
    # outputTextInternal / usageInternal are Field(exclude=True); include them
    # explicitly so equivalence covers the internal legs the serving path reads.
    dumped["outputTextInternal"] = result.output_text_internal
    dumped["usageInternal"] = result.usage_internal
    return dumped


# ---------------------------------------------------------------------------
# 1. Pre-condition: each fixture genuinely produces the intended DISTINCT drop
#    reason through the input adapter (guards the test itself from bit-rot).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("expected_reason", sorted(_DROP_CASES))
def test_fixture_produces_intended_distinct_drop_reason(expected_reason: str) -> None:
    request = _DROP_CASES[expected_reason]()
    adapter_result = build_gate5b4c3_runner_input(request)
    assert adapter_result.status == "dropped"
    assert adapter_result.reason == expected_reason


# ---------------------------------------------------------------------------
# 2. Byte-equivalence: the shim's boundary result == the legacy boundary's drop
#    result for the SAME request+diagnostic, per distinct drop reason.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("drop_reason", sorted(_DROP_CASES))
def test_shim_result_is_wire_identical_to_legacy_boundary_drop(drop_reason: str) -> None:
    request = _DROP_CASES[drop_reason]()
    diagnostic = _diagnostic(request)
    adapter_reason = build_gate5b4c3_runner_input(request).reason

    # Legacy path: the boundary must NOT load ADK for a drop (loader raises).
    legacy = Gate5B4C3LiveRunnerBoundary(_loader_that_must_not_run).invoke(
        request, config=_enabled_config()
    )

    # Shim path: same request+diagnostic, no boundary orchestration.
    shim = build_gate5b4c3_input_drop_boundary_result(
        request,
        diagnostic=diagnostic,
        drop_reason=adapter_reason,
    )

    assert legacy.status == shim.status == "dropped"
    assert legacy.reason == shim.reason == "input_adapter_drop"
    assert legacy.error_preview == shim.error_preview == drop_reason

    legacy_wire = _wire(legacy)
    shim_wire = _wire(shim)
    # latencyMs is trivially-small wall-clock elapsed on both paths (the drop
    # never runs the engine); it is timing noise, not part of the response
    # contract. Everything else must match byte-for-byte.
    legacy_wire.pop("latencyMs", None)
    shim_wire.pop("latencyMs", None)
    assert shim_wire == legacy_wire


# ---------------------------------------------------------------------------
# 3. Transcript emission parity: for a drop the shim emits exactly ONE turn_end
#    record (no turn_start, no message) through the process-global sink, matching
#    the legacy boundary's _emit_turn_completion chokepoint.
# ---------------------------------------------------------------------------


def _capture_records(fn) -> list[tuple[dict, str | None, str | None]]:
    records: list[tuple[dict, str | None, str | None]] = []

    def _sink(event: dict, session_id: str | None, turn_id: str | None) -> None:
        records.append((dict(event), session_id, turn_id))

    set_active_transcript_sink(_sink)
    try:
        fn()
    finally:
        set_active_transcript_sink(None)
    return records


@pytest.mark.parametrize("drop_reason", sorted(_DROP_CASES))
def test_shim_emits_same_turn_completion_records_as_legacy(drop_reason: str) -> None:
    request = _DROP_CASES[drop_reason]()
    diagnostic = _diagnostic(request)
    adapter_reason = build_gate5b4c3_runner_input(request).reason

    legacy_records = _capture_records(
        lambda: Gate5B4C3LiveRunnerBoundary(_loader_that_must_not_run).invoke(
            request, config=_enabled_config()
        )
    )
    shim_records = _capture_records(
        lambda: build_gate5b4c3_input_drop_boundary_result(
            request,
            diagnostic=diagnostic,
            drop_reason=adapter_reason,
        )
    )

    # A drop emits exactly ONE record: turn_end. No turn_start, no message.
    assert [r[0]["type"] for r in legacy_records] == ["turn_end"]
    assert [r[0]["type"] for r in shim_records] == ["turn_end"]

    legacy_event, legacy_sid, legacy_tid = legacy_records[0]
    shim_event, shim_sid, shim_tid = shim_records[0]
    # Identity coordinates identical.
    assert shim_sid == legacy_sid
    assert shim_tid == legacy_tid
    # turn_end record content identical except latency_ms timing noise.
    legacy_event.pop("latency_ms", None)
    shim_event.pop("latency_ms", None)
    assert shim_event == legacy_event
    assert shim_event["terminal"] == "dropped"
    assert shim_event["reason"] == "input_adapter_drop"


def test_shim_emits_nothing_when_no_transcript_sink_registered() -> None:
    """Fail-open parity: with no sink registered the shim (like the boundary)
    performs no transcript side effect and still returns the drop result."""
    request = _input_token_budget_drop_request()
    diagnostic = _diagnostic(request)
    set_active_transcript_sink(None)
    result = build_gate5b4c3_input_drop_boundary_result(
        request,
        diagnostic=diagnostic,
        drop_reason="input_token_budget_exceeded",
    )
    assert result.status == "dropped"
    assert result.reason == "input_adapter_drop"


# ---------------------------------------------------------------------------
# 4. Active-tools threading: the runnerErrorDiagnostic must reflect the tools
#    passed to the shim exactly as the legacy boundary reflects its adk_tools.
# ---------------------------------------------------------------------------


class _NamedTool:
    def __init__(self, name: str) -> None:
        self.name = name


def test_shim_threads_active_tool_names_like_legacy() -> None:
    request = _input_token_budget_drop_request()
    diagnostic = _diagnostic(request)
    adapter_reason = build_gate5b4c3_runner_input(request).reason
    tool = _NamedTool("Bash")

    legacy = Gate5B4C3LiveRunnerBoundary(
        _loader_that_must_not_run, adk_tools=(tool,)
    ).invoke(request, config=_enabled_config())
    shim = build_gate5b4c3_input_drop_boundary_result(
        request,
        diagnostic=diagnostic,
        drop_reason=adapter_reason,
        active_tools=(tool,),
    )

    legacy_wire = _wire(legacy)
    shim_wire = _wire(shim)
    legacy_wire.pop("latencyMs", None)
    shim_wire.pop("latencyMs", None)
    assert shim_wire == legacy_wire
    # And the tool name is actually surfaced in the diagnostic.
    active_names = shim_wire["runnerErrorDiagnostic"]["activeToolNames"]  # type: ignore[index]
    assert "Bash" in active_names
