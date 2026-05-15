#!/bin/sh
# Wrapper for Claude Code CLI — routes through the bot's own router sidecar
# Usage: claude-agent.sh "prompt"
#        claude-agent.sh --model gpt-5.5 "prompt"
set -e

# Parse --model flag if provided (overrides auto-detection)
EXPLICIT_MODEL=""
if [ "$1" = "--model" ]; then
  EXPLICIT_MODEL="$2"
  shift 2
fi

# ── Claude Code CLI path (npm global install, no symlink) ──
CLAUDE_BIN="node /usr/local/lib/node_modules/@anthropic-ai/claude-code/cli.js"

# ── Auto-detect model + router + API key from bot's openclaw.json ──
# Config structure: { models: { providers: { anthropic: { baseUrl, apiKey, models: [...] } } } }
OC_CONFIG="${HOME}/.openclaw/openclaw.json"
if [ -f "$OC_CONFIG" ]; then
  # Extract primary model (e.g. "clawy-smart-router/auto", "anthropic/claude-sonnet-4-6")
  AUTO_MODEL=$(node -e "
    const c = JSON.parse(require('fs').readFileSync('$OC_CONFIG','utf8'));
    console.log(c?.agents?.defaults?.model?.primary || 'claude-sonnet-4-6');
  " 2>/dev/null || echo "claude-sonnet-4-6")

  # Extract provider baseUrl from the model's provider
  PROVIDER_PREFIX=$(echo "$AUTO_MODEL" | cut -d'/' -f1)
  AUTO_BASE_URL=$(node -e "
    const c = JSON.parse(require('fs').readFileSync('$OC_CONFIG','utf8'));
    const p = c?.models?.providers?.['$PROVIDER_PREFIX'] || {};
    console.log(p.baseUrl || '');
  " 2>/dev/null || echo "")

  # Extract gateway token (gw_) from anthropic provider as ANTHROPIC_API_KEY
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

# Use explicit model if provided, otherwise auto-detected
MODEL="${EXPLICIT_MODEL:-$AUTO_MODEL}"

# ── Fallback API key from secrets mount ──
if [ -z "$ANTHROPIC_API_KEY" ]; then
  SECRET_FILE="/run/secrets/bot-secrets/ANTHROPIC_API_KEY"
  if [ -f "$SECRET_FILE" ]; then
    export ANTHROPIC_API_KEY=$(cat "$SECRET_FILE")
  else
    echo '{"error":"ANTHROPIC_API_KEY not found (checked openclaw.json + secrets mount)"}' >&2
    exit 1
  fi
fi

# ── Base URL: prefer router sidecar, fallback to api-proxy ──
if [ -n "$AUTO_BASE_URL" ]; then
  export ANTHROPIC_BASE_URL="$AUTO_BASE_URL"
else
  export ANTHROPIC_BASE_URL="${ANTHROPIC_BASE_URL:-http://api-proxy.clawy-system.svc.cluster.local:3001}"
fi

# Allow any model name through Claude Code's client-side validation
export ANTHROPIC_CUSTOM_MODEL_OPTION="$MODEL"

# Disable telemetry in headless mode
export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1

# Working directory
WORK_DIR="/workspace/coding"
mkdir -p "$WORK_DIR"
cd "$WORK_DIR"

# Pass all args to claude CLI in print (headless) mode
exec $CLAUDE_BIN -p "$@" \
  --allowedTools "Bash(npm:*),Bash(node:*),Bash(python*:*),Bash(git:*),Bash(ls:*),Bash(mkdir:*),Bash(cat:*),Bash(cp:*),Bash(mv:*),Read,Edit,Write,Glob,Grep" \
  --output-format stream-json \
  --max-turns 30 \
  --model "$MODEL"
