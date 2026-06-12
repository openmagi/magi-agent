"""Memory subsystem golden regression. A diff = a memory behavior change.

Before/after ANY C4 edit, run this; never `capture --write` inside Pack C
(C4 is behavior-preserving)."""
from __future__ import annotations

import difflib
import json

import pytest

from tests.fixtures.memory_golden.capture import SCENARIOS, golden_path, render


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_memory_golden_trace_unchanged(name: str) -> None:
    path = golden_path(name)
    assert path.exists(), (
        f"missing golden '{name}'. Capture on the pristine pre-C4 base:\n"
        f"  python -m tests.fixtures.memory_golden.capture --write"
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
        pytest.fail(f"memory subsystem behavior changed for '{name}':\n{diff}")


def test_memory_goldens_are_non_trivial() -> None:
    matrix = {
        e["scenario"]: e
        for e in json.loads(golden_path("compaction_matrix").read_text())
    }
    assert matrix["disabled_harness"]["status"] == "disabled"
    assert matrix["approval_required"]["status"] == "approval_required"
    assert matrix["success_local_fake"]["status"] == "success"
    assert matrix["success_local_fake"]["executed"] is True
    assert matrix["success_local_fake"]["localTestOnly"] is True
    blocked = [e for e in matrix.values() if e["status"] == "blocked"]
    # 8 denial-strategy block branches (the 9th strategy branch surfaces as
    # status="approval_required") + the 2 kernel adapter-gate rows.
    assert len(blocked) == 10
    # ...with 9 distinct reason tuples (the 2 adapter-gate rows share one).
    assert len({tuple(e["reasonCodes"]) for e in blocked}) == 9
    assert all(e["receiptId"].startswith("memory-compaction:") for e in matrix.values())
    assert all(
        e["executed"] is False
        for name, e in matrix.items()
        if name != "success_local_fake"
    )

    recall = {
        e["scenario"]: e for e in json.loads(golden_path("recall_gate").read_text())
    }
    assert recall["disabled_with_policies"]["status"] == "disabled"
    assert recall["disabled_with_policies"]["adapterCalls"] == 0
    assert recall["missing_namespace"]["status"] == "blocked"
    assert "missing_memory_namespace_policy" in recall["missing_namespace"]["reasonCodes"]
    assert recall["missing_projection"]["status"] == "blocked"
    assert "missing_memory_projection_policy" in recall["missing_projection"]["reasonCodes"]
    assert recall["allowed_local_fake"]["status"] == "allowed"
    assert recall["allowed_local_fake"]["adapterCalls"] == 1
    assert recall["allowed_local_fake"]["references"] >= 1

    rows = json.loads(golden_path("review_trigger").read_text())
    trigger = {e["scenario"]: e["fires"] for e in rows if e["kind"] == "trigger"}
    assert trigger == {
        "disabled": False,
        "zero_turns": False,
        "on_boundary": True,
        "off_boundary": False,
        "interval_one_every_turn": True,
        "double_boundary": True,
    }
    review = {e["scenario"]: e for e in rows if e["kind"] == "review"}
    assert review["config_disabled"]["status"] == "disabled"
    assert review["config_disabled"]["reasonCodes"] == ["review_config_disabled"]
    assert review["env_gate_disabled"]["status"] == "disabled"
    assert review["env_gate_disabled"]["reasonCodes"] == ["review_env_gate_disabled"]
    mixed = review["reviewed_mixed_batch"]
    assert mixed["status"] == "reviewed"
    assert mixed["candidates"] == 2
    assert mixed["droppedDeclarative"] == 1
    assert mixed["attemptedWrites"] == 1
    assert mixed["simulated"] == 1
    assert mixed["written"] == 0
    assert mixed["writeReceipts"][0]["factPreview"].startswith("sha256:")
