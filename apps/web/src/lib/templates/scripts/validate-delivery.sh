#!/bin/sh
# AEF Cron delivery pre-flight validator (CP3)
# Usage: validate-delivery.sh --channel <name>
#        validate-delivery.sh --target <chatId>
#        validate-delivery.sh --mode announce --channel <name>
#
# Run BEFORE `openclaw cron add` to catch misconfigurations early.
# Returns JSON: { valid: bool, resolved?, error?, recommendation? }
# Exit code 0 if valid, 1 if invalid.

set -e

PROXY_URL="${CHAT_PROXY_URL:-http://chat-proxy.clawy-system.svc.cluster.local:3002}"
GATEWAY_TOKEN="${GATEWAY_TOKEN:-$OPENCLAW_GATEWAY_TOKEN}"
SESSION_KEY="${SESSION_KEY:-$OPENCLAW_SESSION_KEY:-}"

if [ -z "$GATEWAY_TOKEN" ]; then
  echo '{"error":"GATEWAY_TOKEN not set"}'
  exit 1
fi

MODE="announce"
CHANNEL=""
TARGET=""

while [ $# -gt 0 ]; do
  case "$1" in
    --mode) MODE="$2"; shift 2 ;;
    --channel) CHANNEL="$2"; shift 2 ;;
    --target) TARGET="$2"; shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

PAYLOAD=$(printf '{"mode":"%s","channel":"%s","target":"%s"}' "$MODE" "$CHANNEL" "$TARGET")

RESP=$(curl -sS --max-time 5 -X POST \
  -H "Authorization: Bearer $GATEWAY_TOKEN" \
  -H "Content-Type: application/json" \
  ${SESSION_KEY:+-H "X-Session-Key: $SESSION_KEY"} \
  "$PROXY_URL/v1/bot-cron/validate-delivery" \
  --data "$PAYLOAD" \
  --write-out "__STATUS__%{http_code}")

STATUS="${RESP##*__STATUS__}"
BODY="${RESP%__STATUS__*}"
echo "$BODY"

# Exit code based on valid field
case "$BODY" in
  *'"valid":true'*) exit 0 ;;
  *) exit 1 ;;
esac
