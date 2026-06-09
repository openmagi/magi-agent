# tests/benchmarks/taubench/test_harness.py
from __future__ import annotations

import pytest

from magi_agent.benchmarks.taubench.episode import EpisodeResult
from magi_agent.benchmarks.taubench.harness import aggregate, run_subset, run_with_retry


def test_run_subset_aggregates_pass_hat_k() -> None:
    # task 0 succeeds every trial; task 1 succeeds on even trials
    def solve_one(task: int, trial: int) -> bool:
        return True if task == 0 else (trial % 2 == 0)

    report = run_subset([0, 1], trials=4, solve_one=solve_one)
    # task0 4/4, task1 2/4 -> pass^1 = (1.0 + 0.5)/2 = 0.75
    assert report.pass_hat_k[1] == pytest.approx(0.75)
    assert report.trials == 4


def test_run_with_retry_success_first_try() -> None:
    assert run_with_retry(lambda: EpisodeResult(reward=1.0, done=True, turns=1)) == (True, False)


def test_run_with_retry_recovers_on_second() -> None:
    seq = iter([EpisodeResult(reward=0, done=False, turns=0, infra_error=True),
                EpisodeResult(reward=1.0, done=True, turns=1)])
    assert run_with_retry(lambda: next(seq)) == (True, False)


def test_run_with_retry_persistent_infra_error() -> None:
    err = lambda: EpisodeResult(reward=0, done=False, turns=0, infra_error=True)  # noqa: E731
    assert run_with_retry(err) == (False, True)


def test_aggregate_rejects_ragged_results() -> None:
    with pytest.raises(ValueError):
        aggregate([[True, False], [True]], trials=2)


def test_aggregate_empty_tasks() -> None:
    report = aggregate([], trials=4)
    assert report.pass_hat_k[1] == 0.0
