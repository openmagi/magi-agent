"""Control-plane golden regression.

For each of the 4 hard seams, run the scenario driver and assert the live
decision trace equals the committed golden JSON exactly. A mismatch means a
control-plane behavior change; the failure prints a unified diff and the regen
instruction.

One-liner for later phases: *Before/after any control-plane edit, run this
golden regression; a diff = a behavior change to review.*
"""

from __future__ import annotations

import difflib
import json

import pytest

from tests.fixtures.neutral_runtime_golden.capture import (
    SCENARIOS,
    golden_path,
    render,
)


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_golden_trace_unchanged(name: str) -> None:
    path = golden_path(name)
    assert path.exists(), (
        f"missing golden for '{name}'. Capture it on the pristine base with:\n"
        f"  python -m tests.fixtures.neutral_runtime_golden.capture --write"
    )

    live = render(SCENARIOS[name]())
    golden = path.read_text()

    if live != golden:
        diff = "".join(
            difflib.unified_diff(
                golden.splitlines(keepends=True),
                live.splitlines(keepends=True),
                fromfile=f"golden/{name}.json",
                tofile=f"live/{name}",
            )
        )
        pytest.fail(
            f"control-plane behavior changed for seam '{name}':\n{diff}\n"
            f"If this change is intended, re-run capture --write and review the "
            f"diff in the PR:\n"
            f"  python -m tests.fixtures.neutral_runtime_golden.capture --write"
        )


def test_goldens_are_non_trivial() -> None:
    # Each golden must encode a real, non-empty decision trace for its seam.
    expectations = {
        "loop_guard": lambda t: any(
            e["kind"] == "after_tool" and e["override"] is not None for e in t
        ),
        "compaction": lambda t: any(
            e["kind"] == "compaction" and e["fired"] for e in t
        ),
        "edit_retry": lambda t: any(
            e["kind"] == "tool_error" and e["override"] is not None for e in t
        ),
        "ga_constraint": lambda t: any(e["kind"] == "reinject" for e in t),
    }
    for name, predicate in expectations.items():
        trace = json.loads(golden_path(name).read_text())
        assert trace, f"golden '{name}' is empty"
        assert predicate(trace), f"golden '{name}' lacks its expected seam decision"
