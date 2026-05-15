#!/bin/sh
# Send a file to the user via chat channel attachment API
# Usage: file-send.sh /path/to/file.xlsx [channel_name]
#
# channel_name defaults to "general" if not specified
# Returns JSON with attachment id — embed [attachment:<id>:<filename>] in your message

set -e

. "$(dirname "$0")/_transport.sh" 2>/dev/null || true

FILE_PATH="$1"
CHANNEL="${2:-general}"

PROXY_URL="${CHAT_PROXY_URL:-http://chat-proxy.clawy-system.svc.cluster.local:3002}"
GATEWAY_TOKEN="${GATEWAY_TOKEN:-$OPENCLAW_GATEWAY_TOKEN}"

if [ -z "$GATEWAY_TOKEN" ]; then
  echo '{"error":"GATEWAY_TOKEN not set"}'
  exit 1
fi

if [ -z "$FILE_PATH" ] || [ ! -f "$FILE_PATH" ]; then
  echo '{"error":"Usage: file-send.sh /path/to/file [channel_name]. File not found: '"$FILE_PATH"'"}'
  exit 1
fi

# ── Activity emit (live tool feed in UI) ──
. "$(dirname "$0")/_activity.sh" 2>/dev/null || true
clawy_activity_emit "file-send" start
trap 'clawy_activity_emit "file-send" end' EXIT

RESULT=$(clawy_transport_request \
  --method POST \
  --url "$PROXY_URL/v1/bot-channels/attachment" \
  --header "Authorization: Bearer $GATEWAY_TOKEN" \
  --form-file "file=$FILE_PATH" \
  --form-field "channel_name=$CHANNEL")

if [ "$(clawy_transport_is_ok "$RESULT")" != "true" ]; then
  clawy_transport_failure_json "$RESULT"
  exit 1
fi

BODY=$(clawy_transport_body "$RESULT")
echo "$BODY"

# Extract id for convenience
ID=$(echo "$BODY" | grep -o '"id":"[^"]*"' | head -1 | sed 's/"id":"//;s/"//')
FILENAME=$(basename "$FILE_PATH")

if [ -n "$ID" ]; then
  echo ""
  echo "SUCCESS: Include this marker in your message:"
  echo "[attachment:${ID}:${FILENAME}]"
fi
