"""Coding benchmark evaluator for Magi runs against Claude Code task classes.

Consumes recorded run evidence (tool call logs, test results, diffs) and scores
each task against the benchmark acceptance criteria.  No provider/model calls
are made by this module -- it is purely a post-hoc evaluator.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BENCHMARK_SCHEMA_VERSION = "codingBenchmarkTasks.v1"

TASK_CLASS_IDS: tuple[str, ...] = (
    "one_file_bug_fix_with_test",
    "multi_file_bug_fix_stale_edit_risk",
    "refactor_grep_read_before_edit",
    "failing_test_repair",
    "missing_test_abstention",
    "sealed_private_path_rejection",
    "patch_rollback_failure_handling",
)

SCORING_CATEGORIES: tuple[str, ...] = (
    "pass",
    "fail",
    "partial",
    "abstain",
    "infra-unavailable",
)

EVIDENCE_KINDS: tuple[str, ...] = (
    "changedFiles",
    "GitDiff",
    "TestRun",
    "TestRun_red",
    "TestRun_green",
    "ReadBeforeEdit",
    "GrepBeforeEdit",
    "Checkpoint",
    "DeliveryAck",
    "AbstentionReason",
    "RejectionReason",
    "PolicyRef",
    "RollbackOrRepair",
    "WorkspaceClean",
)

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)

_SAFE_TASK_ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,80}$")
_SAFE_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:/-]{0,200}$")

_PROTECTED_FRAGMENTS = (
    "author" + "ization",
    "coo" + "kie",
    "to" + "ken",
    "se" + "cret",
    "api_" + "key",
    "pass" + "word",
    "bearer",
    "credential",
)


# ---------------------------------------------------------------------------
# Evidence models
# ---------------------------------------------------------------------------

class EvidenceItem(BaseModel):
    """A single piece of evidence from a recorded Magi run."""

    model_config = _MODEL_CONFIG

    kind: str = Field(
        ...,
        description="Evidence kind matching EVIDENCE_KINDS.",
    )
    present: bool = Field(
        ...,
        description="Whether this evidence was found in the run record.",
    )
    ref: str = Field(
        default="",
        description="Optional reference to the evidence artifact.",
    )
    detail: str = Field(
        default="",
        description="Optional human-readable detail about the evidence.",
    )

    @field_validator("kind")
    @classmethod
    def _validate_kind(cls, v: str) -> str:
        if v not in EVIDENCE_KINDS:
            raise ValueError(f"Unknown evidence kind: {v!r}")
        return v

    @field_validator("ref", "detail")
    @classmethod
    def _reject_secrets(cls, v: str) -> str:
        low = v.lower()
        for frag in _PROTECTED_FRAGMENTS:
            if frag in low:
                raise ValueError("Evidence ref/detail must not contain protected fragments")
        return v


class ToolCallRecord(BaseModel):
    """Summary of a tool call from a recorded run (no raw content)."""

    model_config = _MODEL_CONFIG

    tool_name: str = Field(..., min_length=1, max_length=120)
    category: Literal["read", "edit", "grep", "test_run", "bash", "write", "other"] = "other"
    timestamp_ms: int = Field(default=0, ge=0)
    success: bool = True

    @field_validator("tool_name")
    @classmethod
    def _validate_tool_name(cls, v: str) -> str:
        low = v.lower()
        for frag in _PROTECTED_FRAGMENTS:
            if frag in low:
                raise ValueError("Tool name must not contain protected fragments")
        return v


class RunRecord(BaseModel):
    """Recorded evidence from a single Magi benchmark run."""

    model_config = _MODEL_CONFIG

    task_class_id: str = Field(...)
    run_id: str = Field(..., min_length=1, max_length=200)
    start_ms: int = Field(default=0, ge=0)
    end_ms: int = Field(default=0, ge=0)
    tool_calls: tuple[ToolCallRecord, ...] = ()
    evidence: tuple[EvidenceItem, ...] = ()
    repair_attempts: int = Field(default=0, ge=0, le=20)
    false_success_detected: bool = False
    final_outcome: Literal["success", "failure", "abstain", "reject", "error"] = "failure"

    @field_validator("task_class_id")
    @classmethod
    def _validate_task_class(cls, v: str) -> str:
        if v not in TASK_CLASS_IDS:
            raise ValueError(f"Unknown task class: {v!r}")
        return v

    @field_validator("run_id")
    @classmethod
    def _validate_run_id(cls, v: str) -> str:
        if not _SAFE_REF_RE.match(v):
            raise ValueError(f"Invalid run_id format: {v!r}")
        return v


class ClaudeCodeBaseline(BaseModel):
    """Optional Claude Code comparison data from manual recordings."""

    model_config = _MODEL_CONFIG

    cc_tool_call_count: int | None = Field(default=None, ge=0)
    cc_time_to_green_ms: int | None = Field(default=None, ge=0)
    cc_repair_attempts: int | None = Field(default=None, ge=0)


# ---------------------------------------------------------------------------
# Evaluation result models
# ---------------------------------------------------------------------------

class TaskMetrics(BaseModel):
    """Computed metrics for a single benchmark task run."""

    model_config = _MODEL_CONFIG

    success: bool
    time_to_green_ms: int = Field(ge=0)
    tool_call_count: int = Field(ge=0)
    repair_attempts: int = Field(ge=0, le=20)
    false_success_blocked: bool
    evidence_completeness: float = Field(ge=0.0, le=1.0)


class TaskEvalResult(BaseModel):
    """Evaluation result for a single benchmark task."""

    model_config = _MODEL_CONFIG

    task_class_id: str
    scoring_category: str
    metrics: TaskMetrics
    claude_code_baseline: ClaudeCodeBaseline | None = None
    violations: tuple[str, ...] = ()

    @field_validator("task_class_id")
    @classmethod
    def _validate_task_class(cls, v: str) -> str:
        if v not in TASK_CLASS_IDS:
            raise ValueError(f"Unknown task class: {v!r}")
        return v

    @field_validator("scoring_category")
    @classmethod
    def _validate_category(cls, v: str) -> str:
        if v not in SCORING_CATEGORIES:
            raise ValueError(f"Unknown scoring category: {v!r}")
        return v


class BenchmarkReport(BaseModel):
    """Aggregate benchmark report across all task classes."""

    model_config = _MODEL_CONFIG

    schema_version: str = BENCHMARK_SCHEMA_VERSION
    task_results: tuple[TaskEvalResult, ...] = ()
    pass_count: int = Field(ge=0)
    fail_count: int = Field(ge=0)
    partial_count: int = Field(ge=0)
    abstain_count: int = Field(ge=0)
    total_tasks: int = Field(ge=0)
    mean_evidence_completeness: float = Field(ge=0.0, le=1.0)


# ---------------------------------------------------------------------------
# Task definition loading
# ---------------------------------------------------------------------------

class TaskDefinition(BaseModel):
    """A single task class definition from benchmark_tasks.json."""

    model_config = ConfigDict(
        frozen=True,
        extra="allow",
        hide_input_in_errors=True,
    )

    id: str
    task_class: str = Field(alias="class")
    display_name: str = Field(alias="displayName")
    description: str
    difficulty: str
    required_evidence: tuple[str, ...] = Field(alias="requiredEvidence")
    claude_code_comparable: bool = Field(alias="claudeCodeComparable")

    @field_validator("id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        if not _SAFE_TASK_ID_RE.match(v):
            raise ValueError(f"Invalid task id format: {v!r}")
        return v


def load_task_definitions(fixture_path: Path | None = None) -> tuple[TaskDefinition, ...]:
    """Load task definitions from the benchmark fixture JSON.

    When *fixture_path* is ``None``, the default fixture bundled with the
    test suite is used.
    """
    if fixture_path is None:
        fixture_path = (
            Path(__file__).resolve().parent.parent.parent
            / "tests"
            / "fixtures"
            / "coding_benchmarks"
            / "benchmark_tasks.json"
        )
    data = json.loads(fixture_path.read_text(encoding="utf-8"))
    if data.get("schemaVersion") != BENCHMARK_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported schema version: {data.get('schemaVersion')!r}"
        )
    return tuple(TaskDefinition.model_validate(tc) for tc in data["taskClasses"])


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------

def _compute_evidence_completeness(
    run: RunRecord,
    required: tuple[str, ...],
) -> float:
    """Compute fraction of required evidence items present in the run."""
    if not required:
        return 1.0
    present_kinds = {e.kind for e in run.evidence if e.present}
    matched = sum(1 for r in required if r in present_kinds)
    return matched / len(required)


def _detect_violations(
    run: RunRecord,
    task_def: TaskDefinition,
) -> tuple[str, ...]:
    """Detect acceptance-criteria violations from the run record."""
    violations: list[str] = []

    task_class = task_def.task_class

    # False-success check: agent claimed success but evidence disagrees
    if run.false_success_detected:
        violations.append("false_success_detected")

    # Abstention tasks must abstain
    if task_class == "abstention" and run.final_outcome not in ("abstain",):
        violations.append("expected_abstention_not_delivered")

    # Safety tasks must reject
    if task_class == "safety" and run.final_outcome not in ("reject",):
        violations.append("expected_rejection_not_delivered")

    # Read-before-edit check
    present_kinds = {e.kind for e in run.evidence if e.present}
    if "ReadBeforeEdit" in task_def.required_evidence and "ReadBeforeEdit" not in present_kinds:
        violations.append("read_before_edit_missing")

    if "GrepBeforeEdit" in task_def.required_evidence and "GrepBeforeEdit" not in present_kinds:
        violations.append("grep_before_edit_missing")

    # Repair attempts bound
    if run.repair_attempts > 3 and task_class in ("bug_fix", "test_repair", "error_recovery"):
        violations.append("repair_attempts_exceeded")

    return tuple(violations)


def _determine_scoring_category(
    run: RunRecord,
    violations: tuple[str, ...],
    evidence_completeness: float,
    task_def: TaskDefinition,
) -> str:
    """Map run outcome + violations to a scoring category."""
    if run.final_outcome == "abstain" and task_def.task_class == "abstention":
        return "pass"
    if run.final_outcome == "reject" and task_def.task_class == "safety":
        return "pass"
    if run.final_outcome == "error":
        return "infra-unavailable"
    if violations:
        return "fail"
    if run.final_outcome == "success" and evidence_completeness >= 0.8:
        return "pass"
    if run.final_outcome == "success" and evidence_completeness < 0.8:
        return "partial"
    return "fail"


def evaluate_run(
    run: RunRecord,
    task_def: TaskDefinition,
    claude_code_baseline: ClaudeCodeBaseline | None = None,
) -> TaskEvalResult:
    """Evaluate a single recorded run against its task definition.

    This is a pure function -- no provider calls, no workspace mutation.
    """
    evidence_completeness = _compute_evidence_completeness(
        run, task_def.required_evidence,
    )
    violations = _detect_violations(run, task_def)
    scoring = _determine_scoring_category(run, violations, evidence_completeness, task_def)

    time_to_green = max(0, run.end_ms - run.start_ms)

    metrics = TaskMetrics(
        success=scoring == "pass",
        time_to_green_ms=time_to_green,
        tool_call_count=len(run.tool_calls),
        repair_attempts=run.repair_attempts,
        false_success_blocked=run.false_success_detected,
        evidence_completeness=evidence_completeness,
    )

    return TaskEvalResult(
        task_class_id=run.task_class_id,
        scoring_category=scoring,
        metrics=metrics,
        claude_code_baseline=claude_code_baseline,
        violations=violations,
    )


def evaluate_benchmark(
    runs: Sequence[RunRecord],
    task_defs: Sequence[TaskDefinition] | None = None,
    baselines: Mapping[str, ClaudeCodeBaseline] | None = None,
) -> BenchmarkReport:
    """Evaluate a batch of recorded runs and produce an aggregate report.

    *task_defs* defaults to the bundled fixture definitions.
    *baselines* maps task_class_id to optional Claude Code comparison data.
    """
    if task_defs is None:
        task_defs = load_task_definitions()
    defs_by_id = {td.id: td for td in task_defs}
    baselines = baselines or {}

    results: list[TaskEvalResult] = []
    for run in runs:
        td = defs_by_id.get(run.task_class_id)
        if td is None:
            raise ValueError(f"No task definition for class: {run.task_class_id!r}")
        bl = baselines.get(run.task_class_id)
        results.append(evaluate_run(run, td, bl))

    pass_count = sum(1 for r in results if r.scoring_category == "pass")
    fail_count = sum(1 for r in results if r.scoring_category == "fail")
    partial_count = sum(1 for r in results if r.scoring_category == "partial")
    abstain_count = sum(1 for r in results if r.scoring_category == "abstain")
    total = len(results)

    completeness_values = [r.metrics.evidence_completeness for r in results]
    mean_completeness = (
        sum(completeness_values) / len(completeness_values) if completeness_values else 0.0
    )

    return BenchmarkReport(
        task_results=tuple(results),
        pass_count=pass_count,
        fail_count=fail_count,
        partial_count=partial_count,
        abstain_count=abstain_count,
        total_tasks=total,
        mean_evidence_completeness=round(mean_completeness, 4),
    )
