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
    assert run_with_retry(lambda: next(seq), sleep=lambda *_: None) == (True, False)


def test_run_with_retry_persistent_infra_error() -> None:
    err = lambda: EpisodeResult(reward=0, done=False, turns=0, infra_error=True)  # noqa: E731
    assert run_with_retry(err, sleep=lambda *_: None) == (False, True)


def test_run_with_retry_no_sleep_on_first_success() -> None:
    slept = {"n": 0}

    def sleep(_seconds: float) -> None:
        slept["n"] += 1

    result = run_with_retry(
        lambda: EpisodeResult(reward=1.0, done=True, turns=1), sleep=sleep
    )
    assert result == (True, False)
    assert slept["n"] == 0


def test_run_with_retry_attempt_and_sleep_counts_on_recovery() -> None:
    seq = [EpisodeResult(reward=0, done=False, turns=0, infra_error=True),
           EpisodeResult(reward=0, done=False, turns=0, infra_error=True),
           EpisodeResult(reward=1.0, done=True, turns=1)]
    counts = {"attempt": 0, "sleep": 0}

    def attempt() -> EpisodeResult:
        r = seq[counts["attempt"]]
        counts["attempt"] += 1
        return r

    def sleep(_seconds: float) -> None:
        counts["sleep"] += 1

    assert run_with_retry(attempt, sleep=sleep) == (True, False)
    assert counts["attempt"] == 3  # initial + 2 retries
    assert counts["sleep"] == 2    # one backoff before each retry


def test_run_with_retry_persistent_attempt_count() -> None:
    counts = {"attempt": 0}

    def attempt() -> EpisodeResult:
        counts["attempt"] += 1
        return EpisodeResult(reward=0, done=False, turns=0, infra_error=True)

    assert run_with_retry(attempt, sleep=lambda *_: None) == (False, True)
    assert counts["attempt"] == 3  # initial + 2 retries (default retries=2)


def test_aggregate_rejects_ragged_results() -> None:
    with pytest.raises(ValueError):
        aggregate([[True, False], [True]], trials=2)


def test_aggregate_empty_tasks() -> None:
    report = aggregate([], trials=4)
    assert report.pass_hat_k[1] == 0.0
