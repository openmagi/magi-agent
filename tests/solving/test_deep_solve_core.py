"""Unit tests for magi_agent.solving.deep_solve (U1.5).

All tests use fake deps — no network, no runtime imports.
TDD RED-first: these were written before the module existed.
"""
from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Mapping
from typing import Any

import pytest

from magi_agent.solving.deep_solve import (
    DeepSolveConfig,
    DeepSolveDeps,
    DeepSolveOutcome,
    DeepSolveVerdictData,
    ExecutionReport,
    Finding,
    FindingCategory,
    StageResult,
    assemble_refold,
    run_deep_solve,
)


# ---------------------------------------------------------------------------
# Fake deps helpers
# ---------------------------------------------------------------------------

class _FakeDeps:
    """Minimal fake implementing DeepSolveDeps protocol."""

    def __init__(
        self,
        stage_outputs: list[tuple[str, list[dict[str, Any]]]] | None = None,
        exec_results: list[ExecutionReport] | None = None,
    ) -> None:
        # Each entry = (full_text, findings_list) for successive run_stage calls
        self._stage_outputs: list[tuple[str, list[dict[str, Any]]]] = stage_outputs or []
        self._exec_results: list[ExecutionReport] = exec_results or []
        self._stage_call_idx = 0
        self._exec_call_idx = 0
        self.progress_events: list[dict[str, Any]] = []
        self.verdicts: list[DeepSolveVerdictData] = []
        self.agents_spent_log: list[int] = []

    async def run_stage(
        self,
        *,
        stage: str,
        role: str,
        toolset_request: str,
        objective: str,
        agents_spent_so_far: int,
    ) -> StageResult:
        self.agents_spent_log.append(agents_spent_so_far)
        if self._stage_call_idx >= len(self._stage_outputs):
            # Default: empty findings, no-op full text
            full_text = f"[stage={stage}] default output"
            findings_raw: list[dict[str, Any]] = []
        else:
            full_text, findings_raw = self._stage_outputs[self._stage_call_idx]
        self._stage_call_idx += 1
        # Embed findings as fenced JSON block in the full text
        import json
        findings_json = json.dumps(findings_raw)
        text_with_findings = full_text + f"\n```findings\n{findings_json}\n```"
        return StageResult(
            stage_id=stage,
            full_text=text_with_findings,
            sanitized_summary=full_text[:512],
            child_ref=f"child:{hashlib.sha256(stage.encode()).hexdigest()[:16]}",
            agents_spent=1,
        )

    async def execute_tests(self, *, artifact: str, test_command: str) -> ExecutionReport:
        if self._exec_call_idx >= len(self._exec_results):
            return ExecutionReport(
                command_digest="none",
                total=0,
                passed=0,
                failed_cases=(),
                score=None,
                raw_output="",
            )
        result = self._exec_results[self._exec_call_idx]
        self._exec_call_idx += 1
        return result

    def emit_progress(self, event: Mapping[str, object]) -> None:
        self.progress_events.append(dict(event))

    def append_verdict(self, verdict: DeepSolveVerdictData) -> None:
        self.verdicts.append(verdict)


def _finding(
    category: str = "critical_logic",
    severity: str = "critical",
    section_bucket: int | None = None,
    description: str = "A bug",
    stage: str = "verify",
) -> dict[str, Any]:
    return {
        "stage": stage,
        "category": category,
        "section_bucket": section_bucket,
        "severity": severity,
        "description": description,
    }


def _exec_report(
    total: int = 5,
    failed: tuple[str, ...] = (),
    score: float | None = None,
) -> ExecutionReport:
    return ExecutionReport(
        command_digest="abc123",
        total=total,
        passed=total - len(failed),
        failed_cases=failed,
        score=score,
        raw_output="ok",
    )


def run(coro: Any) -> Any:
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# T1 — Executable acceptance
# ---------------------------------------------------------------------------

