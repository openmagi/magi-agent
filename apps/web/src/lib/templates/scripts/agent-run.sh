#!/bin/sh
# Universal subagent runner — any model, full tool access, generous context
# Routes through the bot's router sidecar (Anthropic ↔ OpenAI/Google auto-translated by api-proxy)
#
# Usage: agent-run.sh "prompt"
#        agent-run.sh --model google/gemini-3.1-pro-preview "prompt"
#        agent-run.sh --model openai/gpt-5.5 --max-turns 10 "prompt"
#        agent-run.sh --context file.md "analyze this"
#        agent-run.sh --history "analyze based on recent conversation"
set -e

# ── Activity emit (live tool feed in UI) ──
. "$(dirname "$0")/_activity.sh" 2>/dev/null || true
clawy_activity_emit "agent-run" start
trap 'clawy_activity_emit "agent-run" end' EXIT

# ── Parse flags ──
EXPLICIT_MODEL=""
MAX_TURNS=20
WORKDIR=""
CONTEXT_FILE=""
INCLUDE_HISTORY=false
MAX_OUTPUT_CHARS=10000

while [ $# -gt 1 ]; do
  case "$1" in
    --model)      EXPLICIT_MODEL="$2"; shift 2 ;;
    --max-turns)  MAX_TURNS="$2"; shift 2 ;;
    --workdir)    WORKDIR="$2"; shift 2 ;;
    --context)    CONTEXT_FILE="$2"; shift 2 ;;
    --history)    INCLUDE_HISTORY=true; shift ;;
    --max-output) MAX_OUTPUT_CHARS="$2"; shift 2 ;;
    *) break ;;
  esac
done
PROMPT="$1"

if [ -z "$PROMPT" ]; then
  echo "Usage: agent-run.sh [--model provider/model] [--max-turns N] [--workdir path] [--context file] [--history] [--max-output N] \"prompt\"" >&2
  exit 1
fi

# ── Claude Code CLI path (npm global install, no symlink) ──
CLAUDE_BIN="node /usr/local/lib/node_modules/@anthropic-ai/claude-code/cli.js"

