#!/bin/sh
# insane-fetch wrapper — Phase 1+2 adaptive URL fetcher.
# Use after jina.sh returns "empty" / "captcha" / "upstream_error".
# Usage: insane-fetch.sh <url>
#
# Returns JSON: { success, phase, status, target, content:{html,jsonld,ogp}, failure_reason?, escalate_to_browser? }
# If escalate_to_browser is true, the bot should follow up with the `browser` skill.
set -e

URL="$1"
if [ -z "$URL" ]; then
  echo '{"success":false,"failure_reason":"usage","detail":"insane-fetch.sh <url>"}'
  exit 1
fi

: "${API_PROXY_URL:=$CORE_AGENT_API_PROXY_URL}"
if [ -z "$API_PROXY_URL" ] || [ -z "$GATEWAY_TOKEN" ]; then
  echo '{"success":false,"failure_reason":"not_configured","detail":"API_PROXY_URL or GATEWAY_TOKEN not set"}'
  exit 1
fi

if command -v jq >/dev/null 2>&1; then
  BODY=$(jq -cn --arg url "$URL" '{url:$url, locale:"ko-KR", maxPhase:2, extract:["markdown","jsonld","ogp"]}')
else
  ESC_URL=$(printf '%s' "$URL" | sed 's/\\/\\\\/g; s/"/\\"/g')
  BODY="{\"url\":\"$ESC_URL\",\"locale\":\"ko-KR\",\"maxPhase\":2,\"extract\":[\"markdown\",\"jsonld\",\"ogp\"]}"
fi

curl -sS -X POST "$API_PROXY_URL/v1/fetch" \
  -H "Authorization: Bearer $GATEWAY_TOKEN" \
  -H "Content-Type: application/json" \
  --max-time 50 \
  -d "$BODY"