def test_executable_fail_then_pass_accepts() -> None:
    """T1a: fail→refine→all-pass accepts with acceptance_basis='tests_passed'."""
    # S1 solve, S2 improve, then loop: S3 verify (finding), S4 adjudicate (confirm),
    # S5 refine, S5.5 execute (fail) → loop: S3 verify (clean), S4 adjudicate (clean),
    # S5 refine, S5.5 execute (pass all) → accept

    finding = _finding(category="critical_logic", severity="critical")

    # Stage sequence: solve, improve, verify(finding), adjudicate(finding confirmed),
    # refine, verify(clean), adjudicate(clean), refine
    stage_outputs = [
        # S1 solve
        ("initial solution code", []),
        # S2 improve
        ("improved solution", []),
        # S3 verify cycle 1 — has findings
        ("verification report", [finding]),
        # S4 adjudicate cycle 1 — confirms findings
        ("adjudication: confirmed", [finding]),
        # S5 refine cycle 1
        ("refined solution", []),
        # S3 verify cycle 2 — clean
        ("verification report clean", []),
        # S4 adjudicate cycle 2 — clean
        ("adjudication: no issues", []),
        # S5 refine cycle 2
        ("refined solution 2", []),
    ]
    exec_results = [
        _exec_report(total=5, failed=("case_1",)),  # cycle 1 — fail
        _exec_report(total=5, failed=()),             # cycle 2 — all pass
    ]
    deps = _FakeDeps(stage_outputs=stage_outputs, exec_results=exec_results)
    config = DeepSolveConfig(
        problem="sort array ascending",
        test_command="python3 -m pytest test_solution.py",
        consecutive_clean_passes=3,
    )
    outcome = run(run_deep_solve(config, deps))
    assert outcome.acceptance_basis == "tests_passed"
    assert len(deps.verdicts) == 1
    assert deps.verdicts[0].acceptance_basis == "tests_passed"


def test_executable_verifier_clean_but_tests_failing_does_not_accept() -> None:
    """T1b: verifier-clean but tests-failing does NOT accept (execution outranks judgment)."""
    # Run exactly 1 cycle: verify clean, adjudicate clean, but tests fail; no more output
    stage_outputs = [
        ("initial solution", []),
        ("improved solution", []),
        # S3 verify — clean
        ("verify clean", []),
        # S4 adjudicate — clean
        ("adjudicate clean", []),
        # S5 refine
        ("refine", []),
        # S3 verify 2nd — clean
        ("verify clean 2", []),
        # S4 adjudicate 2nd — clean
        ("adjudicate clean 2", []),
        # S5 refine 2nd
        ("refine 2", []),
        # S3 verify 3rd — clean
        ("verify clean 3", []),
        # S4 adjudicate 3rd — clean
        ("adjudicate clean 3", []),
        # S5 refine 3rd
        ("refine 3", []),
    ]
    # All exec reports fail
    exec_results = [_exec_report(total=5, failed=("c1",)) for _ in range(10)]
    deps = _FakeDeps(stage_outputs=stage_outputs, exec_results=exec_results)
    config = DeepSolveConfig(
        problem="sort",
        test_command="pytest",
        consecutive_clean_passes=3,
        fingerprint_budget=64,
    )
    outcome = run(run_deep_solve(config, deps))
    # Should NOT accept on tests_passed
    assert outcome.acceptance_basis != "tests_passed"
    assert len(deps.verdicts) == 1


# ---------------------------------------------------------------------------
# T2 — Proof acceptance
# ---------------------------------------------------------------------------

def test_proof_consecutive_clean_accepts() -> None:
    """T2: 3 consecutive clean → accept."""
    stage_outputs = [
        ("solve", []),
        ("improve", []),
        # cycle 1 verify — clean
        ("verify 1", []),
        ("adjudicate 1", []),
        ("refine 1", []),
        # cycle 2 verify — clean
        ("verify 2", []),
        ("adjudicate 2", []),
        ("refine 2", []),
        # cycle 3 verify — clean
        ("verify 3", []),
        ("adjudicate 3", []),
        ("refine 3", []),
    ]
    deps = _FakeDeps(stage_outputs=stage_outputs)
    config = DeepSolveConfig(
        problem="prove the irrationality of sqrt(2)",
        consecutive_clean_passes=3,
    )
    outcome = run(run_deep_solve(config, deps))
    assert outcome.acceptance_basis == "n_consecutive_clean"
    assert len(deps.verdicts) == 1


