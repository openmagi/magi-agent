#!/bin/sh
# Firecrawl API wrapper — scrape, crawl, and map web pages
# Usage: firecrawl.sh scrape <url>
#        firecrawl.sh crawl <url> [limit]
#        firecrawl.sh map <url>
# Uses FIRECRAWL_API_KEY (user-owned) or API_PROXY_URL (platform credits)
set -e

ACTION="$1"
URL="$2"
LIMIT="${3:-10}"

if [ -z "$ACTION" ] || [ -z "$URL" ]; then
  echo '{"error":"Usage: firecrawl.sh <scrape|crawl|map> <url> [limit]"}'
  exit 1
fi

: "${API_PROXY_URL:=$CORE_AGENT_API_PROXY_URL}"

# User-owned key → direct Firecrawl API; otherwise → platform proxy
if [ -n "$FIRECRAWL_API_KEY" ]; then
  API_BASE="https://api.firecrawl.dev/v1"
  AUTH_HEADER="Authorization: Bearer $FIRECRAWL_API_KEY"
elif [ -n "$API_PROXY_URL" ] && [ -n "$GATEWAY_TOKEN" ]; then
  API_BASE="$API_PROXY_URL/v1/firecrawl"
  AUTH_HEADER="Authorization: Bearer $GATEWAY_TOKEN"
else
  echo '{"error":"Firecrawl not available — no API key or platform proxy configured"}'
  exit 1
fi

case "$ACTION" in
  scrape)
    RESULT=$(curl -sf --compressed -X POST "$API_BASE/scrape" \
      -H "$AUTH_HEADER" \
      -H "Content-Type: application/json" \
      -d "{\"url\":\"$URL\",\"formats\":[\"markdown\"]}" 2>&1) || {
      echo '{"error":"Firecrawl scrape request failed"}'
      exit 1
    }
    echo "$RESULT"
    ;;
  crawl)
    RESULT=$(curl -sf --compressed -X POST "$API_BASE/crawl" \
      -H "$AUTH_HEADER" \
      -H "Content-Type: application/json" \
      -d "{\"url\":\"$URL\",\"limit\":$LIMIT}" 2>&1) || {
      echo '{"error":"Firecrawl crawl request failed"}'
      exit 1
    }
    echo "$RESULT"
    ;;
  map)
    RESULT=$(curl -sf --compressed -X POST "$API_BASE/map" \
      -H "$AUTH_HEADER" \
      -H "Content-Type: application/json" \
      -d "{\"url\":\"$URL\"}" 2>&1) || {
      echo '{"error":"Firecrawl map request failed"}'
      exit 1
    }
    echo "$RESULT"
    ;;
  *)
    echo "{\"error\":\"Unknown action: $ACTION. Use scrape, crawl, or map.\"}"
    exit 1
    ;;
esac
