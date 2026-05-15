#!/bin/sh
# Virtuals ACP CLI wrapper — auto-installs and wraps virtuals-protocol-acp skill
# Usage:
#   acp.sh setup                      # Initial setup + API key
#   acp.sh status                     # Agent status
#   acp.sh browse [query]             # Browse marketplace
#   acp.sh sell init                  # Generate offering.json + handlers.ts
#   acp.sh sell create                # Register service (interactive)
#   acp.sh sell list                  # List my services
#   acp.sh sell update [--endpoint X] # Update service
#   acp.sh serve start               # Start seller runtime
#   acp.sh job create --service ID    # Buy a service
#   acp.sh job status --id ID         # Check job status
#
# Requires: LITE_AGENT_API_KEY env var (set via `acp.sh setup`)

set -e

ACP_DIR="${ACP_DIR:-$HOME/.openclaw/acp}"
ACTION="$1"
shift 2>/dev/null || true

if [ -z "$ACTION" ]; then
  echo '{"error":"Usage: acp.sh <setup|status|browse|sell|serve|job> [args]"}'
  exit 1
fi

# --- Auto-install ---
ensure_installed() {
  if [ -d "$ACP_DIR" ] && [ -f "$ACP_DIR/bin/acp.ts" ]; then
    return 0
  fi
  echo '{"status":"installing","message":"Cloning virtuals-protocol-acp..."}'
  git clone --depth 1 https://github.com/Virtual-Protocol/openclaw-acp.git "$ACP_DIR" 2>/dev/null || {
    echo '{"error":"Failed to clone virtuals-protocol-acp. Check network connectivity."}'
    exit 1
  }
  cd "$ACP_DIR" && npm install --production 2>/dev/null || {
    echo '{"error":"npm install failed in acp directory"}'
    exit 1
  }
  echo '{"status":"installed","path":"'"$ACP_DIR"'"}'
}

# --- API key check ---
check_api_key() {
  if [ -z "$LITE_AGENT_API_KEY" ]; then
    echo '{"error":"LITE_AGENT_API_KEY not set. Run: acp.sh setup"}'
    exit 1
  fi
}

# --- Run ACP CLI ---
run_acp() {
  cd "$ACP_DIR"
  npx tsx bin/acp.ts "$@"
}

case "$ACTION" in
  setup)
    ensure_installed
    run_acp setup "$@"
    ;;
  status)
    ensure_installed
    check_api_key
    run_acp status "$@"
    ;;
  browse)
    ensure_installed
    check_api_key
    run_acp browse "$@"
    ;;
  sell)
    ensure_installed
    check_api_key
    SUBACTION="$1"
    shift 2>/dev/null || true
    if [ -z "$SUBACTION" ]; then
      echo '{"error":"Usage: acp.sh sell <init|create|list|update>"}'
      exit 1
    fi
    run_acp sell "$SUBACTION" "$@"
    ;;
  serve)
    ensure_installed
    check_api_key
    SUBACTION="$1"
    shift 2>/dev/null || true
    if [ -z "$SUBACTION" ]; then
      echo '{"error":"Usage: acp.sh serve <start>"}'
      exit 1
    fi
    run_acp serve "$SUBACTION" "$@"
    ;;
  job)
    ensure_installed
    check_api_key
    SUBACTION="$1"
    shift 2>/dev/null || true
    if [ -z "$SUBACTION" ]; then
      echo '{"error":"Usage: acp.sh job <create|status>"}'
      exit 1
    fi
    run_acp job "$SUBACTION" "$@"
    ;;
  *)
    echo '{"error":"Unknown action: '"$ACTION"'. Use: setup, status, browse, sell, serve, job"}'
    exit 1
    ;;
esac
