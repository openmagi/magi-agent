"""Goal-loop decision golden regression. A diff = a loop-policy behavior change.

Before/after ANY C2 edit, run this; never `capture --write` inside Pack C
(C2 is behavior-preserving)."""
from __future__ import annotations

import difflib
import json

import pytest

from tests.fixtures.goal_loop_golden.capture import SCENARIOS, golden_path, render


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_goal_loop_golden_trace_unchanged(name: str) -> None:
    path = golden_path(name)
    assert path.exists(), (
        f"missing golden '{name}'. Capture on the pristine pre-C2 base:\n"
        f"  python -m tests.fixtures.goal_loop_golden.capture --write"
    )
    live = render(SCENARIOS[name]())
    golden = path.read_text()
    if live != golden:
        diff = "".join(
            difflib.unified_diff(
                golden.splitlines(keepends=True), live.splitlines(keepends=True),
                fromfile=f"golden/{name}.json", tofile=f"live/{name}",
            )
        )
        pytest.fail(f"goal-loop decision behavior changed for '{name}':\n{diff}")


def test_goal_loop_golden_is_non_trivial() -> None:
    trace = json.loads(golden_path("decision_matrix").read_text())
    by_name = {e["scenario"]: e for e in trace}
    assert len(by_name) == 11
    assert by_name["disabled"]["reason"] == "disabled"
    assert by_name["spend_capped"]["reason"] == "spend_capped"
    assert by_name["satisfied_gate_off"]["statusAfter"] == "satisfied"
    assert by_name["not_satisfied_continue"]["decision"] == "continue"
    assert by_name["not_satisfied_continue"]["continuationDigest"]
    assert by_name["exhausted_on_advance"]["reason"] == "exhausted"
    assert by_name["evidence_unmet"]["reason"] == "evidence_unmet"
    assert by_name["judge_budget"]["reason"] == "judge_budget"