def test_proof_critical_finding_resets_counter() -> None:
    """T2b: a critical finding at round 2 resets the counter."""
    critical = _finding(category="critical_error", severity="critical")
    stage_outputs = [
        ("solve", []),
        ("improve", []),
        # cycle 1 — clean
        ("verify 1", []),
        ("adjudicate 1", []),
        ("refine 1", []),
        # cycle 2 — critical finding (resets counter to 0)
        ("verify 2", [critical]),
        ("adjudicate 2", [critical]),
        ("refine 2", []),
        # cycle 3 — clean (counter=1)
        ("verify 3", []),
        ("adjudicate 3", []),
        ("refine 3", []),
        # cycle 4 — clean (counter=2)
        ("verify 4", []),
        ("adjudicate 4", []),
        ("refine 4", []),
        # cycle 5 — clean (counter=3) → accept
        ("verify 5", []),
        ("adjudicate 5", []),
        ("refine 5", []),
    ]
    deps = _FakeDeps(stage_outputs=stage_outputs)
    config = DeepSolveConfig(
        problem="prove p=np",
        consecutive_clean_passes=3,
    )
    outcome = run(run_deep_solve(config, deps))
    assert outcome.acceptance_basis == "n_consecutive_clean"


# ---------------------------------------------------------------------------
# T3 — Rephrase-livelock: same fingerprint → no-progress → refold
# ---------------------------------------------------------------------------

def test_rephrase_livelock_triggers_refold() -> None:
    """T3: same (stage,category,bucket) reworded description → same fingerprint → no-progress → refold."""
    # Two cycles with the same fingerprint (same stage/category/bucket, different description)
    finding1 = _finding(category="critical_logic", severity="critical", section_bucket=None, description="bug description A")
    finding1_rephrased = _finding(category="critical_logic", severity="critical", section_bucket=None, description="bug description rephrased differently")

    stage_outputs = [
        ("solve", []),
        ("improve", []),
        # cycle 1
        ("verify", [finding1]),
        ("adjudicate", [finding1]),
        ("refine", []),
        # cycle 2 — same fingerprint, rephrased (no-progress) → should trigger refold
        ("verify", [finding1_rephrased]),
        ("adjudicate", [finding1_rephrased]),
        ("refine", []),
        # S6 refold
        ("refold output", []),
        # post-refold verify — clean → accept after consecutive passes
        ("post-refold verify 1", []),
        ("post-refold adjudicate 1", []),
        ("post-refold refine 1", []),
        ("post-refold verify 2", []),
        ("post-refold adjudicate 2", []),
        ("post-refold refine 2", []),
        ("post-refold verify 3", []),
        ("post-refold adjudicate 3", []),
        ("post-refold refine 3", []),
    ]
    deps = _FakeDeps(stage_outputs=stage_outputs)
    config = DeepSolveConfig(
        problem="prove theorem",
        consecutive_clean_passes=3,
        fingerprint_budget=64,
    )
    outcome = run(run_deep_solve(config, deps))
    # A refold was triggered; outcome may accept or reject depending on post-refold
    assert outcome.refolds >= 1


# ---------------------------------------------------------------------------
# T4 — Refold-then-plateau → reject
# ---------------------------------------------------------------------------

