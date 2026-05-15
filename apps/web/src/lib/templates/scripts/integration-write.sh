#!/bin/sh
# POST integration data via chat-proxy.
# Usage: echo '{"key":"value"}' | integration-write.sh <service>/<action>
# Examples:
#   echo '{"spreadsheetId":"...","range":"Sheet1!A1","values":[["a","b"]]}' | integration-write.sh google/sheets-write
#   echo '{"title":"My Sheet"}' | integration-write.sh google/sheets-create

set -e

SERVICE="$1"
ACTION="$2"

# Support both "service/action" and "service action" formats
if [ -z "$ACTION" ] && echo "$SERVICE" | grep -q "/"; then
  ACTION=$(echo "$SERVICE" | cut -d'/' -f2)
  SERVICE=$(echo "$SERVICE" | cut -d'/' -f1)
fi

# Strip erroneous HTTP method / flag args bots sometimes pass (e.g. "POST", "--post")
case "$ACTION" in POST|GET|PUT|DELETE|PATCH|--post|--get|--put|--delete) ACTION=""; ;; esac

PROXY_URL="${CHAT_PROXY_URL:-http://chat-proxy.clawy-system.svc.cluster.local:3002}"

if [ -z "$GATEWAY_TOKEN" ] || [ -z "$BOT_ID" ]; then
  echo '{"error":"GATEWAY_TOKEN or BOT_ID not set"}'
  exit 1
fi

if [ -z "$SERVICE" ] || [ -z "$ACTION" ]; then
  echo '{"error":"Usage: echo JSON | integration-write.sh <service> <action>"}'
  exit 1
fi

# Read request body from stdin
BODY=$(cat)

# Build optional custom API key header
EXTRA_HEADERS=""
if [ -n "$GOOGLE_API_KEY" ]; then
  EXTRA_HEADERS="-H \"X-Google-Api-Key: $GOOGLE_API_KEY\""
fi

eval curl -sf --compressed \
  -X POST \
  -H "\"Authorization: Bearer $GATEWAY_TOKEN\"" \
  -H "\"X-Bot-Id: $BOT_ID\"" \
  -H "\"Content-Type: application/json\"" \
  $EXTRA_HEADERS \
  -d "'$BODY'" \
  "\"$PROXY_URL/v1/integrations/$SERVICE/$ACTION\""
