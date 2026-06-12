"""Scheduler tick golden regression. A diff = a scheduling behavior change.

Before/after ANY C3 edit, run this; never `capture --write` inside Pack C
(C3 is behavior-preserving)."""
from __future__ import annotations

import difflib
import json

import pytest

from tests.fixtures.scheduler_golden.capture import SCENARIOS, golden_path, render


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_scheduler_golden_trace_unchanged(name: str) -> None:
    path = golden_path(name)
    assert path.exists(), (
        f"missing golden '{name}'. Capture on the pristine pre-C3 base:\n"
        f"  python -m tests.fixtures.scheduler_golden.capture --write"
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
        pytest.fail(f"scheduler tick behavior changed for '{name}':\n{diff}")


def test_scheduler_goldens_are_non_trivial() -> None:
    fires = json.loads(golden_path("fires_due").read_text())
    assert fires[0]["firedJobIds"] == ["job-due"]
    assert fires[0]["skippedJobIds"] == ["job-later"]
    assert fires[1]["firedJobIds"] == []  # at-most-once: no re-fire
    assert all(not any(e["authorityFlags"].values()) for e in fires)
    assert all(e["evidenceDigest"].startswith("sha256:") for e in fires)
    blocked = json.loads(golden_path("blocked_lease").read_text())
    assert blocked[0]["status"] == "tick_blocked_lease"
    held = json.loads(golden_path("lock_held").read_text())
    assert held[0]["status"] == "tick_skipped_lock_held"
