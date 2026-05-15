#!/bin/bash
set -euo pipefail
NAME="${1:?Usage: agent-restore.sh <name>}"
OCBASE="/home/ocuser/.openclaw"
ARCHIVE_DIR="${OCBASE}/archive"
WORKSPACE="${OCBASE}/specialists/${NAME}"

ARCHIVE=$(ls -t "${ARCHIVE_DIR}/${NAME}"-*.tar.gz 2>/dev/null | head -1)
if [ -z "${ARCHIVE}" ]; then echo "ERROR: No archive found for '${NAME}'"; exit 1; fi

tar xzf "${ARCHIVE}" -C /

# Re-create symlinks (may have broken during archive)
MAIN_WS="${OCBASE}/agents/main/workspace"
if [ -f "${MAIN_WS}/SOUL.md" ]; then
  ln -sf "${MAIN_WS}/SOUL.md" "${WORKSPACE}/SOUL.md" 2>/dev/null || true
fi

openclaw agents add "${NAME}" \
  --workspace "${WORKSPACE}" \
  --model haiku-router/auto \
  --non-interactive 2>&1

# Restore qmd index
qmd --index "${NAME}" collection add "${WORKSPACE}" --name workspace 2>/dev/null || true
qmd --index "${NAME}" update 2>/dev/null || echo "WARN: qmd index restoration skipped"

echo "[$(date)] Agent '${NAME}' restored from ${ARCHIVE}"
