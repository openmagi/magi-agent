# magi-agent SWE-bench harness

Measures the magi-agent runtime on SWE-bench Verified (500).

## Prerequisites
- `ANTHROPIC_API_KEY` exported.
- Docker running (x86_64 recommended).
- `uv pip install -e ".[bench,cli,providers,anthropic]"`

## Quick start
    # 10-instance smoke (plumbing check)
    uv run python -m benchmarks.swebench.run_swebench --run-id smoke10 --limit 10

    # Full baseline (current runtime, no packs)
    uv run python -m benchmarks.swebench.run_swebench --run-id baseline-v1 --max-workers 8

Runs are resumable: re-running the same `--run-id` skips completed instances.
Results land in `benchmarks/swebench/results/<run-id>/`.

## How it works
1. Inference runs INSIDE each official SWE-bench instance image. An isolated
   py3.11 magi venv is mounted read-only at `/opt/magi`; `run_one.sh` resets
   `/testbed` to the base commit, sets `MAGI_PROVIDER`/`MAGI_MODEL`/
   `ANTHROPIC_API_KEY`, runs `magi -p ... --permission-mode bypassPermissions`,
   and captures `git diff` as the prediction.
2. Evaluation hands `predictions.jsonl` to the official
   `swebench.harness.run_evaluation`.
3. `report.summarize` reports resolved % and (optionally) delta vs a baseline.

No magi_agent runtime changes are required: `main` already auto-selects a real
LiteLlm-backed runner with first-party coding tools once a provider env is set.

## Profiles (future phases)
Phase 0 measures the current runtime as-is. Coding-improvement packs (P0-P6) and
the `production` vs `swebench-max` profiles arrive later; see
`docs/plans/2026-06-05-magi-swebench-benchmark-and-coding-packs-design.md` in the
clawy monorepo.
