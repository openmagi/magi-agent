#!/bin/bash
set -euo pipefail

# Syncs template updates from shared templates to active specialists
# Syncs: AGENTS.md, HEARTBEAT.md, TOOLS.md, skills/, knowledge/ (additive)
# Does NOT touch: BOOTSTRAP.md, SCRATCHPAD.md, LESSONS.md, memory/, plans/ (agent-specific state)

OCBASE="/home/ocuser/.openclaw"
TEMPLATES="${OCBASE}/agents-shared/templates"
AGENTS_DIR="${OCBASE}/specialists"
DRY_RUN=false
TARGET_AGENT=""

usage() {
  echo "Usage: agent-sync-templates.sh [--dry-run] [agent-name]"
  echo "  --dry-run    Show what would be synced without making changes"
  echo "  agent-name   Sync only this agent (default: all agents)"
  exit 1
}

while [[ $# -gt 0 ]]; do
  case $1 in
    --dry-run) DRY_RUN=true; shift ;;
    -h|--help) usage ;;
    *) TARGET_AGENT="$1"; shift ;;
  esac
done

sync_agent() {
  local name="$1"
  local workspace="${AGENTS_DIR}/${name}"

  if [ ! -d "${workspace}" ]; then
    echo "SKIP: ${name} (workspace not found)"
    return
  fi

  echo "=== Syncing ${name} ==="

  # Sync template files (with placeholder replacement)
  for file in AGENTS.md HEARTBEAT.md TOOLS.md; do
    if [ -f "${TEMPLATES}/${file}" ]; then
      if $DRY_RUN; then
        echo "  [DRY-RUN] Would update ${file}"
      else
        cp "${TEMPLATES}/${file}" "${workspace}/${file}"
        sed -i "s/{{BOT_NAME}}/${name}/g" "${workspace}/${file}"
        echo "  Updated ${file}"
      fi
    fi
  done

  # Sync skills (additive — new skills added, existing skills updated)
  if [ -d "${TEMPLATES}/skills" ]; then
    for skill_dir in "${TEMPLATES}/skills"/*/; do
      [ -d "${skill_dir}" ] || continue
      skill_name=$(basename "${skill_dir}")
      if $DRY_RUN; then
        echo "  [DRY-RUN] Would sync skill: ${skill_name}"
      else
        mkdir -p "${workspace}/skills/${skill_name}"
        cp -r "${skill_dir}"* "${workspace}/skills/${skill_name}/"
        echo "  Synced skill: ${skill_name}"
      fi
    done
    # Replace {{BOT_NAME}} in synced skill files
    if ! $DRY_RUN; then
      find "${workspace}/skills" -name "*.md" -exec sed -i "s/{{BOT_NAME}}/${name}/g" {} + 2>/dev/null || true
    fi
  fi

  # Sync knowledge (additive — new files added, existing files updated)
  if [ -d "${TEMPLATES}/knowledge" ]; then
    for knowledge_file in "${TEMPLATES}/knowledge"/*; do
      [ -f "${knowledge_file}" ] || continue
      filename=$(basename "${knowledge_file}")
      if $DRY_RUN; then
        echo "  [DRY-RUN] Would sync knowledge: ${filename}"
      else
        cp "${knowledge_file}" "${workspace}/knowledge/${filename}"
        echo "  Synced knowledge: ${filename}"
      fi
    done
  fi

  # Re-index qmd
  if ! $DRY_RUN; then
    qmd --index "${name}" collection add "${workspace}" --name workspace 2>/dev/null || true
    qmd --index "${name}" update 2>/dev/null || echo "  WARN: qmd re-index skipped"
  fi

  echo ""
}

if [ -n "${TARGET_AGENT}" ]; then
  sync_agent "${TARGET_AGENT}"
else
  # Sync all specialists
  for agent_dir in "${AGENTS_DIR}"/*/; do
    [ -d "${agent_dir}" ] || continue
    agent_name=$(basename "${agent_dir}")
    sync_agent "${agent_name}"
  done
fi

echo "[$(date)] Template sync complete"
