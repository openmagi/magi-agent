#!/bin/sh
# Fetch integration data via chat-proxy.
# Usage: integration.sh <service>/<action> [POST_BODY]
#        integration.sh <service>/<action> POST [POST_BODY]
# Examples:
#   integration.sh google/calendar
#   integration.sh twitter/tweet '{"text":"Hello world"}'
#   integration.sh twitter/tweet POST '{"text":"I'\''m tweeting"}'

set -e

. "$(dirname "$0")/_transport.sh" 2>/dev/null || true

SERVICE="${1:-}"
ACTION="${2:-}"
POST_BODY="${3:-}"
BODY_FILE=""

is_method_arg() {
  case "$1" in
    POST|GET|PUT|DELETE|PATCH|--post|--get|--put|--delete|--patch) return 0 ;;
    *) return 1 ;;
  esac
}

# Support both "service/action[/extra]" and "service action" formats.
if echo "$SERVICE" | grep -q "/"; then
  SERVICE_ACTION="$SERVICE"
  SERVICE=$(printf '%s' "$SERVICE_ACTION" | cut -d'/' -f1)
  ACTION=$(printf '%s' "$SERVICE_ACTION" | cut -d'/' -f2-)
  if is_method_arg "${2:-}"; then
    POST_BODY="${3:-}"
  elif [ -n "${2:-}" ]; then
    POST_BODY="$2"
  else
    POST_BODY=""
  fi
else
  if is_method_arg "${3:-}"; then
    POST_BODY="${4:-}"
  elif is_method_arg "$ACTION"; then
    ACTION=""
    POST_BODY="${3:-}"
  else
    POST_BODY="${3:-}"
  fi
fi

# Public skill IDs can differ from the chat-proxy service namespace.
case "$SERVICE" in
  korean-corporate-disclosure) SERVICE="dart" ;;
  court-auction) SERVICE="auction" ;;
  maps-korea) SERVICE="maps-kr" ;;
  maps-google) SERVICE="maps" ;;
  golf-caddie) SERVICE="golf" ;;
  fmp-financial-data) SERVICE="fmp" ;;
esac

PROXY_URL="${CHAT_PROXY_URL:-http://chat-proxy.clawy-system.svc.cluster.local:3002}"

# Support both GATEWAY_TOKEN and OPENCLAW_GATEWAY_TOKEN env var names
GATEWAY_TOKEN="${GATEWAY_TOKEN:-$OPENCLAW_GATEWAY_TOKEN}"

if [ -z "$GATEWAY_TOKEN" ] || [ -z "$BOT_ID" ]; then
  echo '{"error":"GATEWAY_TOKEN or BOT_ID not set"}'
  exit 1
fi

# ── Activity emit (live tool feed in UI) ──
. "$(dirname "$0")/_activity.sh" 2>/dev/null || true
_integration_label="integration:${SERVICE:-?}"
clawy_activity_emit "$_integration_label" start
trap 'if [ -n "$BODY_FILE" ] && [ -f "$BODY_FILE" ]; then rm -f "$BODY_FILE"; fi; clawy_activity_emit "$_integration_label" end 2>/dev/null || true' EXIT

if [ -z "$SERVICE" ] || [ -z "$ACTION" ]; then
  echo '{"error":"Usage: integration.sh <service> <action>"}'
  exit 1
fi

URL="$PROXY_URL/v1/integrations/$SERVICE/$ACTION"
URL=$("$(clawy_transport_node_bin)" - "$URL" <<'NODE'
try {
  process.stdout.write(new URL(process.argv[2] || "").toString());
} catch {
  process.stdout.write(process.argv[2] || "");
}
NODE
)

if [ -n "$POST_BODY" ]; then
  BODY_FILE=$(mktemp)
  printf '%s' "$POST_BODY" > "$BODY_FILE"
  set -- \
    --method POST \
    --url "$URL" \
    --header "Authorization: Bearer $GATEWAY_TOKEN" \
    --header "X-Bot-Id: $BOT_ID" \
    --header "Content-Type: application/json"
  if [ -n "$GOOGLE_API_KEY" ]; then
    set -- "$@" --header "X-Google-Api-Key: $GOOGLE_API_KEY"
  fi
  if [ -n "$GOOGLE_ADS_DEVELOPER_TOKEN" ]; then
    set -- "$@" --header "X-Google-Ads-Developer-Token: $GOOGLE_ADS_DEVELOPER_TOKEN"
  fi
  set -- "$@" --body-file "$BODY_FILE"
  RESULT=$(clawy_transport_request "$@")
else
  set -- \
    --method GET \
    --url "$URL" \
    --header "Authorization: Bearer $GATEWAY_TOKEN" \
    --header "X-Bot-Id: $BOT_ID"
  if [ -n "$GOOGLE_API_KEY" ]; then
    set -- "$@" --header "X-Google-Api-Key: $GOOGLE_API_KEY"
  fi
  if [ -n "$GOOGLE_ADS_DEVELOPER_TOKEN" ]; then
    set -- "$@" --header "X-Google-Ads-Developer-Token: $GOOGLE_ADS_DEVELOPER_TOKEN"
  fi
  RESULT=$(clawy_transport_request "$@")
fi

if [ "$(clawy_transport_is_ok "$RESULT")" = "true" ]; then
  clawy_transport_body "$RESULT"
else
  clawy_transport_failure_json "$RESULT"
  exit 1
fi
