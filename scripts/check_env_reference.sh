#!/usr/bin/env bash
# env-reference drift gate (15-flag-governance.md PR4 / D4).
#
# Regenerates the flag inventory from magi_agent/config/flags.py and fails when
# the committed docs/env-reference.md generated section is stale relative to the
# registry. A new public flag registered without regenerating the doc fails CI.
#
# Mirrors scripts/check_module_map.sh. The drift logic itself lives in the
# generator's --check mode (scripts/generate_env_reference.py).
#
# Usage (locally and in CI):
#     bash scripts/check_env_reference.sh
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

if ! uv run --no-sync python scripts/generate_env_reference.py --check; then
    cat >&2 <<'EOF'

env-reference drift gate FAILED: docs/env-reference.md is stale relative to the
FLAGS registry in magi_agent/config/flags.py.
Regenerate it and commit the result:

    uv run python scripts/generate_env_reference.py
EOF
    exit 1
fi

echo "env-reference up to date"
