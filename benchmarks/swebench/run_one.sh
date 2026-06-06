#!/usr/bin/env bash
# benchmarks/swebench/run_one.sh  (executed INSIDE the instance container)
# Env in: BASE_COMMIT, MAGI_BIN, ISSUE_FILE, OUT_PATCH, OUT_LOG,
#         ANTHROPIC_API_KEY, MAGI_BENCH_MODEL (optional), MAGI_TIMEOUT_SECONDS
set -uo pipefail

cd /testbed || { echo "no /testbed" >&2; exit 3; }

# Clean any image-baked test edits; pin to base commit.
git reset --hard "${BASE_COMMIT}" >/dev/null 2>&1
git clean -fdq >/dev/null 2>&1

# Provider config for the EXISTING real runner (cli/providers.py). The runner is
# auto-selected by _build_default_runner once a provider is configured, and it
# roots the coding tools at cwd (=/testbed). No MAGI_USE_REAL_RUNNER flag exists.
export MAGI_PROVIDER=anthropic
export MAGI_MODEL="${MAGI_BENCH_MODEL:-claude-sonnet-4-6}"
# ANTHROPIC_API_KEY is provided via `docker run -e` (see container.py).

# Run the agent. Never let a hang kill the batch — wrap in timeout.
timeout "${MAGI_TIMEOUT_SECONDS:-1800}" \
  "${MAGI_BIN}" -p "$(cat "${ISSUE_FILE}")" \
  --output stream-json \
  --permission-mode bypassPermissions \
  > "${OUT_LOG}" 2>&1
status=$?

# Prediction = working-tree diff vs base commit (excludes untracked test files
# by design; SWE-bench applies its own test_patch at eval time).
git -C /testbed diff "${BASE_COMMIT}" > "${OUT_PATCH}" 2>/dev/null

echo "magi_exit=${status}" >&2
exit 0