def test_refold_then_plateau_rejects() -> None:
    """T4: refold-then-plateau → reject; verdict 'rejected' with open findings listed."""
    finding = _finding(category="critical_error", severity="critical", description="error A")
    finding_same_fp = _finding(category="critical_error", severity="critical", description="error A rephrased")

    stage_outputs = [
        ("solve", []),
        ("improve", []),
        # cycle 1 — finding
        ("verify", [finding]),
        ("adjudicate", [finding]),
        ("refine", []),
        # cycle 2 — same fingerprint (no-progress → refold)
        ("verify", [finding_same_fp]),
        ("adjudicate", [finding_same_fp]),
        ("refine", []),
        # refold stage
        ("refold", []),
        # post-refold cycle — same fingerprint again (no-progress → reject)
        ("post-refold verify", [finding_same_fp]),
        ("post-refold adjudicate", [finding_same_fp]),
        ("post-refold refine", []),
    ]
    deps = _FakeDeps(stage_outputs=stage_outputs)
    config = DeepSolveConfig(
        problem="prove",
        consecutive_clean_passes=3,
        fingerprint_budget=64,
    )
    outcome = run(run_deep_solve(config, deps))
    assert outcome.acceptance_basis == "rejected"
    assert len(outcome.final_findings_open) > 0
    assert len(deps.verdicts) == 1
    assert deps.verdicts[0].acceptance_basis == "rejected"


# ---------------------------------------------------------------------------
# T5 — Fingerprint budget breach
# ---------------------------------------------------------------------------

def test_fingerprint_budget_breach_rejects() -> None:
    """T5: fingerprint budget breach → reject."""
    # Generate unique findings to exceed budget
    stage_outputs_list: list[tuple[str, list[dict[str, Any]]]] = [
        ("solve", []),
        ("improve", []),
    ]
    budget = 4
    # Each cycle produces 2 new unique findings
    for i in range(budget):
        f1 = _finding(category="critical_logic", severity="critical", section_bucket=i * 2, description=f"unique bug {i*2}")
        f2 = _finding(category="implementation_bug", severity="major", section_bucket=i * 2 + 1, description=f"unique bug {i*2+1}")
        stage_outputs_list.append(("verify", [f1, f2]))
        stage_outputs_list.append(("adjudicate", [f1, f2]))
        stage_outputs_list.append(("refine", []))

    deps = _FakeDeps(stage_outputs=stage_outputs_list)
    config = DeepSolveConfig(
        problem="code",
        test_command="pytest",
        fingerprint_budget=budget,
    )
    exec_results = [_exec_report(total=3, failed=("c1",)) for _ in range(20)]
    deps = _FakeDeps(stage_outputs=stage_outputs_list, exec_results=exec_results)
    outcome = run(run_deep_solve(config, deps))
    assert outcome.acceptance_basis == "rejected"


# ---------------------------------------------------------------------------
# T6 — Run-global dedup across refold
# ---------------------------------------------------------------------------

def test_run_global_dedup_across_refold() -> None:
    """T6: fingerprint seen pre-refold counts as seen post-refold."""
    finding = _finding(category="critical_error", severity="critical", description="error pre-refold")
    same_finding_post = _finding(category="critical_error", severity="critical", description="error post-refold rephrased")

    stage_outputs = [
        ("solve", []),
        ("improve", []),
        # cycle 1 — finding (added to global fingerprint set)
        ("verify", [finding]),
        ("adjudicate", [finding]),
        ("refine", []),
        # cycle 2 — same fingerprint (no-progress → refold)
        ("verify", [finding]),
        ("adjudicate", [finding]),
        ("refine", []),
        # refold
        ("refold", []),
        # post-refold cycle — same fingerprint (still counts as seen → no-progress → reject)
        ("post-refold verify", [same_finding_post]),
        ("post-refold adjudicate", [same_finding_post]),
        ("post-refold refine", []),
    ]
    deps = _FakeDeps(stage_outputs=stage_outputs)
    config = DeepSolveConfig(
        problem="prove",
        consecutive_clean_passes=3,
        fingerprint_budget=64,
    )
    outcome = run(run_deep_solve(config, deps))
    # The post-refold cycle had same fingerprint as pre-refold → no-progress → reject
    assert outcome.acceptance_basis == "rejected"
    assert outcome.refolds == 1


