"""Gate5B dispatch golden regression. A diff = a gate5b behavior change to review.

Before/after ANY C1 edit, run this; never `capture --write` inside Pack C
(C1 is behavior-preserving)."""
from __future__ import annotations

import difflib
import json

import pytest

from tests.fixtures.gate5b_golden.capture import SCENARIOS, golden_path, render


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_gate5b_golden_trace_unchanged(name: str) -> None:
    path = golden_path(name)
    assert path.exists(), (
        f"missing golden '{name}'. Capture on the pristine pre-C1 base:\n"
        f"  python -m tests.fixtures.gate5b_golden.capture --write"
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
        pytest.fail(f"gate5b dispatch behavior changed for '{name}':\n{diff}")


def test_gate5b_goldens_are_non_trivial() -> None:
    ok = json.loads(golden_path("dispatch_ok").read_text())
    blocked = json.loads(golden_path("dispatch_blocked").read_text())
    assert len(ok) == 8 and all(e["status"] == "ok" for e in ok)
    assert all(e["output_digest"] for e in ok), "ok trace must carry output digests"
    blocked_events = [e for e in blocked if e["status"] == "blocked"]
    # 5 distinct policy families must each have produced a block with its own reason
    # (event 5 is the single ok completion that arms the budget family).
    assert [e["status"] for e in blocked] == ["blocked"] * 4 + ["ok", "blocked"]
    assert len(blocked_events) == 5
    assert len({e["reason"] for e in blocked_events}) == 5
