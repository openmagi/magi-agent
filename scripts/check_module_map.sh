#!/usr/bin/env bash
# Module-map drift gate (16-module-diet PR10).
#
# Regenerates the module map into a temp file and fails when the committed
# magi_agent/ARCHITECTURE.md is stale relative to the source tree.
#
# Usage (locally and in CI):
#     bash scripts/check_module_map.sh
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT

uv run --no-sync python scripts/generate_module_map.py > "$tmp"

if ! diff -u magi_agent/ARCHITECTURE.md "$tmp"; then
    cat >&2 <<'EOF'

Module-map drift gate FAILED: magi_agent/ARCHITECTURE.md is stale.
Regenerate it and commit the result:

    uv run python scripts/generate_module_map.py > magi_agent/ARCHITECTURE.md
EOF
    exit 1
fi

echo "module map up to date"