# ---------------------------------------------------------------------------
# T7 — Agents counter threading
# ---------------------------------------------------------------------------

def test_agents_counter_threading() -> None:
    """T7: cumulative agents_spent threaded; >1000 aborts."""
    # A fake deps that tracks calls; we verify agents_spent_so_far increases monotonically
    stage_outputs = [
        ("solve", []),
        ("improve", []),
        ("verify clean", []),
        ("adjudicate clean", []),
        ("refine", []),
        ("verify clean 2", []),
        ("adjudicate clean 2", []),
        ("refine 2", []),
        ("verify clean 3", []),
        ("adjudicate clean 3", []),
        ("refine 3", []),
    ]
    deps = _FakeDeps(stage_outputs=stage_outputs)
    config = DeepSolveConfig(problem="prove", consecutive_clean_passes=3)
    run(run_deep_solve(config, deps))
    # Verify agents_spent_so_far is monotonically non-decreasing across calls
    log = deps.agents_spent_log
    assert len(log) > 0
    for i in range(1, len(log)):
        assert log[i] >= log[i - 1], f"agents counter not monotone at position {i}: {log}"


def test_agents_counter_abort_over_1000() -> None:
    """T7b: >1000 agents cumulative → abort with rejected outcome."""
    call_count = [0]

    class _HighCostDeps(_FakeDeps):
        async def run_stage(self, *, stage: str, role: str, toolset_request: str,
                            objective: str, agents_spent_so_far: int) -> StageResult:
            call_count[0] += 1
            import json
            full_text = f"stage={stage}"
            # No findings — so it would try to accept/pass
            return StageResult(
                stage_id=stage,
                full_text=full_text + "\n```findings\n[]\n```",
                sanitized_summary=full_text[:512],
                child_ref=f"child:{call_count[0]:016x}",
                agents_spent=500,  # Each stage costs 500 agents
            )

    deps = _HighCostDeps()
    config = DeepSolveConfig(problem="prove", consecutive_clean_passes=3)
    outcome = run(run_deep_solve(config, deps))
    # Should have aborted due to >1000 agents
    assert outcome.acceptance_basis == "rejected"
    assert "agent" in outcome.reject_reason.lower()


# ---------------------------------------------------------------------------
# T8 — Refold assembly
# ---------------------------------------------------------------------------

def test_refold_assembly_structure() -> None:
    """T8: exact tag structure; execution-results omitted when None; full stage text byte-identical."""
    from magi_agent.solving.deep_solve import DeepSolveRunState

    # Build a mock run state
    state = DeepSolveRunState(
        config=DeepSolveConfig(problem="test problem"),
        stage_results={
            "solve": StageResult(
                stage_id="solve",
                full_text="def solution():\n    key = \"secret\"\n    # /workspace/data/input.txt\n    return 42",
                sanitized_summary="solution function",
                child_ref="child:abc123",
                agents_spent=1,
            ),
            "improve": StageResult(
                stage_id="improve",
                full_text="improved solution text",
                sanitized_summary="improved",
                child_ref="child:def456",
                agents_spent=1,
            ),
            "verify": StageResult(
                stage_id="verify",
                full_text="verification report",
                sanitized_summary="report",
                child_ref="child:789abc",
                agents_spent=1,
            ),
            "adjudicate": StageResult(
                stage_id="adjudicate",
                full_text="adjudication result",
                sanitized_summary="adj",
                child_ref="child:012def",
                agents_spent=1,
            ),
            "refine": StageResult(
                stage_id="refine",
                full_text="refined output",
                sanitized_summary="refined",
                child_ref="child:345678",
                agents_spent=1,
            ),
        },
        execution_results=None,
    )

    result = assemble_refold(state)

    # Check tag structure
    assert "<rigor-header>" in result
    assert "</rigor-header>" in result
    assert "<problem>" in result
    assert "</problem>" in result
    assert "<attempt-1-solution>" in result
    assert "</attempt-1-solution>" in result
    assert "<self-improvement>" in result
    assert "</self-improvement>" in result
    assert "<verification-report>" in result
    assert "</verification-report>" in result
    assert "<adjudication>" in result
    assert "</adjudication>" in result
    assert "<refined-solution>" in result
    assert "</refined-solution>" in result

    # Execution results omitted when None
    assert "<execution-results>" not in result

    # Full stage text byte-identical (B1 round-trip: key = ... and /workspace/... paths)
    assert 'key = "secret"' in result
    assert "/workspace/data/input.txt" in result


