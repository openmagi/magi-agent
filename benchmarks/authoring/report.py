"""Run report emitter: summary.json + report.md (design §6.5 and §11.3).

Computes metrics M1..M7, groups failures by code, renders a triage report
(section 11.3). Optionally renders T3 advisory judge annotations in a clearly
boxed NON-GATING section.

M1  completion_rate          — scenarios reaching expect_ready-consistent terminal state / total
M2  turns_to_ready           — distribution + per-archetype median
M3  dead_end_rate            — scenarios where oracle expected ready but flow hit budget without it
M4  question_loop_rate       — same question id repeated after an answer with no progress
M5  forbidden_string_hits    — count of I5 violations (also hard failures)
M6  containment_violations   — count of I2/I4/I6 violations
M7  persisted_oracle_failures — by assertion code
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from benchmarks.authoring.runner import RunResult


# ---------------------------------------------------------------------------
# Summary data types
# ---------------------------------------------------------------------------


@dataclass
class RunSummary:
    total: int = 0
    passed: int = 0
    failed: int = 0
    # M1..M7
    M1_completion_rate: float = 0.0
    M2_turns_to_ready: dict[str, Any] = field(default_factory=dict)
    M3_dead_end_rate: float = 0.0
    M4_question_loop_rate: float = 0.0
    M5_forbidden_string_hits: int = 0
    M6_containment_violations: int = 0
    M7_persisted_oracle_failures: dict[str, int] = field(default_factory=dict)
    failures_by_code: dict[str, list[str]] = field(default_factory=dict)
    budget_stopped: bool = False


# ---------------------------------------------------------------------------
# Metric computation
# ---------------------------------------------------------------------------


def _turns_to_ready_stats(results: list[RunResult]) -> dict[str, Any]:
    ready_turns = [r.reached_ready_at for r in results if r.reached_ready_at is not None]
    if not ready_turns:
        return {"count": 0, "min": None, "max": None, "median": None, "mean": None}
    sorted_turns = sorted(ready_turns)
    n = len(sorted_turns)
    median = (
        sorted_turns[n // 2] if n % 2 == 1
        else (sorted_turns[n // 2 - 1] + sorted_turns[n // 2]) / 2
    )
    return {
        "count": n,
        "min": sorted_turns[0],
        "max": sorted_turns[-1],
        "median": median,
        "mean": round(sum(ready_turns) / n, 2),
    }


def _classify_failure_code(fd: dict[str, Any]) -> str:
    """Return the primary code from a first_divergence dict."""
    if "invariant" in fd:
        return f"invariant:{fd['invariant']}"
    if "oracle" in fd:
        return f"oracle:{fd['oracle']}"
    return f"code:{fd.get('code', 'unknown')}"


def _count_by_invariant_prefix(
    results: list[RunResult], prefix: str
) -> int:
    count = 0
    for r in results:
        if not r.passed and r.first_divergence:
            if r.first_divergence.get("invariant", "").startswith(prefix):
                count += 1
    return count


def compute_summary(
    results: list[RunResult],
    *,
    budget_stopped: bool = False,
) -> RunSummary:
    """Compute M1..M7 and failure groupings from a list of run results."""
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    failed = total - passed

    # M1 completion rate
    m1 = passed / total if total else 0.0

    # M2 turns-to-ready distribution
    m2 = _turns_to_ready_stats(results)

    # M3 dead-end rate: expected ready but never reached
    dead_ends = sum(
        1 for r in results
        if not r.passed
        and r.first_divergence
        and r.first_divergence.get("oracle") == "expect_ready"
        and "never reached ready" in str(r.first_divergence.get("got", ""))
    )
    m3 = dead_ends / total if total else 0.0

    # M4 question-loop rate
    loops = sum(
        1 for r in results
        if not r.passed
        and r.first_divergence
        and "no_question_loop" in str(r.first_divergence.get("oracle", ""))
    )
    m4 = loops / total if total else 0.0

    # M5 forbidden-string hits (I5 violations)
    m5 = sum(
        1 for r in results
        if not r.passed
        and r.first_divergence
        and r.first_divergence.get("invariant") == "I5"
    )

    # M6 containment violations (I2/I4/I6)
    m6 = sum(
        1 for r in results
        if not r.passed
        and r.first_divergence
        and r.first_divergence.get("invariant", "") in ("I2", "I4", "I6")
    )

    # M7 persisted-oracle failures by code
    m7: dict[str, int] = defaultdict(int)
    for r in results:
        if not r.passed and r.first_divergence:
            oracle = r.first_divergence.get("oracle", "")
            if oracle and not oracle.startswith("expect_ready"):
                m7[oracle] += 1

    # Failure grouping by code
    by_code: dict[str, list[str]] = defaultdict(list)
    for r in results:
        if not r.passed and r.first_divergence:
            code = _classify_failure_code(r.first_divergence)
            by_code[code].append(r.scenario_id)

    return RunSummary(
        total=total,
        passed=passed,
        failed=failed,
        M1_completion_rate=round(m1, 4),
        M2_turns_to_ready=m2,
        M3_dead_end_rate=round(m3, 4),
        M4_question_loop_rate=round(m4, 4),
        M5_forbidden_string_hits=m5,
        M6_containment_violations=m6,
        M7_persisted_oracle_failures=dict(m7),
        failures_by_code=dict(by_code),
        budget_stopped=budget_stopped,
    )


# ---------------------------------------------------------------------------
# Report writers
# ---------------------------------------------------------------------------


def write_report(
    run_dir: Path,
    run_results: list[RunResult],
    *,
    tier: str = "t1",
    judge_annotations: dict[str, Any] | None = None,
    env_info: dict[str, Any] | None = None,
    budget_stopped: bool = False,
) -> RunSummary:
    """Write ``summary.json`` and ``report.md`` to ``run_dir``.

    Returns the computed ``RunSummary`` for callers that want programmatic access
    to the metrics (e.g. the run CLI).
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    summary = compute_summary(run_results, budget_stopped=budget_stopped)

    # --- summary.json ---
    summary_dict = asdict(summary)
    summary_dict["tier"] = tier
    summary_dict["env"] = env_info or {}
    summary_dict["generated_at"] = datetime.now(tz=timezone.utc).isoformat()
    (run_dir / "summary.json").write_text(
        json.dumps(summary_dict, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # --- report.md ---
    md = _render_markdown(
        summary=summary,
        results=run_results,
        tier=tier,
        judge_annotations=judge_annotations or {},
        env_info=env_info or {},
    )
    (run_dir / "report.md").write_text(md, encoding="utf-8")

    return summary


def _render_markdown(
    summary: RunSummary,
    results: list[RunResult],
    tier: str,
    judge_annotations: dict[str, Any],
    env_info: dict[str, Any],
) -> str:
    lines: list[str] = []

    # 1. Header (section 11.3 ordering)
    lines.append(f"# Authoring QA Harness Report — Tier {tier.upper()}")
    lines.append("")
    ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines.append(f"Generated: {ts}")
    for k, v in env_info.items():
        lines.append(f"- **{k}**: {v}")
    lines.append("")
    lines.append(
        f"**Total**: {summary.total}  |  "
        f"**Passed**: {summary.passed}  |  "
        f"**Failed**: {summary.failed}"
    )
    if summary.budget_stopped:
        lines.append("")
        lines.append("> WARNING: run stopped early due to `--max-scenarios` or `--budget-usd` limit.")
    lines.append("")

    # 2. Metric table M1..M7
    lines.append("## Metrics")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    m2 = summary.M2_turns_to_ready
    m2_str = f"median={m2.get('median')}, mean={m2.get('mean')}, n={m2.get('count')}"
    lines.append(f"| M1 completion_rate | {summary.M1_completion_rate:.1%} |")
    lines.append(f"| M2 turns_to_ready | {m2_str} |")
    lines.append(f"| M3 dead_end_rate | {summary.M3_dead_end_rate:.1%} |")
    lines.append(f"| M4 question_loop_rate | {summary.M4_question_loop_rate:.1%} |")
    lines.append(f"| M5 forbidden_string_hits | {summary.M5_forbidden_string_hits} |")
    lines.append(f"| M6 containment_violations | {summary.M6_containment_violations} |")
    m7_str = json.dumps(summary.M7_persisted_oracle_failures) if summary.M7_persisted_oracle_failures else "{}"
    lines.append(f"| M7 persisted_oracle_failures | {m7_str} |")
    lines.append("")

    # 3. Failures grouped by code
    if summary.failures_by_code:
        lines.append("## Failures by Code")
        lines.append("")
        for code, scenario_ids in sorted(summary.failures_by_code.items()):
            lines.append(f"### `{code}`")
            lines.append("")
            for sid in scenario_ids:
                r = next((x for x in results if x.scenario_id == sid), None)
                if r and r.first_divergence:
                    fd = r.first_divergence
                    lines.append(
                        f"- **{sid}** turn={fd.get('turn')} "
                        f"expected={fd.get('expected')!r} "
                        f"got={fd.get('got')!r}"
                    )
                    lines.append(
                        f"  Repro: `python -m benchmarks.authoring.run "
                        f"--tier {tier} --only {sid} --out runs/`"
                    )
                else:
                    lines.append(f"- **{sid}**")
            lines.append("")
    else:
        lines.append("## Failures by Code")
        lines.append("")
        lines.append("No failures.")
        lines.append("")

    # 4. Question-loop and dead-end digests
    loops = [
        r for r in results
        if not r.passed
        and r.first_divergence
        and "no_question_loop" in str(r.first_divergence.get("oracle", ""))
    ]
    dead_ends = [
        r for r in results
        if not r.passed
        and r.first_divergence
        and "never reached ready" in str(r.first_divergence.get("got", ""))
    ]
    if loops or dead_ends:
        lines.append("## Question-loop and Dead-end Digests")
        lines.append("")
        if loops:
            lines.append(f"**Question loops** ({len(loops)}):")
            for r in loops:
                lines.append(f"- {r.scenario_id}")
            lines.append("")
        if dead_ends:
            lines.append(f"**Dead ends** ({len(dead_ends)}):")
            for r in dead_ends:
                lines.append(f"- {r.scenario_id} (reached {r.turns} turns)")
            lines.append("")

    # 5. T3 advisory judge annotations (clearly boxed NON-GATING)
    if tier == "t3" and judge_annotations:
        lines.append("## Advisory Judge Annotations (T3 Only)")
        lines.append("")
        lines.append("> **NON-GATING**: The following annotations are ADVISORY ONLY.")
        lines.append("> A judge verdict of 'fail' cannot change any test result.")
        lines.append("")
        for sid, ann in sorted(judge_annotations.items()):
            verdict = getattr(ann, "verdict", "unknown")
            confidence = getattr(ann, "confidence", 0.0)
            reasoning = getattr(ann, "reasoning", "")
            suggest_promote = getattr(ann, "suggest_promote", False)
            r = next((x for x in results if x.scenario_id == sid), None)
            det_result = "PASS" if (r and r.passed) else "FAIL"
            lines.append(f"### {sid}")
            lines.append("")
            lines.append(f"- Deterministic oracle: **{det_result}**")
            lines.append(f"- Judge verdict: **{verdict}** (confidence={confidence:.0%})")
            lines.append(f"- Reasoning: {reasoning}")
            if suggest_promote:
                lines.append(
                    f"- Suggest promote: yes — "
                    f"`python -m benchmarks.authoring.run --promote {sid}`"
                )
            lines.append("")

    # 6. Promotion hints for all failed scenarios
    failed_results = [r for r in results if not r.passed]
    if failed_results:
        lines.append("## Promotion Hints")
        lines.append("")
        lines.append(
            "Each failure can be promoted to a regression corpus entry. "
            "Edit the oracle in the emitted YAML before committing."
        )
        lines.append("")
        for r in failed_results:
            lines.append(
                f"- `python -m benchmarks.authoring.run "
                f"--tier {tier} --promote {r.scenario_id} --out runs/`"
            )
        lines.append("")

    return "\n".join(lines)
