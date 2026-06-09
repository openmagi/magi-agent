# tests/benchmarks/taubench/test_harness.py
from __future__ import annotations

import pytest

from magi_agent.benchmarks.taubench.harness import run_subset


def test_run_subset_aggregates_pass_hat_k() -> None:
    # task 0 succeeds every trial; task 1 succeeds on even trials
    def solve_one(task: int, trial: int) -> bool:
        return True if task == 0 else (trial % 2 == 0)

    report = run_subset([0, 1], trials=4, solve_one=solve_one)
    # task0 4/4, task1 2/4 -> pass^1 = (1.0 + 0.5)/2 = 0.75
    assert report.pass_hat_k[1] == pytest.approx(0.75)
    assert report.trials == 4
