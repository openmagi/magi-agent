#!/bin/sh
# Metered web search via platform api-proxy
# Usage: web-search.sh "search query"
# Requires: API_PROXY_URL and GATEWAY_TOKEN env vars
set -e

SCRIPT_DIR=$(dirname "$0")
TRANSPORT_SH="${CLAWY_TRANSPORT_HELPER_SH:-$SCRIPT_DIR/../../../scripts/_transport.sh}"
if [ ! -f "$TRANSPORT_SH" ]; then
  TRANSPORT_SH="${HOME:-/home/ocuser}/.openclaw/bin/_transport.sh"
fi
. "$TRANSPORT_SH" 2>/dev/null || true

QUERY="$1"
if [ -z "$QUERY" ]; then echo '{"error":"Usage: web-search.sh <query>"}'; exit 1; fi
: "${API_PROXY_URL:=$CORE_AGENT_API_PROXY_URL}"
if [ -z "$API_PROXY_URL" ] || [ -z "$GATEWAY_TOKEN" ]; then echo '{"error":"API_PROXY_URL or GATEWAY_TOKEN not set — search proxy not available"}'; exit 1; fi
BODY_FILE=$(mktemp)
trap 'rm -f "$BODY_FILE"' EXIT
JSON_BODY='{"query":'"$(printf '%s' "$QUERY" | python3 -c 'import json,sys;print(json.dumps(sys.stdin.read()))')"'}'
printf '%s' "$JSON_BODY" > "$BODY_FILE"
RESULT=$(clawy_transport_request \
  --method POST \
  --url "$API_PROXY_URL/v1/search" \
  --header "Content-Type: application/json" \
  --header "Authorization: Bearer $GATEWAY_TOKEN" \
  --body-file "$BODY_FILE")

if [ "$(clawy_transport_is_ok "$RESULT")" = "true" ]; then
  clawy_transport_body "$RESULT"
else
  clawy_transport_failure_json "$RESULT"
  exit 1
fi