def test_refold_assembly_includes_execution_results() -> None:
    """T8b: execution-results included when present."""
    from magi_agent.solving.deep_solve import DeepSolveRunState

    exec_result = ExecutionReport(
        command_digest="abc",
        total=5,
        passed=3,
        failed_cases=("case_1", "case_2"),
        score=0.6,
        raw_output="subtask 1: TLE\nsubtask 2: WA",
    )

    state = DeepSolveRunState(
        config=DeepSolveConfig(problem="competitive problem"),
        stage_results={
            "solve": StageResult(stage_id="solve", full_text="sol", sanitized_summary="sol", child_ref="child:a", agents_spent=1),
            "improve": StageResult(stage_id="improve", full_text="imp", sanitized_summary="imp", child_ref="child:b", agents_spent=1),
            "verify": StageResult(stage_id="verify", full_text="ver", sanitized_summary="ver", child_ref="child:c", agents_spent=1),
            "adjudicate": StageResult(stage_id="adjudicate", full_text="adj", sanitized_summary="adj", child_ref="child:d", agents_spent=1),
            "refine": StageResult(stage_id="refine", full_text="ref", sanitized_summary="ref", child_ref="child:e", agents_spent=1),
        },
        execution_results=exec_result,
    )

    result = assemble_refold(state)
    assert "<execution-results>" in result
    assert "</execution-results>" in result
    assert "TLE" in result


# ---------------------------------------------------------------------------
# T9 — Findings parse failure
# ---------------------------------------------------------------------------

def test_findings_parse_failure_one_retry_then_zero_findings() -> None:
    """T9: findings parse failure → one retry → zero-findings loop-control path."""
    class _BadFindingsDeps(_FakeDeps):
        def __init__(self) -> None:
            super().__init__()
            self._calls = 0

        async def run_stage(self, *, stage: str, role: str, toolset_request: str,
                            objective: str, agents_spent_so_far: int) -> StageResult:
            self._calls += 1
            self.agents_spent_log.append(agents_spent_so_far)
            # S1/S2 succeed normally
            if stage in ("solve", "improve"):
                return StageResult(
                    stage_id=stage,
                    full_text=f"{stage} output",
                    sanitized_summary=f"{stage}",
                    child_ref=f"child:{stage[:4]}",
                    agents_spent=1,
                )
            # verify stages return malformed JSON (not valid findings list)
            bad_json = f"```findings\nnot valid json!!!\n```"
            return StageResult(
                stage_id=stage,
                full_text=f"{stage} output\n{bad_json}",
                sanitized_summary=f"{stage}",
                child_ref=f"child:{stage[:4]}",
                agents_spent=1,
            )

    deps = _BadFindingsDeps()
    config = DeepSolveConfig(problem="prove", consecutive_clean_passes=3)
    # Should not raise; parse failure → zero findings → consecutive clean pass counting
    outcome = run(run_deep_solve(config, deps))
    # With zero parsed findings, consecutive clean passes will accumulate and accept
    assert outcome.acceptance_basis == "n_consecutive_clean"


# ---------------------------------------------------------------------------
# T10 — Verdict emitted exactly once on every path
# ---------------------------------------------------------------------------

