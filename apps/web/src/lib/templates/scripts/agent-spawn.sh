#!/bin/sh
# AEF agent-spawn.sh — reliable subagent launcher (B1+B2+B4+B6 at Open Magi layer)
#
# Replaces `nohup openclaw agent --session-id X --message Y ... &` with a wrapper that:
#   B1/B2: verifies spawn actually happened (5s handshake + subagents list check)
#   B4:    enforces per-bot concurrency limit (Redis ZSET active_spawns)
#   B6:    emits CP2 events on success/failure
#   retry: 1s → 2s → 4s exponential backoff, max 3 attempts
#
# Usage: agent-spawn.sh --session-id pipeline-step-3a \
#                       --message "..." \
#                       --timeout 7200 \
#                       --log /path/to/log \
#                       [--pipeline-id X]    # for CP2 reporting
#
# Output (stdout, JSON):
#   {"status":"verified","session_id":"...","pid":12345,"attempt":1}
#   {"status":"failed","reason":"phantom","attempts":3}
#   {"status":"rejected","reason":"concurrency_limit","active":4}
#
# Exit codes: 0 = verified, 1 = failed, 2 = bad args, 3 = rate-limited

set -e

SESSION_ID=""
MESSAGE=""
TIMEOUT="7200"
LOG=""
PIPELINE_ID=""
AGENT="main"
CONCURRENT_LIMIT="${AEF_SPAWN_CONCURRENT_LIMIT:-4}"
# Default 12s > gateway 10s timeout, so slow-registering subagents aren't falsely killed.
# 2026-04-18 RCA-PHANTOM-001 §3.3: gateway timeout race caused false phantom detection
# when subagent registered between 5-10s.
VERIFY_WAIT_SEC="${AEF_SPAWN_VERIFY_WAIT:-12}"
MAX_ATTEMPTS="${AEF_SPAWN_MAX_ATTEMPTS:-3}"

while [ $# -gt 0 ]; do
  case "$1" in
    --session-id) SESSION_ID="$2"; shift 2 ;;
    --message) MESSAGE="$2"; shift 2 ;;
    --timeout) TIMEOUT="$2"; shift 2 ;;
    --log) LOG="$2"; shift 2 ;;
    --pipeline-id) PIPELINE_ID="$2"; shift 2 ;;
    --agent) AGENT="$2"; shift 2 ;;
    *) echo "{\"error\":\"unknown arg: $1\"}" >&2; exit 2 ;;
  esac
done

if [ -z "$SESSION_ID" ] || [ -z "$MESSAGE" ]; then
  echo '{"error":"--session-id and --message required"}' >&2
  exit 2
fi

[ -z "$LOG" ] && LOG="/tmp/agent-$SESSION_ID.log"

PROXY_URL="${CHAT_PROXY_URL:-http://chat-proxy.clawy-system.svc.cluster.local:3002}"

# ── Activity emit (live tool feed in UI) ──
. "$(dirname "$0")/_activity.sh" 2>/dev/null || true
clawy_activity_emit "agent-spawn" start
trap 'clawy_activity_emit "agent-spawn" end' EXIT

# ── Helper: report CP2 event (fire-and-forget) ──
report_event() {
  EVENT="$1"
  DETAILS="$2"
  if [ -n "$PIPELINE_ID" ] && [ -x "$(command -v pipeline-report.sh)" ]; then
    pipeline-report.sh "$PIPELINE_ID" "$EVENT" "$SESSION_ID" "$DETAILS" >/dev/null 2>&1 &
  fi
  # also try direct curl if wrapper not in PATH
  if [ -n "$PIPELINE_ID" ] && [ -n "${GATEWAY_TOKEN:-}" ]; then
    PAYLOAD=$(printf '{"pipeline_id":"%s","event":"%s","step_id":"%s","details":"%s"}' \
      "$PIPELINE_ID" "$EVENT" "$SESSION_ID" "$(echo "$DETAILS" | sed 's/"/\\"/g')")
    curl -sS --max-time 3 -X POST \
      -H "Authorization: Bearer $GATEWAY_TOKEN" \
      -H "Content-Type: application/json" \
      "$PROXY_URL/v1/bot-pipeline/report" \
      --data "$PAYLOAD" >/dev/null 2>&1 &
  fi
}

# ── B4: check concurrency via openclaw subagents list ──
check_concurrency() {
  # Count active (non-done) subagents via CLI — cheap + accurate
  ACTIVE_COUNT=$(openclaw subagents list --json 2>/dev/null | \
    awk -F'"active":\\[' '{print $2}' | awk -F']' '{print $1}' | \
    awk -F'{' '{print NF-1}' | head -1)
  [ -z "$ACTIVE_COUNT" ] && ACTIVE_COUNT=0
  if [ "$ACTIVE_COUNT" -ge "$CONCURRENT_LIMIT" ]; then
    return 1
  fi
  return 0
}

# ── Main spawn loop with retry-with-backoff ──
ATTEMPT=1
BACKOFF=1
while [ "$ATTEMPT" -le "$MAX_ATTEMPTS" ]; do
  # Concurrency check
  if ! check_concurrency; then
    echo "{\"status\":\"rejected\",\"reason\":\"concurrency_limit\",\"limit\":$CONCURRENT_LIMIT,\"attempt\":$ATTEMPT}"
    report_event "step_spawn_rejected" "concurrency_limit=$CONCURRENT_LIMIT attempt=$ATTEMPT"
    exit 3
  fi

  # Fire-and-forget spawn
  nohup openclaw agent \
    --agent "$AGENT" \
    --session-id "$SESSION_ID" \
    --message "$MESSAGE" \
    --json \
    --timeout "$TIMEOUT" \
    > "$LOG" 2>&1 &
  SPAWN_PID=$!

  # Wait for handshake
  sleep "$VERIFY_WAIT_SEC"

  # Verify: is the session actually registered?
  if openclaw subagents list --json 2>/dev/null | grep -q "\"sessionId\":\"$SESSION_ID\""; then
    # VERIFIED — emit success event + return
    echo "{\"status\":\"verified\",\"session_id\":\"$SESSION_ID\",\"pid\":$SPAWN_PID,\"attempt\":$ATTEMPT,\"log\":\"$LOG\"}"
    report_event "step_spawned" "verified attempt=$ATTEMPT pid=$SPAWN_PID"
    exit 0
  fi

  # Not verified — kill orphan PID + retry
  kill "$SPAWN_PID" 2>/dev/null || true
  report_event "step_phantom_detected" "attempt=$ATTEMPT pid=$SPAWN_PID"

  if [ "$ATTEMPT" -lt "$MAX_ATTEMPTS" ]; then
    sleep "$BACKOFF"
    BACKOFF=$((BACKOFF * 2))
  fi
  ATTEMPT=$((ATTEMPT + 1))
done

# All attempts exhausted
echo "{\"status\":\"failed\",\"reason\":\"phantom\",\"attempts\":$MAX_ATTEMPTS,\"session_id\":\"$SESSION_ID\"}"
report_event "step_failed" "phantom all $MAX_ATTEMPTS attempts failed"
exit 1
