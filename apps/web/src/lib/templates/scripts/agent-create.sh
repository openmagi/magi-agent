#!/bin/bash
set -euo pipefail
NAME="${1:?Usage: agent-create.sh <name> <bootstrap-file>}"
BOOTSTRAP="${2:?Usage: agent-create.sh <name> <bootstrap-file>}"
OCBASE="/home/ocuser/.openclaw"
WORKSPACE="${OCBASE}/specialists/${NAME}"
TEMPLATES="${OCBASE}/agents-shared/templates"

if [ -d "${WORKSPACE}" ]; then
  echo "ERROR: Workspace ${WORKSPACE} already exists"; exit 1
fi

# Create directory structure
mkdir -p "${WORKSPACE}/memory" "${WORKSPACE}/plans" "${WORKSPACE}/skills" "${WORKSPACE}/knowledge"

# Role-specific file (the ONLY unique file per specialist)
cp "${BOOTSTRAP}" "${WORKSPACE}/BOOTSTRAP.md"
# Replace {{BOT_NAME}} placeholder with actual agent name
sed -i "s/{{BOT_NAME}}/${NAME}/g" "${WORKSPACE}/BOOTSTRAP.md"

# System prompt templates (with placeholder replacement)
cp "${TEMPLATES}/AGENTS.md"    "${WORKSPACE}/AGENTS.md"
cp "${TEMPLATES}/HEARTBEAT.md" "${WORKSPACE}/HEARTBEAT.md"
cp "${TEMPLATES}/TOOLS.md"     "${WORKSPACE}/TOOLS.md"
sed -i "s/{{BOT_NAME}}/${NAME}/g" "${WORKSPACE}/AGENTS.md"
sed -i "s/{{BOT_NAME}}/${NAME}/g" "${WORKSPACE}/HEARTBEAT.md"
sed -i "s/{{BOT_NAME}}/${NAME}/g" "${WORKSPACE}/TOOLS.md"

# Replace {{BOT_NAME}} in skill files
find "${WORKSPACE}/skills" -name "*.md" -exec sed -i "s/{{BOT_NAME}}/${NAME}/g" {} + 2>/dev/null || true

# Symlink shared files from main workspace
MAIN_WS="${OCBASE}/agents/main/workspace"
if [ -f "${MAIN_WS}/SOUL.md" ]; then
  ln -sf "${MAIN_WS}/SOUL.md" "${WORKSPACE}/SOUL.md"
fi

# Knowledge
if [ -d "${TEMPLATES}/knowledge" ]; then
  cp "${TEMPLATES}/knowledge/"* "${WORKSPACE}/knowledge/" 2>/dev/null || true
fi

# Skills (copy all default skills)
if [ -d "${TEMPLATES}/skills" ]; then
  for skill_dir in "${TEMPLATES}/skills"/*/; do
    [ -d "${skill_dir}" ] || continue
    skill_name=$(basename "${skill_dir}")
    cp -r "${skill_dir}" "${WORKSPACE}/skills/${skill_name}"
  done
fi

# Initialize memory hierarchy
cat > "${WORKSPACE}/memory/WORKING.md" << 'MDEOF'
# WORKING.md — Active Tasks
(no active tasks)
MDEOF

cat > "${WORKSPACE}/SCRATCHPAD.md" << 'MDEOF'
# SCRATCHPAD
## Global
### Lessons
(none yet)
MDEOF

cat > "${WORKSPACE}/MEMORY.md" << 'MDEOF'
# MEMORY.md — Long-term Memory
(none yet)
MDEOF

cat > "${WORKSPACE}/LESSONS.md" << 'MDEOF'
# LESSONS.md — Domain Knowledge

## Patterns
(none yet)

## Gotchas
(none yet)

## References
(none yet)
MDEOF

# Copy auth profiles from main agent
mkdir -p "${OCBASE}/agents/${NAME}/agent"
if [ -f "${OCBASE}/agents/main/agent/auth-profiles.json" ]; then
  cp "${OCBASE}/agents/main/agent/auth-profiles.json" \
     "${OCBASE}/agents/${NAME}/agent/auth-profiles.json"
fi

# Register with OpenClaw — use SUBAGENT_MODEL env var (set by provisioning)
# Falls back to sonnet for quality specialist work
AGENT_MODEL="${SUBAGENT_MODEL:-anthropic/claude-sonnet-4-6}"
openclaw agents add "${NAME}" \
  --workspace "${WORKSPACE}" \
  --model "${AGENT_MODEL}" \
  --non-interactive 2>&1

# Initialize qmd index for this agent
qmd --index "${NAME}" collection add "${WORKSPACE}" --name workspace 2>/dev/null || true
qmd --index "${NAME}" update 2>/dev/null || echo "WARN: qmd index creation skipped"

echo "[$(date)] Agent '${NAME}' created. Workspace: ${WORKSPACE}"
