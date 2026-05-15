#!/bin/bash
set -euo pipefail
OCBASE="/home/ocuser/.openclaw"

echo "=== Active Agents ==="
RAW=$(openclaw agents list --json 2>/dev/null)
echo "${RAW}" | python3 -c '
import json,sys
raw = sys.stdin.read()
start = raw.find("["); end = raw.rfind("]") + 1
if start == -1: print("  (no agents found)"); sys.exit(0)
for a in json.loads(raw[start:end]):
    d = " (default)" if a.get("isDefault") else ""
    print(f"  {a.get(\"id\",\"?\"):20s} model={a.get(\"model\",\"?\"):30s}{d}")
' || echo "  (error reading agents)"

echo ""
echo "=== Agent Statistics (from AGENT-REGISTRY.md) ==="
REGISTRY="${OCBASE}/agents/main/workspace/AGENT-REGISTRY.md"
if [ -f "${REGISTRY}" ]; then
  grep -E "^\*\*(Last Used|Turn Count):" "${REGISTRY}" 2>/dev/null || echo "  (no stats found)"
else
  echo "  (registry not found)"
fi

echo ""
echo "=== Archived Agents ==="
ARCHIVE_DIR="${OCBASE}/archive"
if [ -d "${ARCHIVE_DIR}" ] && ls "${ARCHIVE_DIR}"/*.tar.gz >/dev/null 2>&1; then
  ls -1t "${ARCHIVE_DIR}"/*.tar.gz | while read f; do
    SIZE=$(du -h "$f" | cut -f1); echo "  $(basename "$f" .tar.gz)  (${SIZE})"
  done
else echo "  (none)"; fi
