#!/bin/sh
# AEF Pipeline self-report (CP2)
# Usage: pipeline-report.sh <pipeline_id> <event> [step_id] [details...]
#
# Events:
#   pipeline_started / pipeline_completed / pipeline_paused
#   step_spawned / step_verified / step_phantom_detected
#   step_stalled / step_completed / step_failed
#   delivery_error
#
# Call this at every pipeline state transition in the pipeline skill's
# spawn-with-verify / stalled-detection / error paths.

set -e

PROXY_URL="${CHAT_PROXY_URL:-http://chat-proxy.clawy-system.svc.cluster.local:3002}"
GATEWAY_TOKEN="${GATEWAY_TOKEN:-$OPENCLAW_GATEWAY_TOKEN}"
SESSION_KEY="${SESSION_KEY:-$OPENCLAW_SESSION_KEY:-}"

if [ -z "$GATEWAY_TOKEN" ]; then
  echo '{"error":"GATEWAY_TOKEN not set"}'
  exit 1
fi

PIPELINE_ID="$1"
EVENT="$2"
STEP_ID="${3:-}"
shift 2 2>/dev/null || true
[ -n "$STEP_ID" ] && shift 1 2>/dev/null || true
DETAILS="$*"

if [ -z "$PIPELINE_ID" ] || [ -z "$EVENT" ]; then
  echo '{"error":"usage: pipeline-report.sh <pipeline_id> <event> [step_id] [details]"}'
  exit 2
fi

# Escape JSON strings (basic — no quotes in inputs assumed)
PAYLOAD=$(printf '{"pipeline_id":"%s","event":"%s","step_id":"%s","details":"%s"}' \
  "$PIPELINE_ID" "$EVENT" "$STEP_ID" "$(echo "$DETAILS" | sed 's/"/\\"/g')")

HEADERS="-H Authorization:Bearer\ $GATEWAY_TOKEN -H Content-Type:application/json"
[ -n "$SESSION_KEY" ] && HEADERS="$HEADERS -H X-Session-Key:$SESSION_KEY"

curl -sS --max-time 5 -X POST \
  -H "Authorization: Bearer $GATEWAY_TOKEN" \
  -H "Content-Type: application/json" \
  ${SESSION_KEY:+-H "X-Session-Key: $SESSION_KEY"} \
  "$PROXY_URL/v1/bot-pipeline/report" \
  --data "$PAYLOAD"