def test_verdict_emitted_exactly_once_on_accept() -> None:
    """T10a: verdict emitted exactly once on accept."""
    stage_outputs = [
        ("solve", []),
        ("improve", []),
        ("verify 1", []),
        ("adjudicate 1", []),
        ("refine 1", []),
        ("verify 2", []),
        ("adjudicate 2", []),
        ("refine 2", []),
        ("verify 3", []),
        ("adjudicate 3", []),
        ("refine 3", []),
    ]
    deps = _FakeDeps(stage_outputs=stage_outputs)
    config = DeepSolveConfig(problem="prove", consecutive_clean_passes=3)
    run(run_deep_solve(config, deps))
    assert len(deps.verdicts) == 1


def test_verdict_emitted_exactly_once_on_reject() -> None:
    """T10b: verdict emitted exactly once on reject."""
    finding = _finding(description="persistent error")
    stage_outputs = [
        ("solve", []),
        ("improve", []),
        # cycle 1
        ("verify", [finding]),
        ("adjudicate", [finding]),
        ("refine", []),
        # cycle 2 — same fp (no-progress → refold)
        ("verify", [finding]),
        ("adjudicate", [finding]),
        ("refine", []),
        # refold
        ("refold", []),
        # post-refold cycle — same fp (no-progress → reject)
        ("post verify", [finding]),
        ("post adjudicate", [finding]),
        ("post refine", []),
    ]
    deps = _FakeDeps(stage_outputs=stage_outputs)
    config = DeepSolveConfig(problem="prove", consecutive_clean_passes=3)
    run(run_deep_solve(config, deps))
    assert len(deps.verdicts) == 1


def test_verdict_contains_digests_only() -> None:
    """T10c: verdict record carries digests only, not full text."""
    stage_outputs = [
        ("solve", []),
        ("improve", []),
        ("verify 1", []),
        ("adjudicate 1", []),
        ("refine 1", []),
        ("verify 2", []),
        ("adjudicate 2", []),
        ("refine 2", []),
        ("verify 3", []),
        ("adjudicate 3", []),
        ("refine 3", []),
    ]
    deps = _FakeDeps(stage_outputs=stage_outputs)
    config = DeepSolveConfig(problem="complex proof text " * 50)  # long problem
    run(run_deep_solve(config, deps))
    verdict = deps.verdicts[0]
    # problem_digest should be a short digest, not the full problem text
    assert len(verdict.problem_digest) < 100
    assert "complex proof text" not in verdict.problem_digest


# ---------------------------------------------------------------------------
# T11 — Import boundaries
# ---------------------------------------------------------------------------

def test_solving_module_import_boundaries() -> None:
    """T11: solving module imports nothing from runtime/tools/transport."""
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys
import importlib
importlib.import_module("magi_agent.solving.deep_solve")
importlib.import_module("magi_agent.solving.templates")

forbidden_prefixes = (
    "magi_agent.runtime",
    "magi_agent.tools",
    "magi_agent.transport",
)
loaded = [
    name for name in sys.modules
    if any(name == p or name.startswith(p + ".") for p in forbidden_prefixes)
]
if loaded:
    raise AssertionError(f"solving module pulled in forbidden modules: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


# ---------------------------------------------------------------------------
# T12 — First progress event has cost estimate
# ---------------------------------------------------------------------------

def test_first_progress_event_has_cost_estimate() -> None:
    """First progress event must include an estimated_child_turns cost line."""
    stage_outputs = [
        ("solve", []),
        ("improve", []),
        ("verify 1", []),
        ("adjudicate 1", []),
        ("refine 1", []),
        ("verify 2", []),
        ("adjudicate 2", []),
        ("refine 2", []),
        ("verify 3", []),
        ("adjudicate 3", []),
        ("refine 3", []),
    ]
    deps = _FakeDeps(stage_outputs=stage_outputs)
    config = DeepSolveConfig(problem="prove", consecutive_clean_passes=3)
    run(run_deep_solve(config, deps))
    assert len(deps.progress_events) > 0
    first_event = deps.progress_events[0]
    assert "estimated_child_turns" in first_event