# ── Auto-detect model + router + API key from bot's openclaw.json ──
OC_CONFIG="${HOME}/.openclaw/openclaw.json"
if [ -f "$OC_CONFIG" ]; then
  AUTO_MODEL=$(node -e "
    const c = JSON.parse(require('fs').readFileSync('$OC_CONFIG','utf8'));
    console.log(c?.agents?.defaults?.model?.primary || 'claude-sonnet-4-6');
  " 2>/dev/null || echo "claude-sonnet-4-6")

  PROVIDER_PREFIX=$(echo "$AUTO_MODEL" | cut -d'/' -f1)
  AUTO_BASE_URL=$(node -e "
    const c = JSON.parse(require('fs').readFileSync('$OC_CONFIG','utf8'));
    const p = c?.models?.providers?.['$PROVIDER_PREFIX'] || {};
    console.log(p.baseUrl || '');
  " 2>/dev/null || echo "")

  if [ -z "$ANTHROPIC_API_KEY" ]; then
    ANTHROPIC_API_KEY=$(node -e "
      const c = JSON.parse(require('fs').readFileSync('$OC_CONFIG','utf8'));
      const p = c?.models?.providers?.anthropic || {};
      console.log(p.apiKey || '');
    " 2>/dev/null || echo "")
    export ANTHROPIC_API_KEY
  fi
else
  AUTO_MODEL="claude-sonnet-4-6"
  AUTO_BASE_URL=""
fi

MODEL="${EXPLICIT_MODEL:-$AUTO_MODEL}"

# ── Fallback API key from secrets mount ──
if [ -z "$ANTHROPIC_API_KEY" ]; then
  SECRET_FILE="/run/secrets/bot-secrets/ANTHROPIC_API_KEY"
  if [ -f "$SECRET_FILE" ]; then
    export ANTHROPIC_API_KEY=$(cat "$SECRET_FILE")
  else
    echo "ERROR: ANTHROPIC_API_KEY not found (checked openclaw.json + secrets mount)" >&2
    exit 1
  fi
fi

# ── Base URL: prefer router sidecar, fallback to api-proxy ──
if [ -n "$AUTO_BASE_URL" ]; then
  export ANTHROPIC_BASE_URL="$AUTO_BASE_URL"
else
  export ANTHROPIC_BASE_URL="${ANTHROPIC_BASE_URL:-http://api-proxy.clawy-system.svc.cluster.local:3001}"
fi

export ANTHROPIC_CUSTOM_MODEL_OPTION="$MODEL"
export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1

# ── Working directory ──
if [ -z "$WORKDIR" ]; then
  WORKDIR="/workspace/tasks/$(date +%s)-$$"
fi
mkdir -p "$WORKDIR"

# ── Context injection ──
DEFAULT_CONTEXT_FILE="$WORKDIR/CONTEXT.md"
SUBAGENT_CONTEXT_ROOTS="${AGENT_RUN_CONTEXT_ROOTS:-/workspace/openclaw-home/workspace /home/ocuser/.openclaw/workspace /workspace}"
SUBAGENT_BASELINE_FILES="CLAUDE.md AGENTS.md BOOTSTRAP.md IDENTITY.md USER.md USER-RULES.md MEMORY.md memory/ROOT.md SCRATCHPAD.md WORKING.md LEARNING.md TOOLS.md EXECUTION.md DISCIPLINE.md EXECUTION-TOOLS.md"
export DEFAULT_CONTEXT_FILE SUBAGENT_CONTEXT_ROOTS SUBAGENT_BASELINE_FILES

if ! node <<'NODE' 2>/dev/null
const fs = require("fs");
const path = require("path");

const output = process.env.DEFAULT_CONTEXT_FILE;
const roots = (process.env.SUBAGENT_CONTEXT_ROOTS || "")
  .split(/\s+/)
  .filter(Boolean);
const files = (process.env.SUBAGENT_BASELINE_FILES || "")
  .split(/\s+/)
  .filter(Boolean);

function safeResolve(root, relPath) {
  const resolvedRoot = path.resolve(root);
  const full = path.resolve(resolvedRoot, relPath);
  if (full !== resolvedRoot && !full.startsWith(resolvedRoot + path.sep)) {
    return null;
  }
  return full;
}

function findFile(relPath) {
  for (const root of roots) {
    const full = safeResolve(root, relPath);
    if (!full) continue;
    try {
      if (fs.statSync(full).isFile()) return full;
    } catch {}
  }
  return null;
}

function extractMarkdownSection(markdown, heading) {
  const lines = markdown.split(/\r?\n/);
  const start = lines.findIndex((line) => line.trim() === `## ${heading}`);
  if (start < 0) return null;
  let end = lines.length;
  for (let i = start + 1; i < lines.length; i++) {
    if (/^##\s+/.test(lines[i] || "")) {
      end = i;
      break;
    }
  }
  return lines.slice(start, end).join("\n").trim();
}

function stripMarkdownSections(markdown, shouldStripHeading) {
  const lines = markdown.split(/\r?\n/);
  const kept = [];
  let stripping = false;
  for (const line of lines) {
    const heading = line.match(/^##\s+(.+?)\s*$/);
    if (heading) stripping = shouldStripHeading(heading[1] || "");
    if (!stripping) kept.push(line);
  }
  return kept.join("\n").trim();
}

function renderAgentsForChild(body) {
  const sections = [];
  const hipocampus = body.match(/<!-- hipocampus:protocol:start -->([\s\S]*?)<!-- hipocampus:protocol:end -->/);
  if (hipocampus) sections.push(hipocampus[0].trim());
  for (const heading of ["Runtime Environment", "Temporal Awareness", "File Permissions"]) {
    const section = extractMarkdownSection(body, heading);
    if (section) sections.push(section);
  }
  if (sections.length === 0) {
    if (/(?:meta-layer|Subagent Dispatch|agent-run\.sh|Native SpawnAgent)/i.test(body)) {
      return "<!-- AGENTS.md parent meta-layer content omitted for spawned child execution. -->";
    }
    return body;
  }
  sections.push("<!-- Parent meta-layer dispatch/orchestration sections intentionally omitted for spawned child execution. -->");
  return sections.join("\n\n");
}

function renderExecutionForChild(body) {
  return stripMarkdownSections(body, (heading) => /^(?:Multi-Agent Orchestration)$/i.test(heading));
}

function renderExecutionToolsForChild(body) {
  return stripMarkdownSections(body, (heading) => /^(?:Agent Runner|Coding Agent)\b/i.test(heading));
}

function truncate(body) {
  const max = 12000;
  if (body.length <= max) return body.trim();
  return `${body.slice(0, max).trimEnd()}\n\n[truncated for subagent context]`;
}

const parts = [
  "# Subagent Execution Baseline",
  "",
  "You are a direct execution subagent, not the main meta-layer agent.",
  "Read and follow this file before acting. If a referenced document mentions main/meta-agent orchestration, those parent meta-layer sections are parent-only.",
  "Execute the delegated work directly and return concise evidence, artifact paths, verification results, and blockers.",
];

for (const relPath of files) {
  const full = findFile(relPath);
  if (!full) continue;
  let body = fs.readFileSync(full, "utf8");
  if (relPath === "AGENTS.md") body = renderAgentsForChild(body);
  if (relPath === "EXECUTION.md") body = renderExecutionForChild(body);
  if (relPath === "EXECUTION-TOOLS.md") body = renderExecutionToolsForChild(body);
  if (!body.trim()) continue;
  parts.push("", "---", "", `## ${relPath}`, "", truncate(body));
}

fs.writeFileSync(output, `${parts.join("\n")}\n`);
NODE
then
  printf '%s\n\n%s\n' \
    "# Subagent Execution Baseline" \
    "You are a direct execution subagent. Parent meta-layer sections are parent-only." \
    > "$DEFAULT_CONTEXT_FILE"
fi

if [ -n "$CONTEXT_FILE" ] && [ -f "$CONTEXT_FILE" ]; then
  printf '\n\n---\n\n# Caller Context\n\n' >> "$WORKDIR/CONTEXT.md"
  cat "$CONTEXT_FILE" >> "$WORKDIR/CONTEXT.md"
fi

# ── Conversation history injection ──
if [ "$INCLUDE_HISTORY" = true ]; then
  HISTORY_FILE="$WORKDIR/CONVERSATION_HISTORY.md"
  # Build recent conversation summary from bot's memory files
  node -e "
    const fs = require('fs');
    const parts = [];

    // SCRATCHPAD — current working state
    try {
      const sp = fs.readFileSync('/workspace/openclaw-home/workspace/SCRATCHPAD.md', 'utf8').trim();
      if (sp) parts.push('## Current Working State (SCRATCHPAD)\n' + sp.slice(0, 2000));
    } catch {}

    // WORKING — active tasks
    try {
      const wk = fs.readFileSync('/workspace/openclaw-home/workspace/WORKING.md', 'utf8').trim();
      if (wk) parts.push('## Active Tasks (WORKING)\n' + wk.slice(0, 1500));
    } catch {}

    // Today's memory log
    const today = new Date().toISOString().split('T')[0];
    try {
      const dl = fs.readFileSync('/workspace/openclaw-home/workspace/memory/' + today + '.md', 'utf8').trim();
      if (dl) parts.push('## Today Log\n' + dl.slice(0, 3000));
    } catch {}

    // Yesterday's memory log
    const yesterday = new Date(Date.now() - 86400000).toISOString().split('T')[0];
    try {
      const yl = fs.readFileSync('/workspace/openclaw-home/workspace/memory/' + yesterday + '.md', 'utf8').trim();
      if (yl) parts.push('## Yesterday Log\n' + yl.slice(0, 2000));
    } catch {}

    // ROOT.md — compaction root
    try {
      const root = fs.readFileSync('/workspace/openclaw-home/workspace/memory/ROOT.md', 'utf8').trim();
      if (root) parts.push('## Memory Root\n' + root.slice(0, 2000));
    } catch {}

    if (parts.length > 0) {
      fs.writeFileSync('$HISTORY_FILE', '# Conversation History & Context\n\n' + parts.join('\n\n---\n\n') + '\n');
    }
  " 2>/dev/null || true

  if [ -f "$HISTORY_FILE" ]; then
    # Append to existing CONTEXT.md or create new
    if [ -f "$WORKDIR/CONTEXT.md" ]; then
      printf '\n\n---\n\n' >> "$WORKDIR/CONTEXT.md"
      cat "$HISTORY_FILE" >> "$WORKDIR/CONTEXT.md"
    else
      cp "$HISTORY_FILE" "$WORKDIR/CONTEXT.md"
    fi
  fi
fi

cd "$WORKDIR"

# ── Execute with output capture + truncation ──
OUTPUT_FILE="$WORKDIR/agent-output.txt"
RUN_PROMPT=$(printf 'Read and follow ./CONTEXT.md before acting. You are the direct execution subagent; parent meta-layer sections are parent-only background.\n\nDelegated task:\n%s' "$PROMPT")

$CLAUDE_BIN -p "$RUN_PROMPT" \
  --allowedTools "Bash(curl:*),Bash(sh:*),Bash(node:*),Bash(cat:*),Bash(ls:*),Bash(mkdir:*),Bash(cp:*),Bash(mv:*),Bash(head:*),Bash(tail:*),Bash(grep:*),Bash(wc:*),Bash(sort:*),Bash(jq:*),Bash(integration.sh:*),Bash(web-search.sh:*),Bash(firecrawl.sh:*),Bash(file-send.sh:*),Read,Write,Glob,Grep" \
  --max-turns "$MAX_TURNS" \
  --model "$MODEL" > "$OUTPUT_FILE" 2>&1 || true

# ── Output truncation: if output exceeds MAX_OUTPUT_CHARS, save full + return summary ──
OUTPUT_SIZE=$(wc -c < "$OUTPUT_FILE" 2>/dev/null || echo "0")

if [ "$OUTPUT_SIZE" -gt "$MAX_OUTPUT_CHARS" ]; then
  # Keep full output in file, return truncated + pointer
  echo "=== Subagent Output (truncated — full result: $OUTPUT_FILE) ==="
  head -c "$MAX_OUTPUT_CHARS" "$OUTPUT_FILE"
  echo ""
  echo "..."
  echo "=== Output truncated at ${MAX_OUTPUT_CHARS} chars. Full output (${OUTPUT_SIZE} chars) saved to: $OUTPUT_FILE ==="
  echo "=== Use 'cat $OUTPUT_FILE' to read the complete result ==="
else
  cat "$OUTPUT_FILE"
fi
