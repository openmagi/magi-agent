#!/bin/sh
# Jina Reader wrapper — fast markdown extraction for public URLs.
# Usage: jina.sh <url> [--json | --spa | --text | --html] [--selector=<css>] [--no-cache]
#
# Returns a JSON envelope: { success: bool, mode, data?|error?, detail?, bytes?, cached? }
# Bot LLM branches on `success`/`error` to decide next step (e.g. escalate to
# insane-fetch on "empty"/"captcha"/"paywall").

set -e

URL="$1"
if [ -z "$URL" ]; then
  echo '{"success":false,"error":"usage","detail":"jina.sh <url> [--json|--spa|--text|--html] [--selector=<css>] [--no-cache]"}'
  exit 1
fi
shift

MODE="markdown"
SELECTOR=""
NO_CACHE="false"

while [ $# -gt 0 ]; do
  case "$1" in
    --json)      MODE="json" ;;
    --spa)       MODE="spa" ;;
    --text)      MODE="text" ;;
    --html)      MODE="html" ;;
    --no-cache)  NO_CACHE="true" ;;
    --selector=*) SELECTOR="${1#--selector=}" ;;
    *) echo '{"success":false,"error":"usage","detail":"unknown flag '"$1"'"}'; exit 1 ;;
  esac
  shift
done

# Resolve API proxy URL — new core-agent uses CORE_AGENT_API_PROXY_URL; fall back to legacy name
: "${API_PROXY_URL:=$CORE_AGENT_API_PROXY_URL}"
if [ -z "$API_PROXY_URL" ] || [ -z "$GATEWAY_TOKEN" ]; then
  echo '{"success":false,"error":"not_configured","detail":"API_PROXY_URL or GATEWAY_TOKEN not set"}'
  exit 1
fi

# Build JSON body with jq if available, otherwise manual string construction
if command -v jq >/dev/null 2>&1; then
  BODY=$(jq -cn --arg url "$URL" --arg mode "$MODE" --arg selector "$SELECTOR" --argjson noCache "$NO_CACHE" \
    '{url:$url, mode:$mode} + (if $selector=="" then {} else {selector:$selector} end) + {noCache:$noCache}')
else
  # Minimal fallback — URL and selector must not contain unescaped backslashes/quotes
  ESC_URL=$(printf '%s' "$URL" | sed 's/\\/\\\\/g; s/"/\\"/g')
  ESC_SEL=$(printf '%s' "$SELECTOR" | sed 's/\\/\\\\/g; s/"/\\"/g')
  if [ -n "$SELECTOR" ]; then
    BODY="{\"url\":\"$ESC_URL\",\"mode\":\"$MODE\",\"selector\":\"$ESC_SEL\",\"noCache\":$NO_CACHE}"
  else
    BODY="{\"url\":\"$ESC_URL\",\"mode\":\"$MODE\",\"noCache\":$NO_CACHE}"
  fi
fi

curl -sS -X POST "$API_PROXY_URL/v1/jina" \
  -H "Authorization: Bearer $GATEWAY_TOKEN" \
  -H "Content-Type: application/json" \
  --max-time 35 \
  -d "$BODY"
