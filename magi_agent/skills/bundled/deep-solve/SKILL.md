---
name: deep-solve
description: Run the DeepSolve verification-and-refinement pipeline on hard, well-posed problems. Triggers - olympiad math, competitive programming, algorithm problems, mathematical proofs, complex analysis problems, deep solve; 올림피아드, 경쟁 프로그래밍, 알고리즘 문제, 수학 증명, 복잡한 분석 문제.
---

# Deep Solve

Use this skill when the user brings a hard, well-posed problem: olympiad-style
mathematics, competitive programming / algorithm tasks, formal proofs, or a
complex analysis question where a single-pass answer is likely to contain
subtle errors. The `DeepSolve` tool runs the verification-and-refinement
pipeline from arXiv 2507.15855: isolated solver / verifier / adjudicator child
agents iterate solve → verify → adjudicate → refine cycles, with ground-truth
test execution when a `test_command` is available, until the result is
accepted or honestly rejected.

## Suggest vs. invoke

This is a heavyweight multi-child pipeline (many child-agent turns per run).

- **Invoke immediately** when the user explicitly asks: `/deep-solve`, "use
  deep solve", "run the verification pipeline", or an equivalent direct
  request.
- **Suggest and confirm first** when YOU detect a matching problem shape
  (olympiad / competitive-programming / proof / complex analysis) but the user
  did not ask for it. Briefly say what DeepSolve does and that it spawns
  multiple child agents, then start only on confirmation.

## How to call the DeepSolve tool

Arguments:

- `problem` (required): the full problem statement, verbatim. Include all
  constraints, input/output formats, and limits.
- `test_command` (strongly recommended for executable problems): a shell
  command that grades the candidate artifact and exits 0 on pass. The artifact
  path is exported as `DEEP_SOLVE_ARTIFACT`. This drives **ground-truth
  acceptance** — execution outranks judgment, so supply it whenever tests or
  a grader exist. `tests` is an accepted alias.
- `domain` (optional): `competitive_programming`, `math_proof`, or
  `general_analysis`. Inferred from the problem when omitted.
- `consecutive_clean_passes` (optional, default 3): for proof/general
  problems, how many consecutive clean verification rounds are required to
  accept.
- `language` (optional, default `python3`): implementation language for
  executable problems.

Example:

```json
{
  "problem": "Given N (1 <= N <= 2*10^5) integers ... print the maximum ...",
  "test_command": "python3 grader.py \"$DEEP_SOLVE_ARTIFACT\"",
  "domain": "competitive_programming"
}
```

## Reading the result (acceptanceBasis)

The result's `acceptanceBasis` is the confidence label — present it honestly:

- `tests_passed` — the artifact passed the supplied `test_command`
  (ground-truth acceptance; the strongest label).
- `n_consecutive_clean` — accepted after N consecutive clean verification
  rounds with no confirmed critical/major findings (judgment-based; strong,
  but not ground truth).
- `rejected` — the pipeline could not converge. The run still returns the
  **best candidate**, labeled unverified, with the open findings listed.
  Present it as an unverified attempt with its known issues — never as a
  verified answer.

## Rules

- **Loop control belongs to the tool.** When the `DeepSolve` tool is
  available, do NOT manually re-implement the pipeline by chaining
  `SpawnAgent` calls (solver/verifier/adjudicator by hand). The tool enforces
  the acceptance gate, fingerprint-dedup convergence, and the refold
  escalation deterministically; a hand-rolled loop will self-exempt.
- If the tool returns a blocked result (disabled, pack removed, child runner
  not attached), relay the reason and hint honestly instead of silently
  falling back to a single-pass answer. You may then offer a normal
  best-effort answer, clearly labeled as unverified.
- Do not inflate confidence: repeat the `acceptanceBasis` label to the user
  when delivering the result.
