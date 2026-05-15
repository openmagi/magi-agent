#!/bin/sh
# AEF pipeline-interventions.sh — fetch user-requested interventions from Open Magi platform
#
# User sends retry/cancel/pause/resume via dashboard UI. Open Magi writes to Redis
# list, this script retrieves them so the pipeline skill can act.
#
# Usage: pipeline-interventions.sh <pipeline_id>
#
# Output (stdout, JSON):
#   {"pipeline_id":"...","interventions":[{"action":"cancel","step_id":null,"requested_at":1712345678901}, ...]}
#
# Exit code: 0 on success (may be empty list), 1 on error.

set -e

PIPELINE_ID="$1"
if [ -z "$PIPELINE_ID" ]; then
  echo '{"error":"usage: pipeline-interventions.sh <pipeline_id>"}' >&2
  exit 2
fi

PROXY_URL="${CHAT_PROXY_URL:-http://chat-proxy.clawy-system.svc.cluster.local:3002}"
GATEWAY_TOKEN="${GATEWAY_TOKEN:-$OPENCLAW_GATEWAY_TOKEN}"

if [ -z "$GATEWAY_TOKEN" ]; then
  echo '{"error":"GATEWAY_TOKEN not set"}' >&2
  exit 1
fi

curl -sS --max-time 3 \
  -H "Authorization: Bearer $GATEWAY_TOKEN" \
  "$PROXY_URL/v1/bot-pipelines/interventions?pipeline_id=$(printf '%s' "$PIPELINE_ID" | sed 's/[^a-zA-Z0-9._-]//g')"
