"""Deep-solve orchestrator core (U1) — pure, no network.

Implements the verification-and-refinement pipeline from arXiv 2507.15855
adapted for competitive programming and mathematical proof domains.

Design references:
- docs/plans/2026-07-08-magi-deep-solve-pipeline-design.md (esp. D5-D10, §4)
- docs/plans/2026-07-08-magi-deep-solve-implementation-plan.md (U1 spec)

Pure module: MUST NOT import from magi_agent.runtime, magi_agent.tools,
or magi_agent.transport. All I/O is injected via DeepSolveDeps.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from magi_agent.solving.templates import (
    DOMAIN_TEMPLATES,
    RIGOR_HEADERS,
    DomainTemplate,
    get_template,
)


# ---------------------------------------------------------------------------
# Pydantic config (mirror repo _MODEL_CONFIG from child_runner_boundary.py)
# ---------------------------------------------------------------------------

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
)

# ---------------------------------------------------------------------------
# Types (U1.1)
# ---------------------------------------------------------------------------

ProblemClass = Literal["executable", "proof", "general"]

FindingCategory = Literal[
    # Competitive programming
    "critical_logic",
    "complexity_exceeded",
    "implementation_bug",
    "missed_edge_case",
    # Math proof / general
    "critical_error",
    "justification_gap_major",
    "justification_gap_minor",
]


class Finding(BaseModel):
    """One structured finding from a verifier or adjudicator stage."""

    model_config = _MODEL_CONFIG

    stage: str
    category: FindingCategory
    section_bucket: int | None = None
    severity: Literal["critical", "major", "minor"]
    description: str


class StageResult(BaseModel):
    """Result from one pipeline stage child run."""

    model_config = _MODEL_CONFIG

    stage_id: str
    #: Full raw text from the child — pipeline-internal channel only (B1).
    #: NEVER emit this publicly; public projections use sanitized_summary.
    full_text: str
    sanitized_summary: str
    child_ref: str | None
    agents_spent: int


class ExecutionReport(BaseModel):
    """Result from running test cases against a candidate solution."""

    model_config = _MODEL_CONFIG

    command_digest: str
    total: int
    passed: int
    failed_cases: tuple[str, ...]
    score: float | None
    raw_output: str


class DeepSolveConfig(BaseModel):
    """Configuration for one deep-solve run."""

    model_config = _MODEL_CONFIG

    problem: str
    domain: DomainTemplate | None = None
    problem_class: ProblemClass | None = None
    test_command: str | None = None
    consecutive_clean_passes: int = 3
    fingerprint_budget: int = 64
    language: str = "python3"


class DeepSolveVerdictData(BaseModel):
    """Verdict record emitted to the evidence ledger (digests/refs only — B1)."""

    model_config = _MODEL_CONFIG

    problem_digest: str
    problem_class: ProblemClass
    cycles: int
    refolds: int
    acceptance_basis: Literal["tests_passed", "n_consecutive_clean", "rejected"]
    final_findings_open: tuple[str, ...]
    per_stage_child_refs: tuple[str, ...]


class DeepSolveOutcome(BaseModel):
    """Return value from run_deep_solve."""

    model_config = _MODEL_CONFIG

    acceptance_basis: Literal["tests_passed", "n_consecutive_clean", "rejected"]
    cycles: int
    refolds: int
    final_findings_open: tuple[str, ...]
    best_candidate: str
    reject_reason: str = ""


# ---------------------------------------------------------------------------
# Run state (mutable, not frozen)
# ---------------------------------------------------------------------------

class DeepSolveRunState(BaseModel):
    """Accumulated state across all stages of one run (pipeline-internal)."""

    model_config = ConfigDict(
        frozen=False,
        populate_by_name=True,
        extra="forbid",
    )

    config: DeepSolveConfig
    #: Latest full-text per named stage (pipeline-internal channel — B1).
    stage_results: dict[str, StageResult] = Field(default_factory=dict)
    #: Latest execution report if any.
    execution_results: ExecutionReport | None = None
    #: Run-global fingerprint set — never reset (survives refold).
    fingerprints: set[str] = Field(default_factory=set)
    #: Cumulative agents counter.
    agents_spent: int = 0
    #: Consecutive clean verification passes.
    consecutive_clean: int = 0
    #: Number of refolds performed.
    refolds: int = 0
    #: Number of full loop cycles.
    cycles: int = 0
    #: plateau_streak: 0 = fresh, 1 = one no-progress cycle, 2 = two → reject.
    plateau_streak: int = 0
    #: Open findings from the last cycle.
    current_findings: list[Finding] = Field(default_factory=list)
    #: Collected child refs (for verdict record).
    child_refs: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Dependency seam (U1.2)
# ---------------------------------------------------------------------------

class DeepSolveDeps(Protocol):
    """Injected I/O seam for the orchestrator — all external effects go here."""

    async def run_stage(
        self,
        *,
        stage: str,
        role: str,
        toolset_request: str,
        objective: str,
        agents_spent_so_far: int,
    ) -> StageResult: ...

    async def execute_tests(
        self,
        *,
        artifact: str,
        test_command: str,
    ) -> ExecutionReport: ...

    def emit_progress(self, event: Mapping[str, object]) -> None: ...

    def append_verdict(self, verdict: DeepSolveVerdictData) -> None: ...


# ---------------------------------------------------------------------------
# Fingerprinting (D7 / B4)
# ---------------------------------------------------------------------------

# Categories that require a "critical" or "major" severity level to reset
# the consecutive-clean counter.
_CRITICAL_MAJOR_SEVERITIES = {"critical", "major"}

# Categories counted as critical/major for proof class acceptance gate.
_PROOF_BLOCKING_CATEGORIES: set[FindingCategory] = {
    "critical_error",
    "justification_gap_major",
    "critical_logic",
    "complexity_exceeded",
}


def _compute_fingerprint(finding: Finding, problem_class: ProblemClass) -> str:
    """Compute a run-global fingerprint for a finding (D7 / B4).

    Executable class: (stage, category, min(section_bucket or 0, 31))
    Proof/general class: (stage, category) -- no location component.
    """
    if problem_class == "executable":
        bucket = min(finding.section_bucket or 0, 31)
        key = f"{finding.stage}|{finding.category}|{bucket}"
    else:
        key = f"{finding.stage}|{finding.category}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Findings parsing (A4)
# ---------------------------------------------------------------------------

_FINDINGS_BLOCK_RE = re.compile(
    r"```findings\s*\n(.*?)\n```",
    re.DOTALL,
)

_VALID_CATEGORIES: set[str] = {
    "critical_logic",
    "complexity_exceeded",
    "implementation_bug",
    "missed_edge_case",
    "critical_error",
    "justification_gap_major",
    "justification_gap_minor",
}

_VALID_SEVERITIES: set[str] = {"critical", "major", "minor"}


def _parse_findings(text: str) -> list[Finding] | None:
    """Extract and validate the findings JSON block from stage output.

    Returns a list of Finding objects on success, or None on parse failure.
    Loop control keys ONLY on parsed schema fields (A4 injection posture).
    """
    match = _FINDINGS_BLOCK_RE.search(text)
    if not match:
        return None
    raw = match.group(1).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, list):
        return None
    findings: list[Finding] = []
    for item in data:
        if not isinstance(item, dict):
            return None
        category = item.get("category", "")
        severity = item.get("severity", "")
        if category not in _VALID_CATEGORIES or severity not in _VALID_SEVERITIES:
            continue  # skip invalid findings rather than failing entirely
        try:
            findings.append(
                Finding(
                    stage=str(item.get("stage", "unknown")),
                    category=category,  # type: ignore[arg-type]
                    section_bucket=item.get("section_bucket"),
                    severity=severity,  # type: ignore[arg-type]
                    description=str(item.get("description", "")),
                )
            )
        except Exception:
            continue
    return findings


async def _get_findings_with_retry(
    deps: DeepSolveDeps,
    *,
    stage: str,
    role: str,
    toolset_request: str,
    objective: str,
    agents_spent_so_far: int,
) -> tuple[StageResult, list[Finding]]:
    """Run a stage and parse findings; retry once on parse failure (T9)."""
    result = await deps.run_stage(
        stage=stage,
        role=role,
        toolset_request=toolset_request,
        objective=objective,
        agents_spent_so_far=agents_spent_so_far,
    )
    findings = _parse_findings(result.full_text)
    if findings is None:
        # One retry
        result = await deps.run_stage(
            stage=stage,
            role=role,
            toolset_request=toolset_request,
            objective=objective,
            agents_spent_so_far=agents_spent_so_far + result.agents_spent,
        )
        findings = _parse_findings(result.full_text)
        if findings is None:
            # Treat as zero findings for loop control (raw text still enters trace)
            findings = []
    return result, findings


# ---------------------------------------------------------------------------
# Intake helpers (S0)
# ---------------------------------------------------------------------------

# Heuristic keyword sets for domain template detection
_CP_KEYWORDS = {
    "algorithm", "competitive", "programming", "시간복잡도", "공간복잡도",
    "정올", "올림피아드", "코드포스", "codeforces", "leetcode", "boj",
    "subtask", "sort", "graph", "dp", "dynamic programming",
    "array", "binary search", "greedy", "tree",
}

_MATH_KEYWORDS = {
    "prove", "proof", "theorem", "lemma", "conjecture", "수학", "증명",
    "정리", "보조정리", "추론", "가설", "수론", "대수", "기하",
    "analysis", "convergence", "continuous", "derivative", "integral",
}


def _detect_domain(problem: str) -> DomainTemplate:
    """Heuristic (deterministic) domain detection."""
    lower = problem.lower()
    cp_hits = sum(1 for kw in _CP_KEYWORDS if kw in lower)
    math_hits = sum(1 for kw in _MATH_KEYWORDS if kw in lower)
    if cp_hits > math_hits:
        return "competitive_programming"
    if math_hits > 0:
        return "math_proof"
    return "general_analysis"


def _resolve_intake(config: DeepSolveConfig) -> tuple[ProblemClass, DomainTemplate]:
    """S0 intake: resolve problem_class and domain template."""
    # Problem class
    if config.problem_class is not None:
        problem_class: ProblemClass = config.problem_class
    elif config.test_command is not None:
        problem_class = "executable"
    else:
        problem_class = "proof"

    # Domain template
    if config.domain is not None:
        domain: DomainTemplate = config.domain
    else:
        domain = _detect_domain(config.problem)

    return problem_class, domain


# ---------------------------------------------------------------------------
# Refold assembly (D8)
# ---------------------------------------------------------------------------

def assemble_refold(state: DeepSolveRunState) -> str:
    """Assemble the refold prompt (D8) — deterministic, no model call.

    Produces the exact XML-tagged structure from the design:
      <rigor-header>, bridge line, <problem>, stage results as XML tags,
      <execution-results> (omitted when None).

    Full stage texts are embedded byte-identical (B1 round-trip guarantee).
    """
    _, domain = _resolve_intake(state.config)
    rigor_header = RIGOR_HEADERS.get(domain, RIGOR_HEADERS["general_analysis"])

    parts: list[str] = []

    # Rigor header
    parts.append(f"<rigor-header>\n{rigor_header.strip()}\n</rigor-header>")

    # Bridge line (Korean-primary per design D8)
    parts.append(
        "아래의 사고 과정 및 맥락을 참고하여 "
        "문제를 처음부터 새롭게 풀어주십시오."
    )

    # Problem statement
    parts.append(f"<problem>\n{state.config.problem}\n</problem>")

    # Stage results in canonical order
    stage_tag_map = [
        ("solve", "attempt-1-solution"),
        ("improve", "self-improvement"),
        ("verify", "verification-report"),
        ("adjudicate", "adjudication"),
        ("refine", "refined-solution"),
    ]
    for stage_id, tag in stage_tag_map:
        if stage_id in state.stage_results:
            full_text = state.stage_results[stage_id].full_text
            parts.append(f"<{tag}>\n{full_text}\n</{tag}>")

    # Execution results (omitted when None — D8 design)
    if state.execution_results is not None:
        er = state.execution_results
        exec_text = er.raw_output
        if er.failed_cases:
            exec_text += f"\nFailed cases: {', '.join(er.failed_cases)}"
        if er.score is not None:
            exec_text += f"\nScore: {er.score}"
        parts.append(f"<execution-results>\n{exec_text}\n</execution-results>")

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Progress events helpers
# ---------------------------------------------------------------------------

def _estimated_child_turns() -> int:
    """Static formula for O2 cost estimate: 2 + 3*expected_cycles(=2) + 1 refold."""
    return 2 + 3 * 2 + 1  # = 9


# ---------------------------------------------------------------------------
# Verdict helpers
# ---------------------------------------------------------------------------

def _problem_digest(problem: str) -> str:
    return hashlib.sha256(problem.encode()).hexdigest()[:16]


def _emit_verdict(
    deps: DeepSolveDeps,
    state: DeepSolveRunState,
    problem_class: ProblemClass,
    acceptance_basis: Literal["tests_passed", "n_consecutive_clean", "rejected"],
) -> None:
    """Emit the verdict record exactly once (T10)."""
    open_descriptions = tuple(f.description for f in state.current_findings)
    child_refs = tuple(
        r for r in state.child_refs if r is not None
    )
    verdict = DeepSolveVerdictData(
        problem_digest=_problem_digest(state.config.problem),
        problem_class=problem_class,
        cycles=state.cycles,
        refolds=state.refolds,
        acceptance_basis=acceptance_basis,
        final_findings_open=open_descriptions,
        per_stage_child_refs=child_refs,
    )
    deps.append_verdict(verdict)


# ---------------------------------------------------------------------------
# Stage role / toolset mapping (D5)
# ---------------------------------------------------------------------------

def _stage_role(stage: str) -> str:
    if stage in ("solve", "improve", "refine", "refold"):
        return "coding"
    if stage in ("verify",):
        return "reviewer"
    return "general"


def _stage_toolset(stage: str) -> str:
    """Requested toolset for the stage child (D5 table)."""
    if stage in ("solve", "improve", "refine", "refold"):
        return "readonly"
    return "none"


# ---------------------------------------------------------------------------
# Main orchestrator (U1.3)
# ---------------------------------------------------------------------------

async def run_deep_solve(
    config: DeepSolveConfig,
    deps: DeepSolveDeps,
) -> DeepSolveOutcome:
    """Run the deep-solve pipeline and return an outcome.

    Implements design §4 exactly:
    S0 intake → S1 solve → S2 improve → LOOP{S3 verify → S4 adjudicate →
    S5 refine → S5.5 execute (executable)} → S7 verdict.

    append_verdict is called EXACTLY ONCE on every code path (T10).
    """
    problem_class, domain = _resolve_intake(config)
    state = DeepSolveRunState(config=config)

    # S0 intake: emit first progress event with cost estimate (O2)
    deps.emit_progress({
        "event": "deep_solve_start",
        "stage": "intake",
        "problem_class": problem_class,
        "domain": domain,
        "estimated_child_turns": _estimated_child_turns(),
    })

    # -----------------------------------------------------------------------
    # S1 solve
    # -----------------------------------------------------------------------
    solver_template = get_template(domain, "solver")
    s1_result = await deps.run_stage(
        stage="solve",
        role=_stage_role("solve"),
        toolset_request=_stage_toolset("solve"),
        objective=solver_template.replace("{{problem}}", config.problem),
        agents_spent_so_far=state.agents_spent,
    )
    state.agents_spent += s1_result.agents_spent
    state.stage_results["solve"] = s1_result
    if s1_result.child_ref:
        state.child_refs.append(s1_result.child_ref)

    deps.emit_progress({"event": "deep_solve_stage", "stage": "solve", "agents_spent": state.agents_spent})

    # Agents cap check after S1
    if state.agents_spent > 1000:
        _emit_verdict(deps, state, problem_class, "rejected")
        return DeepSolveOutcome(
            acceptance_basis="rejected",
            cycles=0,
            refolds=0,
            final_findings_open=(),
            best_candidate=s1_result.full_text,
            reject_reason="agents counter exceeded 1000",
        )

    # -----------------------------------------------------------------------
    # S2 improve
    # -----------------------------------------------------------------------
    improver_template = get_template(domain, "improver")
    s2_objective = improver_template.replace(
        "{{previous_solution}}", s1_result.full_text
    )
    s2_result = await deps.run_stage(
        stage="improve",
        role=_stage_role("improve"),
        toolset_request=_stage_toolset("improve"),
        objective=s2_objective,
        agents_spent_so_far=state.agents_spent,
    )
    state.agents_spent += s2_result.agents_spent
    state.stage_results["improve"] = s2_result
    if s2_result.child_ref:
        state.child_refs.append(s2_result.child_ref)

    deps.emit_progress({"event": "deep_solve_stage", "stage": "improve", "agents_spent": state.agents_spent})

    if state.agents_spent > 1000:
        _emit_verdict(deps, state, problem_class, "rejected")
        return DeepSolveOutcome(
            acceptance_basis="rejected",
            cycles=0,
            refolds=0,
            final_findings_open=(),
            best_candidate=s2_result.full_text,
            reject_reason="agents counter exceeded 1000",
        )

    # Keep track of the "best candidate" (latest refined solution for reject path)
    best_candidate = s2_result.full_text

    # -----------------------------------------------------------------------
    # Main loop: LOOP { S3 verify → S4 adjudicate → S5 refine → S5.5 execute }
    # -----------------------------------------------------------------------
    while True:
        state.cycles += 1

        # S3 verify
        verifier_template = get_template(domain, "verifier")
        verify_objective = verifier_template.replace(
            "{{solution}}", best_candidate
        ).replace("{{problem}}", config.problem)
        s3_result, verify_findings = await _get_findings_with_retry(
            deps,
            stage="verify",
            role=_stage_role("verify"),
            toolset_request=_stage_toolset("verify"),
            objective=verify_objective,
            agents_spent_so_far=state.agents_spent,
        )
        state.agents_spent += s3_result.agents_spent
        state.stage_results["verify"] = s3_result
        if s3_result.child_ref:
            state.child_refs.append(s3_result.child_ref)

        deps.emit_progress({
            "event": "deep_solve_stage", "stage": "verify",
            "cycle": state.cycles, "agents_spent": state.agents_spent,
        })

        if state.agents_spent > 1000:
            _emit_verdict(deps, state, problem_class, "rejected")
            return DeepSolveOutcome(
                acceptance_basis="rejected",
                cycles=state.cycles,
                refolds=state.refolds,
                final_findings_open=tuple(f.description for f in state.current_findings),
                best_candidate=best_candidate,
                reject_reason="agents counter exceeded 1000",
            )

        # S4 adjudicate
        adjudicator_template = get_template(domain, "adjudicator")
        adj_objective = adjudicator_template.replace(
            "{{solution}}", best_candidate
        ).replace("{{verification_report}}", s3_result.full_text)
        s4_result, confirmed_findings = await _get_findings_with_retry(
            deps,
            stage="adjudicate",
            role=_stage_role("adjudicate"),
            toolset_request=_stage_toolset("adjudicate"),
            objective=adj_objective,
            agents_spent_so_far=state.agents_spent,
        )
        state.agents_spent += s4_result.agents_spent
        state.stage_results["adjudicate"] = s4_result
        if s4_result.child_ref:
            state.child_refs.append(s4_result.child_ref)

        deps.emit_progress({
            "event": "deep_solve_stage", "stage": "adjudicate",
            "cycle": state.cycles, "agents_spent": state.agents_spent,
        })

        if state.agents_spent > 1000:
            state.current_findings = confirmed_findings
            _emit_verdict(deps, state, problem_class, "rejected")
            return DeepSolveOutcome(
                acceptance_basis="rejected",
                cycles=state.cycles,
                refolds=state.refolds,
                final_findings_open=tuple(f.description for f in confirmed_findings),
                best_candidate=best_candidate,
                reject_reason="agents counter exceeded 1000",
            )

        state.current_findings = confirmed_findings

        # -----------------------------------------------------------------------
        # Fingerprint dedup and no-progress detection (D7 / B4)
        # -----------------------------------------------------------------------
        cycle_fingerprints: set[str] = set()
        for finding in confirmed_findings:
            fp = _compute_fingerprint(finding, problem_class)
            cycle_fingerprints.add(fp)

        new_fps = cycle_fingerprints - state.fingerprints
        all_seen = len(new_fps) == 0 and len(cycle_fingerprints) > 0

        # Add new fingerprints to global set
        state.fingerprints.update(cycle_fingerprints)

        # Fingerprint budget check
        if len(state.fingerprints) > config.fingerprint_budget:
            _emit_verdict(deps, state, problem_class, "rejected")
            return DeepSolveOutcome(
                acceptance_basis="rejected",
                cycles=state.cycles,
                refolds=state.refolds,
                final_findings_open=tuple(f.description for f in confirmed_findings),
                best_candidate=best_candidate,
                reject_reason=f"fingerprint budget exceeded ({len(state.fingerprints)} > {config.fingerprint_budget})",
            )

        # S5 refine (always run, then check acceptance)
        refiner_template = get_template(domain, "solver")
        refine_objective = (
            f"아래의 지적된 문제를 수정하여 개선된 풀이를 제출하십시오.\n\n"
            f"[원래 풀이]\n{best_candidate}\n\n"
            f"[확정된 문제]\n{json.dumps([f.model_dump() for f in confirmed_findings], ensure_ascii=False)}\n\n"
            f"[문제]\n{config.problem}"
        )
        s5_result = await deps.run_stage(
            stage="refine",
            role=_stage_role("refine"),
            toolset_request=_stage_toolset("refine"),
            objective=refine_objective,
            agents_spent_so_far=state.agents_spent,
        )
        state.agents_spent += s5_result.agents_spent
        state.stage_results["refine"] = s5_result
        if s5_result.child_ref:
            state.child_refs.append(s5_result.child_ref)
        best_candidate = s5_result.full_text

        deps.emit_progress({
            "event": "deep_solve_stage", "stage": "refine",
            "cycle": state.cycles, "agents_spent": state.agents_spent,
        })

        if state.agents_spent > 1000:
            _emit_verdict(deps, state, problem_class, "rejected")
            return DeepSolveOutcome(
                acceptance_basis="rejected",
                cycles=state.cycles,
                refolds=state.refolds,
                final_findings_open=tuple(f.description for f in confirmed_findings),
                best_candidate=best_candidate,
                reject_reason="agents counter exceeded 1000",
            )

        # S5.5 execute (executable class only — D5, D6)
        prev_score: float | None = None
        if problem_class == "executable" and config.test_command:
            if state.execution_results is not None:
                prev_score = state.execution_results.score
            exec_report = await deps.execute_tests(
                artifact=best_candidate,
                test_command=config.test_command,
            )
            state.execution_results = exec_report

            deps.emit_progress({
                "event": "deep_solve_stage", "stage": "execute",
                "cycle": state.cycles,
                "total": exec_report.total,
                "passed": exec_report.passed,
                "failed": len(exec_report.failed_cases),
            })

            # Acceptance gate (executable): all tests pass and total > 0 (D6)
            if exec_report.failed_cases == () and exec_report.total > 0:
                _emit_verdict(deps, state, problem_class, "tests_passed")
                return DeepSolveOutcome(
                    acceptance_basis="tests_passed",
                    cycles=state.cycles,
                    refolds=state.refolds,
                    final_findings_open=(),
                    best_candidate=best_candidate,
                )

        # -----------------------------------------------------------------------
        # Acceptance gate (proof class) — D6
        # -----------------------------------------------------------------------
        if problem_class != "executable":
            # Check for critical/major findings that reset the counter
            has_blocking = any(
                f.severity in _CRITICAL_MAJOR_SEVERITIES
                or f.category in _PROOF_BLOCKING_CATEGORIES
                for f in confirmed_findings
            )
            if has_blocking:
                state.consecutive_clean = 0
            elif not confirmed_findings:
                state.consecutive_clean += 1
            # Minor findings don't reset counter but don't increment (per spec: "minor gaps
            # do not reset but are reported")

            if state.consecutive_clean >= config.consecutive_clean_passes:
                _emit_verdict(deps, state, problem_class, "n_consecutive_clean")
                return DeepSolveOutcome(
                    acceptance_basis="n_consecutive_clean",
                    cycles=state.cycles,
                    refolds=state.refolds,
                    final_findings_open=tuple(f.description for f in confirmed_findings),
                    best_candidate=best_candidate,
                )

        # -----------------------------------------------------------------------
        # No-progress detection and escalation ladder (D7, O1)
        # -----------------------------------------------------------------------
        # Determine if this was a no-progress cycle:
        # - All confirmed findings were already seen (fingerprint dedup), OR
        # - Executable: no test-score improvement AND tests not all passing
        is_no_progress = False

        if confirmed_findings and all_seen:
            # All confirmed findings are already-seen fingerprints
            is_no_progress = True
        elif problem_class == "executable" and state.execution_results is not None:
            # Score didn't improve
            curr_score = state.execution_results.score
            if prev_score is not None and curr_score is not None and curr_score <= prev_score:
                if state.execution_results.failed_cases:
                    is_no_progress = True
            elif prev_score is None and curr_score is None and state.execution_results.failed_cases:
                # No score tracking, but still failing
                pass  # Not no-progress by score criterion alone

        if is_no_progress:
            state.plateau_streak += 1
            if state.plateau_streak >= 2:
                # Second consecutive no-progress after refold → terminal REJECT (O1)
                _emit_verdict(deps, state, problem_class, "rejected")
                return DeepSolveOutcome(
                    acceptance_basis="rejected",
                    cycles=state.cycles,
                    refolds=state.refolds,
                    final_findings_open=tuple(f.description for f in confirmed_findings),
                    best_candidate=best_candidate,
                    reject_reason="plateau after refold: two consecutive no-progress cycles",
                )
            elif state.refolds == 0:
                # First no-progress: trigger refold (S6)
                state.refolds += 1
                refold_prompt = assemble_refold(state)
                s6_result = await deps.run_stage(
                    stage="refold",
                    role=_stage_role("refold"),
                    toolset_request=_stage_toolset("refold"),
                    objective=refold_prompt,
                    agents_spent_so_far=state.agents_spent,
                )
                state.agents_spent += s6_result.agents_spent
                # Refold output becomes the new best candidate
                best_candidate = s6_result.full_text
                state.stage_results["refine"] = s6_result  # update for next assemble
                if s6_result.child_ref:
                    state.child_refs.append(s6_result.child_ref)

                deps.emit_progress({
                    "event": "deep_solve_stage", "stage": "refold",
                    "cycle": state.cycles, "agents_spent": state.agents_spent,
                })
                # Reset clean counter post-refold (re-verify from scratch)
                state.consecutive_clean = 0
                # plateau_streak keeps its value (now =1); next no-progress → reject
            else:
                # Already refold-ed, and another no-progress → reject
                state.plateau_streak += 1
                _emit_verdict(deps, state, problem_class, "rejected")
                return DeepSolveOutcome(
                    acceptance_basis="rejected",
                    cycles=state.cycles,
                    refolds=state.refolds,
                    final_findings_open=tuple(f.description for f in confirmed_findings),
                    best_candidate=best_candidate,
                    reject_reason="plateau after refold: no-progress persists post-refold",
                )
        else:
            # Progress was made (new fingerprints or score improved): reset plateau streak
            state.plateau_streak = 0

        if state.agents_spent > 1000:
            _emit_verdict(deps, state, problem_class, "rejected")
            return DeepSolveOutcome(
                acceptance_basis="rejected",
                cycles=state.cycles,
                refolds=state.refolds,
                final_findings_open=tuple(f.description for f in confirmed_findings),
                best_candidate=best_candidate,
                reject_reason="agents counter exceeded 1000",
            )

        # Continue the loop
