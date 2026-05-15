#!/bin/bash
set -euo pipefail
NAME="${1:?Usage: agent-archive.sh <name>}"
OCBASE="/home/ocuser/.openclaw"
ARCHIVE_DIR="${OCBASE}/archive"
mkdir -p "${ARCHIVE_DIR}"

if [ "${NAME}" = "main" ]; then echo "ERROR: Cannot archive the main agent"; exit 1; fi

TIMESTAMP=$(date +%Y-%m-%d-%H%M%S)
ARCHIVE="${ARCHIVE_DIR}/${NAME}-${TIMESTAMP}.tar.gz"

# Create archive with error checking
tar czf "${ARCHIVE}" \
  -C / \
  "home/ocuser/.openclaw/agents/${NAME}" \
  "home/ocuser/.openclaw/specialists/${NAME}" \
  2>/dev/null \
  || { echo "ERROR: Archive failed — agent NOT deleted"; exit 1; }

# Verify archive is non-empty
if [ ! -s "${ARCHIVE}" ]; then
  echo "ERROR: Archive is empty — agent NOT deleted"
  exit 1
fi

# Verify archive is not corrupted
tar tzf "${ARCHIVE}" >/dev/null 2>&1 || {
  echo "ERROR: Archive corrupted — agent NOT deleted"
  rm -f "${ARCHIVE}"
  exit 1
}

# Only delete after successful archive verification
openclaw agents delete "${NAME}" --force 2>&1
echo "[$(date)] Agent '${NAME}' archived to ${ARCHIVE}"
