"""Contract tests for the coding benchmark evaluator.

All tests use recorded/mock evidence only -- no provider calls, no workspace
mutation, no live model execution.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from magi_agent.benchmarks.coding_eval import (
    BENCHMARK_SCHEMA_VERSION,
    EVIDENCE_KINDS,
    SCORING_CATEGORIES,
    TASK_CLASS_IDS,
    BenchmarkReport,
    ClaudeCodeBaseline,
    EvidenceItem,
    RunRecord,
    TaskDefinition,
    TaskEvalResult,
    TaskMetrics,
    ToolCallRecord,
    evaluate_benchmark,
    evaluate_run,
    load_task_definitions,
)

# ---------------------------------------------------------------------------
# Fixture path
# ---------------------------------------------------------------------------

FIXTURE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "coding_benchmarks"
    / "benchmark_tasks.json"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_evidence(kinds: list[str], *, present: bool = True) -> tuple[EvidenceItem, ...]:
    return tuple(EvidenceItem(kind=k, present=present) for k in kinds)


def _make_tool_calls(count: int, category: str = "other") -> tuple[ToolCallRecord, ...]:
    return tuple(
        ToolCallRecord(tool_name=f"tool_{i}", category=category)
        for i in range(count)
    )


def _make_run(
    task_class_id: str,
    *,
    outcome: str = "success",
    evidence_kinds: list[str] | None = None,
    tool_count: int = 3,
    repair_attempts: int = 0,
    false_success: bool = False,
    start_ms: int = 0,
    end_ms: int = 1000,
) -> RunRecord:
    evidence = _make_evidence(evidence_kinds or [])
    return RunRecord(
        task_class_id=task_class_id,
        run_id=f"test-run-{task_class_id}",
        start_ms=start_ms,
        end_ms=end_ms,
        tool_calls=_make_tool_calls(tool_count),
        evidence=evidence,
        repair_attempts=repair_attempts,
        false_success_detected=false_success,
        final_outcome=outcome,
    )


# ---------------------------------------------------------------------------
# Fixture loading tests
# ---------------------------------------------------------------------------

class TestFixtureLoading:
    """Tests that the benchmark fixture JSON is valid and loadable."""

    def test_fixture_file_exists(self) -> None:
        assert FIXTURE_PATH.exists(), f"Fixture not found: {FIXTURE_PATH}"

    def test_fixture_json_parseable(self) -> None:
        data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        assert isinstance(data, dict)

    def test_fixture_schema_version(self) -> None:
        data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        assert data["schemaVersion"] == BENCHMARK_SCHEMA_VERSION

    def test_fixture_has_authority_default_off(self) -> None:
        data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        authority = data["authority"]
        assert authority["defaultOff"] is True
        assert authority["localOnly"] is True
        assert authority["liveAuthorityAllowed"] is False
        assert authority["providerModelToolMcpBrowserShellActivationAllowed"] is False

    def test_fixture_has_all_seven_task_classes(self) -> None:
        defs = load_task_definitions(FIXTURE_PATH)
        ids = {d.id for d in defs}
        assert ids == set(TASK_CLASS_IDS)

    def test_each_task_class_has_required_fields(self) -> None:
        defs = load_task_definitions(FIXTURE_PATH)
        for td in defs:
            assert td.id
            assert td.task_class
            assert td.display_name
            assert td.description
            assert td.difficulty
            assert td.required_evidence
            assert td.claude_code_comparable is True

    def test_fixture_metrics_section_present(self) -> None:
        data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        metrics = data["metrics"]
        required_ids = {m["id"] for m in metrics["required"]}
        assert "success" in required_ids
        assert "time_to_green_ms" in required_ids
        assert "tool_call_count" in required_ids
        assert "repair_attempts" in required_ids
        assert "false_success_blocked" in required_ids
        assert "evidence_completeness" in required_ids

    def test_fixture_claude_code_comparison_fields(self) -> None:
        data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        cc_ids = {m["id"] for m in data["metrics"]["claudeCodeComparison"]}
        assert "cc_tool_call_count" in cc_ids
        assert "cc_time_to_green_ms" in cc_ids
        assert "cc_repair_attempts" in cc_ids

    def test_no_raw_file_paths_in_fixture(self) -> None:
        """Fixture must not contain absolute host paths."""
        text = FIXTURE_PATH.read_text(encoding="utf-8")
        assert "/Users/" not in text
        assert "/home/" not in text
        assert "C:\\" not in text


# ---------------------------------------------------------------------------
# Model validation tests
# ---------------------------------------------------------------------------

class TestModelValidation:
    """Tests for pydantic model validation rules."""

    def test_evidence_item_rejects_unknown_kind(self) -> None:
        with pytest.raises(ValueError, match="Unknown evidence kind"):
            EvidenceItem(kind="nonexistent_kind", present=True)

    def test_evidence_item_rejects_secrets_in_ref(self) -> None:
        with pytest.raises(ValueError, match="protected fragments"):
            EvidenceItem(kind="changedFiles", present=True, ref="my-authorization-header")

    def test_tool_call_rejects_secret_name(self) -> None:
        with pytest.raises(ValueError, match="protected fragments"):
            ToolCallRecord(tool_name="get_api_key_value")

    def test_run_record_rejects_unknown_task_class(self) -> None:
        with pytest.raises(ValueError, match="Unknown task class"):
            RunRecord(
                task_class_id="imaginary_task",
                run_id="test-run-1",
                final_outcome="success",
            )

    def test_run_record_rejects_invalid_run_id(self) -> None:
        with pytest.raises(ValueError, match="Invalid run_id"):
            RunRecord(
                task_class_id="failing_test_repair",
                run_id="!!!invalid!!!",
                final_outcome="success",
            )

    def test_task_metrics_rejects_negative_time(self) -> None:
        with pytest.raises(ValueError):
            TaskMetrics(
                success=True,
                time_to_green_ms=-1,
                tool_call_count=3,
                repair_attempts=0,
                false_success_blocked=False,
                evidence_completeness=1.0,
            )

    def test_task_metrics_rejects_completeness_above_one(self) -> None:
        with pytest.raises(ValueError):
            TaskMetrics(
                success=True,
                time_to_green_ms=100,
                tool_call_count=3,
                repair_attempts=0,
                false_success_blocked=False,
                evidence_completeness=1.5,
            )


# ---------------------------------------------------------------------------
# One-file bug fix evaluation
# ---------------------------------------------------------------------------

class TestOneFileBugFix:
    """Evaluate one_file_bug_fix_with_test task class."""

    def test_pass_with_full_evidence(self) -> None:
        run = _make_run(
            "one_file_bug_fix_with_test",
            evidence_kinds=["changedFiles", "GitDiff", "TestRun"],
        )
        defs = load_task_definitions(FIXTURE_PATH)
        td = next(d for d in defs if d.id == "one_file_bug_fix_with_test")
        result = evaluate_run(run, td)
        assert result.scoring_category == "pass"
        assert result.metrics.success is True
        assert result.metrics.evidence_completeness == 1.0

    def test_fail_missing_evidence(self) -> None:
        run = _make_run(
            "one_file_bug_fix_with_test",
            evidence_kinds=["changedFiles"],
        )
        defs = load_task_definitions(FIXTURE_PATH)
        td = next(d for d in defs if d.id == "one_file_bug_fix_with_test")
        result = evaluate_run(run, td)
        assert result.metrics.evidence_completeness < 1.0

    def test_false_success_detected(self) -> None:
        run = _make_run(
            "one_file_bug_fix_with_test",
            evidence_kinds=["changedFiles", "GitDiff", "TestRun"],
            false_success=True,
        )
        defs = load_task_definitions(FIXTURE_PATH)
        td = next(d for d in defs if d.id == "one_file_bug_fix_with_test")
        result = evaluate_run(run, td)
        assert result.scoring_category == "fail"
        assert "false_success_detected" in result.violations


# ---------------------------------------------------------------------------
# Multi-file bug fix evaluation
# ---------------------------------------------------------------------------

class TestMultiFileBugFix:
    """Evaluate multi_file_bug_fix_stale_edit_risk task class."""

    def test_pass_with_read_before_edit(self) -> None:
        run = _make_run(
            "multi_file_bug_fix_stale_edit_risk",
            evidence_kinds=["changedFiles", "GitDiff", "TestRun", "ReadBeforeEdit"],
        )
        defs = load_task_definitions(FIXTURE_PATH)
        td = next(d for d in defs if d.id == "multi_file_bug_fix_stale_edit_risk")
        result = evaluate_run(run, td)
        assert result.scoring_category == "pass"

    def test_violation_missing_read_before_edit(self) -> None:
        run = _make_run(
            "multi_file_bug_fix_stale_edit_risk",
            evidence_kinds=["changedFiles", "GitDiff", "TestRun"],
        )
        defs = load_task_definitions(FIXTURE_PATH)
        td = next(d for d in defs if d.id == "multi_file_bug_fix_stale_edit_risk")
        result = evaluate_run(run, td)
        assert "read_before_edit_missing" in result.violations


# ---------------------------------------------------------------------------
# Refactor with grep evaluation
# ---------------------------------------------------------------------------

class TestRefactorGrepReadBeforeEdit:
    """Evaluate refactor_grep_read_before_edit task class."""

    def test_pass_with_grep_and_read(self) -> None:
        run = _make_run(
            "refactor_grep_read_before_edit",
            evidence_kinds=["changedFiles", "GitDiff", "GrepBeforeEdit", "TestRun"],
        )
        defs = load_task_definitions(FIXTURE_PATH)
        td = next(d for d in defs if d.id == "refactor_grep_read_before_edit")
        result = evaluate_run(run, td)
        assert result.scoring_category == "pass"

    def test_violation_missing_grep(self) -> None:
        run = _make_run(
            "refactor_grep_read_before_edit",
            evidence_kinds=["changedFiles", "GitDiff", "TestRun"],
        )
        defs = load_task_definitions(FIXTURE_PATH)
        td = next(d for d in defs if d.id == "refactor_grep_read_before_edit")
        result = evaluate_run(run, td)
        assert "grep_before_edit_missing" in result.violations


# ---------------------------------------------------------------------------
# Failing test repair evaluation
# ---------------------------------------------------------------------------

class TestFailingTestRepair:
    """Evaluate failing_test_repair task class."""

    def test_pass_red_then_green(self) -> None:
        run = _make_run(
            "failing_test_repair",
            evidence_kinds=["TestRun_red", "changedFiles", "GitDiff", "TestRun_green"],
            repair_attempts=1,
        )
        defs = load_task_definitions(FIXTURE_PATH)
        td = next(d for d in defs if d.id == "failing_test_repair")
        result = evaluate_run(run, td)
        assert result.scoring_category == "pass"
        assert result.metrics.repair_attempts == 1

    def test_violation_excessive_repair_attempts(self) -> None:
        run = _make_run(
            "failing_test_repair",
            evidence_kinds=["TestRun_red", "changedFiles", "GitDiff", "TestRun_green"],
            repair_attempts=5,
        )
        defs = load_task_definitions(FIXTURE_PATH)
        td = next(d for d in defs if d.id == "failing_test_repair")
        result = evaluate_run(run, td)
        assert "repair_attempts_exceeded" in result.violations


# ---------------------------------------------------------------------------
# Missing test abstention evaluation
# ---------------------------------------------------------------------------

class TestMissingTestAbstention:
    """Evaluate missing_test_abstention task class."""

    def test_pass_on_proper_abstention(self) -> None:
        run = _make_run(
            "missing_test_abstention",
            outcome="abstain",
            evidence_kinds=["AbstentionReason"],
        )
        defs = load_task_definitions(FIXTURE_PATH)
        td = next(d for d in defs if d.id == "missing_test_abstention")
        result = evaluate_run(run, td)
        assert result.scoring_category == "pass"

    def test_fail_when_not_abstaining(self) -> None:
        run = _make_run(
            "missing_test_abstention",
            outcome="success",
            evidence_kinds=["AbstentionReason"],
        )
        defs = load_task_definitions(FIXTURE_PATH)
        td = next(d for d in defs if d.id == "missing_test_abstention")
        result = evaluate_run(run, td)
        assert result.scoring_category == "fail"
        assert "expected_abstention_not_delivered" in result.violations


# ---------------------------------------------------------------------------
# Sealed/private path rejection evaluation
# ---------------------------------------------------------------------------

class TestSealedPrivatePathRejection:
    """Evaluate sealed_private_path_rejection task class."""

    def test_pass_on_proper_rejection(self) -> None:
        run = _make_run(
            "sealed_private_path_rejection",
            outcome="reject",
            evidence_kinds=["RejectionReason", "PolicyRef"],
        )
        defs = load_task_definitions(FIXTURE_PATH)
        td = next(d for d in defs if d.id == "sealed_private_path_rejection")
        result = evaluate_run(run, td)
        assert result.scoring_category == "pass"

    def test_fail_when_not_rejecting(self) -> None:
        run = _make_run(
            "sealed_private_path_rejection",
            outcome="success",
            evidence_kinds=["RejectionReason", "PolicyRef"],
        )
        defs = load_task_definitions(FIXTURE_PATH)
        td = next(d for d in defs if d.id == "sealed_private_path_rejection")
        result = evaluate_run(run, td)
        assert result.scoring_category == "fail"
        assert "expected_rejection_not_delivered" in result.violations


# ---------------------------------------------------------------------------
# Patch rollback evaluation
# ---------------------------------------------------------------------------

class TestPatchRollbackFailureHandling:
    """Evaluate patch_rollback_failure_handling task class."""

    def test_pass_with_rollback_evidence(self) -> None:
        run = _make_run(
            "patch_rollback_failure_handling",
            evidence_kinds=["TestRun_red", "RollbackOrRepair", "TestRun_green", "WorkspaceClean"],
            repair_attempts=1,
        )
        defs = load_task_definitions(FIXTURE_PATH)
        td = next(d for d in defs if d.id == "patch_rollback_failure_handling")
        result = evaluate_run(run, td)
        assert result.scoring_category == "pass"

    def test_fail_on_error_outcome(self) -> None:
        run = _make_run(
            "patch_rollback_failure_handling",
            outcome="error",
            evidence_kinds=[],
        )
        defs = load_task_definitions(FIXTURE_PATH)
        td = next(d for d in defs if d.id == "patch_rollback_failure_handling")
        result = evaluate_run(run, td)
        assert result.scoring_category == "infra-unavailable"


# ---------------------------------------------------------------------------
# Aggregate benchmark evaluation
# ---------------------------------------------------------------------------

class TestBenchmarkReport:
    """Tests for the aggregate benchmark report."""

    def test_evaluate_benchmark_all_pass(self) -> None:
        defs = load_task_definitions(FIXTURE_PATH)
        runs = []
        for td in defs:
            ev_kinds = list(td.required_evidence)
            outcome = "success"
            if td.task_class == "abstention":
                outcome = "abstain"
            elif td.task_class == "safety":
                outcome = "reject"
            runs.append(_make_run(td.id, outcome=outcome, evidence_kinds=ev_kinds))

        report = evaluate_benchmark(runs, defs)
        assert report.total_tasks == 7
        assert report.pass_count == 7
        assert report.fail_count == 0
        assert report.mean_evidence_completeness == 1.0

    def test_evaluate_benchmark_mixed(self) -> None:
        defs = load_task_definitions(FIXTURE_PATH)
        td_bug = next(d for d in defs if d.id == "one_file_bug_fix_with_test")
        td_abs = next(d for d in defs if d.id == "missing_test_abstention")

        runs = [
            _make_run("one_file_bug_fix_with_test", evidence_kinds=["changedFiles", "GitDiff", "TestRun"]),
            _make_run("missing_test_abstention", outcome="success", evidence_kinds=[]),
        ]
        report = evaluate_benchmark(runs, defs)
        assert report.total_tasks == 2
        assert report.pass_count == 1
        assert report.fail_count == 1

    def test_claude_code_baseline_passthrough(self) -> None:
        defs = load_task_definitions(FIXTURE_PATH)
        td = next(d for d in defs if d.id == "one_file_bug_fix_with_test")
        baseline = ClaudeCodeBaseline(cc_tool_call_count=4, cc_time_to_green_ms=2000)
        run = _make_run("one_file_bug_fix_with_test", evidence_kinds=["changedFiles", "GitDiff", "TestRun"])

        result = evaluate_run(run, td, baseline)
        assert result.claude_code_baseline is not None
        assert result.claude_code_baseline.cc_tool_call_count == 4

    def test_report_schema_version(self) -> None:
        report = evaluate_benchmark([], load_task_definitions(FIXTURE_PATH))
        assert report.schema_version == BENCHMARK_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Metrics computation tests
# ---------------------------------------------------------------------------

class TestMetricsComputation:
    """Tests for specific metric computation logic."""

    def test_time_to_green_from_timestamps(self) -> None:
        run = _make_run(
            "one_file_bug_fix_with_test",
            evidence_kinds=["changedFiles", "GitDiff", "TestRun"],
            start_ms=1000,
            end_ms=5500,
        )
        defs = load_task_definitions(FIXTURE_PATH)
        td = next(d for d in defs if d.id == "one_file_bug_fix_with_test")
        result = evaluate_run(run, td)
        assert result.metrics.time_to_green_ms == 4500

    def test_tool_call_count(self) -> None:
        run = _make_run(
            "one_file_bug_fix_with_test",
            evidence_kinds=["changedFiles", "GitDiff", "TestRun"],
            tool_count=7,
        )
        defs = load_task_definitions(FIXTURE_PATH)
        td = next(d for d in defs if d.id == "one_file_bug_fix_with_test")
        result = evaluate_run(run, td)
        assert result.metrics.tool_call_count == 7

    def test_partial_evidence_completeness(self) -> None:
        # Only 2 of 3 required evidence items
        run = _make_run(
            "one_file_bug_fix_with_test",
            evidence_kinds=["changedFiles", "GitDiff"],
        )
        defs = load_task_definitions(FIXTURE_PATH)
        td = next(d for d in defs if d.id == "one_file_bug_fix_with_test")
        result = evaluate_run(run, td)
        assert abs(result.metrics.evidence_completeness - 2.0 / 3.0) < 0.01

    def test_evidence_present_false_not_counted(self) -> None:
        evidence = (
            EvidenceItem(kind="changedFiles", present=True),
            EvidenceItem(kind="GitDiff", present=False),
            EvidenceItem(kind="TestRun", present=True),
        )
        run = RunRecord(
            task_class_id="one_file_bug_fix_with_test",
            run_id="test-explicit-evidence",
            evidence=evidence,
            final_outcome="success",
        )
        defs = load_task_definitions(FIXTURE_PATH)
        td = next(d for d in defs if d.id == "one_file_bug_fix_with_test")
        result = evaluate_run(run, td)
        assert abs(result.metrics.evidence_completeness - 2.0 / 3.0) < 0.01
