# magi_agent/benchmarks/taubench/harness.py
"""Harness aggregation: pure orchestration over an injected solve_one callable.

No tau_bench import here — the real agent + env are injected by cli.py;
tests inject a deterministic fake solve_one.
"""
from __future__ import annotations

from collections.abc import Callable

from magi_agent.benchmarks.taubench.scorer import TauReport, score


def aggregate(results_per_task: list[list[bool]], *, trials: int) -> TauReport:
    """results_per_task[i] = list of per-trial success bools for task i."""
    successes = [sum(1 for ok in trials_list if ok) for trials_list in results_per_task]
    rewards = [1.0 if ok else 0.0 for tl in results_per_task for ok in tl]
    return score(successes_per_task=successes, trials=trials, rewards=rewards)


def run_subset(
    task_indices: list[int],
    *,
    trials: int,
    solve_one: Callable[[int, int], bool],
) -> TauReport:
    """Run each (task_index, trial) through solve_one and aggregate results.

    solve_one(task_index, trial) -> success bool.
    Live wiring injects the real tau-bench env + MagiTauAgent;
    tests inject a deterministic fake.
    """
    results = [
        [solve_one(t, trial) for trial in range(trials)]
        for t in task_indices
    ]
    return aggregate(results, trials=trials)


__all__ = ["aggregate", "run_subset"]
