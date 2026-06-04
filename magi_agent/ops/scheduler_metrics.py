"""Scheduler-executor outcome metrics — Track A, PR A5.

Counters and labels for scheduler executor outcomes.  No live metrics backend
is wired — counts are surfaced via the existing ``RuntimeMetricRecord`` /
``RuntimeMetricsSnapshot`` structure from ``ops/metrics.py``.

Counter labels (``SCHEDULER_OUTCOME_COUNTER_LABELS``):

    fired             — due job was ticked and execution was attempted.
    suppressed_silent — turn output was exactly ``[SILENT]`` (delivery suppressed).
    skipped           — no execution (shadow mode, approval denied, or non-due).
    timed_out         — turn runner exceeded the inactivity timeout (600s default).
    lease_rejected    — tick was blocked because the scheduler lease was invalid.

Usage pattern::

    counts = build_scheduler_outcome_counters()
    counts = increment_scheduler_counter(counts, "fired", delta=1)
    records = scheduler_counts_to_metric_records(
        counts, trace_digest=trace, policy_snapshot_digest=policy
    )
    snapshot = build_runtime_metrics_snapshot([], extra_metrics=records)

Forbidden imports: urllib, socket, subprocess, http, requests — none appear here.
"""
from __future__ import annotations

from magi_agent.ops.metrics import RuntimeMetricRecord


#: Canonical set of scheduler outcome counter labels.
SCHEDULER_OUTCOME_COUNTER_LABELS: frozenset[str] = frozenset({
    "fired",
    "suppressed_silent",
    "skipped",
    "timed_out",
    "lease_rejected",
})

#: Metric name prefix for scheduler outcome counters.
#: Must start with ``ops.`` to satisfy the ``RuntimeMetricRecord`` metric-name
#: validator (``SAFE_METRIC_RE`` in ``ops/safety.py``).
_METRIC_PREFIX = "ops.scheduler.outcome"

#: Placeholder digests used when no real trace/policy context is available.
_ZERO_DIGEST = "sha256:" + "0" * 64


def build_scheduler_outcome_counters() -> dict[str, int]:
    """Return a fresh zero-initialised counter dict for all outcome labels."""
    return {label: 0 for label in sorted(SCHEDULER_OUTCOME_COUNTER_LABELS)}


def increment_scheduler_counter(
    counts: dict[str, int],
    label: str,
    *,
    delta: int = 1,
) -> dict[str, int]:
    """Return a new counter dict with ``label`` incremented by ``delta``.

    Raises ``KeyError`` if ``label`` is not a known scheduler outcome label.
    This is intentional: callers must use the canonical label set to avoid
    silent metric drift.

    The input dict is not mutated — a new dict is returned.
    """
    if label not in SCHEDULER_OUTCOME_COUNTER_LABELS:
        raise KeyError(
            f"Unknown scheduler outcome label {label!r}. "
            f"Valid labels: {sorted(SCHEDULER_OUTCOME_COUNTER_LABELS)}"
        )
    result = dict(counts)
    result[label] = result.get(label, 0) + delta
    return result


def scheduler_counts_to_metric_records(
    counts: dict[str, int],
    *,
    trace_digest: str = _ZERO_DIGEST,
    policy_snapshot_digest: str = _ZERO_DIGEST,
) -> tuple[RuntimeMetricRecord, ...]:
    """Convert a scheduler outcome counter dict to ``RuntimeMetricRecord`` tuples.

    Only non-zero counters are included — zero counts are suppressed to keep
    the metric surface minimal and consistent with ``build_runtime_metrics_snapshot``
    (which also suppresses zero-count entries).

    Each record uses:
      metric_name  = ``scheduler.outcome.<label>``
      unit         = ``count``
      dimensions   = ``{"outcome": label}``
    """
    records: list[RuntimeMetricRecord] = []
    for label in sorted(counts):
        count = counts[label]
        if count <= 0:
            continue
        records.append(
            RuntimeMetricRecord(
                metricName=f"{_METRIC_PREFIX}.{label}",
                value=float(count),
                unit="count",
                traceDigest=trace_digest,
                policySnapshotDigest=policy_snapshot_digest,
                dimensions={"outcome": label},
            )
        )
    return tuple(records)


__all__ = [
    "SCHEDULER_OUTCOME_COUNTER_LABELS",
    "build_scheduler_outcome_counters",
    "increment_scheduler_counter",
    "scheduler_counts_to_metric_records",
]
