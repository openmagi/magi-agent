#!/usr/bin/env bash
# Guard: any PR that touches apps/web/src/ MUST regenerate the committed
# magi_agent/web_dashboard/ bundle in the same PR. Without this gate, a
# frontend change merges to main without rebuilding the shipped bundle, and
# every wheel built before someone manually runs scripts/build-web-dashboard.sh
# ships stale UI to lab serve users.
#
# Failure mode this catches: F2/F2.5/F4/F5/F6/F6.5 (#921 #930 #937 #938 #939
# #943) all modified apps/web/src/ without rebuilding magi_agent/web_dashboard/.
# Six PRs of stale frontend went into 0.1.79 because no CI gate enforced the
# bundle refresh.
#
# Logic:
#   1. Find the merge base against origin/main (or the PR base ref).
#   2. List files changed between base and HEAD.
#   3. If any path under apps/web/src/ changed AND no path under
#      magi_agent/web_dashboard/ changed, fail with a clear message pointing
#      at scripts/build-web-dashboard.sh.
#
# Allowed exceptions: a PR that touches only apps/web/{*.md, eslint.config.*,
# .gitignore, package.json, tsconfig.json, vitest.*, vite.config.*} without
# changing any rendered source still triggers the gate — false positive cost
# is one extra ~30s build. Acceptable.

set -euo pipefail

BASE_REF="${GITHUB_BASE_REF:-main}"
# In a local invocation, BASE_REF will be 'main' and we compare HEAD to
# origin/main. In a PR run on GitHub Actions, BASE_REF is the target branch
# and actions/checkout@v4 + the fetch flag below makes origin/<base> resolvable.
if ! git rev-parse --verify "origin/$BASE_REF" >/dev/null 2>&1; then
  echo "::warning::origin/$BASE_REF not fetched; gate is a no-op for this run."
  exit 0
fi

MERGE_BASE="$(git merge-base "origin/$BASE_REF" HEAD)"

CHANGED_FILES="$(git diff --name-only "$MERGE_BASE" HEAD)"

if [ -z "$CHANGED_FILES" ]; then
  echo "No changed files vs origin/$BASE_REF — gate skipped."
  exit 0
fi

SRC_CHANGED="$(echo "$CHANGED_FILES" | grep -E '^apps/web/src/' || true)"
BUNDLE_CHANGED="$(echo "$CHANGED_FILES" | grep -E '^magi_agent/web_dashboard/' || true)"

if [ -z "$SRC_CHANGED" ]; then
  echo "No apps/web/src/ changes — gate skipped."
  exit 0
fi

if [ -n "$BUNDLE_CHANGED" ]; then
  echo "apps/web/src/ changed AND magi_agent/web_dashboard/ updated — gate passes."
  exit 0
fi

cat >&2 <<EOF
::error::web_dashboard bundle is stale.

This PR modifies files under apps/web/src/ but does NOT include any changes
under magi_agent/web_dashboard/. The committed bundle is shipped verbatim
inside the published wheel (see pyproject.toml: "web_dashboard/**/*"), so a
frontend source change without a matching bundle regeneration means lab
serve users will run the pre-PR UI even after upgrading.

Fix:
  bash scripts/build-web-dashboard.sh
  git add magi_agent/web_dashboard
  git commit --amend --no-edit   # or a fresh commit; either works

Files changed under apps/web/src/ in this PR:
$(echo "$SRC_CHANGED" | sed 's/^/  - /')

If a frontend change genuinely should NOT produce a bundle delta (rare —
e.g. a test-only file under apps/web/src/ that no production code imports),
narrow the source list above and document the exception in the PR.
EOF

exit 1
