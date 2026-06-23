"""E-8 — phase classifier soft-fail: misclassification never fail-closes.

``cli/engine._select_policy_phase`` is a keyword-marker classifier that
maps the prompt + harness task types to a phase id. The pre-E-8 logic
short-circuited to ``patch_generation`` / ``code_search`` / ``source_acquisition``
whenever a coding/research keyword was hit. But the SELECTED phase is
later validated against ``phase_routes[phase].routeDenied`` — and a
classifier-picked phase whose route is denied for the resolved model
ends up as ``runner_policy_route_denied`` (fail-CLOSED).

E-8 unifies the decision: only return a phase whose route is NOT denied
for the current model. When the keyword classifier would otherwise pick
a denied phase, fall through to the conversational
``final_answer_drafting`` phase instead. Strictly fewer blocked turns,
no weakened gate (the route check stays — we only avoid dead-ending).
"""

from __future__ import annotations

from typing import Any

import pytest

from magi_agent.cli.engine import _select_policy_phase


# ---------------------------------------------------------------------------
# Helpers — minimal RunnerPolicyAssembly stub. ``_select_policy_phase`` only
# reads ``task_profile`` (legacy fallback) so a permissive stub suffices.
# ---------------------------------------------------------------------------


class _StubAssembly:
    task_profile: dict[str, Any] = {}


def _routes(*phases: str, denied: set[str] | None = None) -> dict[str, dict[str, Any]]:
    denied_set = denied or set()
    return {p: {"routeDenied": p in denied_set, "capabilities": ()} for p in phases}


# ---------------------------------------------------------------------------
# Pre-E-8 foot-gun regression: "hi" on a coding-capable phase set.
# ---------------------------------------------------------------------------


def test_plain_hi_falls_through_to_conversational() -> None:
    """A plain greeting must NEVER pick a coding phase (the documented
    foot-gun) — even when the keyword classifier sees no markers and
    the phase set is coding-heavy."""

    phase_routes = _routes(
        "patch_generation",
        "code_search",
        "final_answer_drafting",
    )
    out = _select_policy_phase(
        phases=tuple(phase_routes.keys()),
        prompt="hi",
        harness_state=None,
        assembly=_StubAssembly(),
        phase_routes=phase_routes,
    )
    assert out == "final_answer_drafting"


# ---------------------------------------------------------------------------
# Soft-fail core: when the keyword-picked phase is denied for the model,
# fall through to the conversational phase rather than block.
# ---------------------------------------------------------------------------


def test_coding_prompt_denied_phase_softens_to_conversational() -> None:
    """A coding prompt whose routed model lacks ``coding`` (so
    ``patch_generation`` carries ``routeDenied: True``) must fall through
    to ``final_answer_drafting`` — strictly fewer dead-ended turns."""

    phase_routes = _routes(
        "patch_generation",
        "code_search",
        "test_interpretation",
        "final_answer_drafting",
        denied={"patch_generation", "code_search", "test_interpretation"},
    )
    out = _select_policy_phase(
        phases=tuple(phase_routes.keys()),
        prompt="fix the bug in foo.py",
        harness_state=None,
        assembly=_StubAssembly(),
        phase_routes=phase_routes,
    )
    assert out == "final_answer_drafting"


def test_research_prompt_denied_phase_softens_to_conversational() -> None:
    phase_routes = _routes(
        "source_acquisition",
        "source_extraction",
        "final_answer_drafting",
        denied={"source_acquisition", "source_extraction"},
    )
    out = _select_policy_phase(
        phases=tuple(phase_routes.keys()),
        prompt="please research the latest source on this",
        harness_state=None,
        assembly=_StubAssembly(),
        phase_routes=phase_routes,
    )
    assert out == "final_answer_drafting"


# ---------------------------------------------------------------------------
# Positive cases: when the phase IS available, the classifier still picks it.
# ---------------------------------------------------------------------------


def test_coding_prompt_picks_patch_generation_when_route_available() -> None:
    phase_routes = _routes(
        "patch_generation",
        "code_search",
        "final_answer_drafting",
    )
    out = _select_policy_phase(
        phases=tuple(phase_routes.keys()),
        prompt="fix the bug in foo.py",
        harness_state=None,
        assembly=_StubAssembly(),
        phase_routes=phase_routes,
    )
    assert out == "patch_generation"


def test_research_prompt_picks_source_acquisition_when_route_available() -> None:
    phase_routes = _routes(
        "source_acquisition",
        "final_answer_drafting",
    )
    out = _select_policy_phase(
        phases=tuple(phase_routes.keys()),
        prompt="research the source for this claim",
        harness_state=None,
        assembly=_StubAssembly(),
        phase_routes=phase_routes,
    )
    assert out == "source_acquisition"


# ---------------------------------------------------------------------------
# Back-compat: callers that don't pass phase_routes still work.
# ---------------------------------------------------------------------------


def test_phase_routes_kwarg_optional_defaults_to_legacy_behavior() -> None:
    """Existing callers that omit ``phase_routes`` (or pass ``None``)
    must keep the legacy phase-pick behavior."""

    out = _select_policy_phase(
        phases=("patch_generation", "final_answer_drafting"),
        prompt="fix bug",
        harness_state=None,
        assembly=_StubAssembly(),
    )
    assert out == "patch_generation"


# ---------------------------------------------------------------------------
# Property: no input yields a denied phase when a conversational fallback
# is available.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prompt",
    [
        "hi",
        "hello there",
        "what is the weather",
        "fix the bug",
        "research sources for x",
        "cite the relevant paper",
        "browse the web",
        "write tests for foo",
    ],
)
def test_no_input_picks_denied_phase_when_fallback_available(prompt: str) -> None:
    phase_routes = _routes(
        "patch_generation",
        "code_search",
        "test_interpretation",
        "source_acquisition",
        "source_extraction",
        "final_answer_drafting",
        denied={
            "patch_generation",
            "code_search",
            "test_interpretation",
            "source_acquisition",
            "source_extraction",
        },
    )
    out = _select_policy_phase(
        phases=tuple(phase_routes.keys()),
        prompt=prompt,
        harness_state=None,
        assembly=_StubAssembly(),
        phase_routes=phase_routes,
    )
    assert out == "final_answer_drafting"
    assert phase_routes[out]["routeDenied"] is False
