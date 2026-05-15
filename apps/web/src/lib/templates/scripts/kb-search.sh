#!/bin/sh
# Knowledge Base search wrapper
# Usage: kb-search.sh "query keywords"
#        kb-search.sh "collection name" "query keywords" [top_k]
#        kb-search.sh --collections
#        kb-search.sh --documents ["collection name"]
#        kb-search.sh --manifest ["collection name"]
#        kb-search.sh --guide ["collection name"]
#        kb-search.sh --get "s3/object/key.md"

set -e

. "$(dirname "$0")/_transport.sh" 2>/dev/null || true

PROXY_URL="${CHAT_PROXY_URL:-http://chat-proxy.clawy-system.svc.cluster.local:3002}"
GATEWAY_TOKEN="${GATEWAY_TOKEN:-$OPENCLAW_GATEWAY_TOKEN}"

if [ -z "$GATEWAY_TOKEN" ] || [ -z "$BOT_ID" ]; then
  echo '{"error":"GATEWAY_TOKEN or BOT_ID not set"}'
  exit 1
fi

# ── Activity emit (live tool feed in UI) ──
. "$(dirname "$0")/_activity.sh" 2>/dev/null || true
clawy_activity_emit "kb-search" start

BODY_FILE=""
cleanup() {
  if [ -n "$BODY_FILE" ]; then rm -f "$BODY_FILE"; fi
  clawy_activity_emit "kb-search" end 2>/dev/null || true
}
trap cleanup EXIT

json_body() {
  BODY_FILE=$(mktemp)
  if command -v node >/dev/null 2>&1; then
    node - "$@" > "$BODY_FILE" <<'NODE'
const args = process.argv.slice(2);
const body = {};
for (let i = 0; i < args.length; i += 2) {
  const key = args[i];
  const value = args[i + 1] ?? "";
  if (!key || value === "") continue;
  body[key] = key === "top_k" ? Number.parseInt(value, 10) || 10 : value;
}
process.stdout.write(JSON.stringify(body));
NODE
  elif command -v python3 >/dev/null 2>&1; then
    python3 - "$@" > "$BODY_FILE" <<'PY'
import json
import sys

args = sys.argv[1:]
body = {}
for index in range(0, len(args), 2):
    key = args[index]
    value = args[index + 1] if index + 1 < len(args) else ""
    if not key or value == "":
        continue
    body[key] = int(value) if key == "top_k" and value.isdigit() else value
print(json.dumps(body, ensure_ascii=False), end="")
PY
  else
    echo '{"error":"node or python3 required to encode KB request JSON"}'
    exit 1
  fi
}

post_json() {
  ACTION="$1"
  shift
  json_body "$@"
  RESULT=$(clawy_transport_request \
    --method POST \
    --url "$PROXY_URL/v1/integrations/knowledge/$ACTION" \
    --header "Authorization: Bearer $GATEWAY_TOKEN" \
    --header "X-Bot-Id: $BOT_ID" \
    --header "Content-Type: application/json" \
    --body-file "$BODY_FILE")
  if [ "$(clawy_transport_is_ok "$RESULT")" = "true" ]; then
    clawy_transport_body "$RESULT"
  else
    clawy_transport_failure_json "$RESULT"
    exit 1
  fi
}

get_path() {
  PATH_SUFFIX="$1"
  RESULT=$(clawy_transport_request \
    --method GET \
    --url "$PROXY_URL/v1/integrations/knowledge/$PATH_SUFFIX" \
    --header "Authorization: Bearer $GATEWAY_TOKEN" \
    --header "X-Bot-Id: $BOT_ID")
  if [ "$(clawy_transport_is_ok "$RESULT")" = "true" ]; then
    clawy_transport_body "$RESULT"
  else
    clawy_transport_failure_json "$RESULT"
    exit 1
  fi
}

urlencode() {
  if command -v node >/dev/null 2>&1; then
    node -e 'process.stdout.write(encodeURIComponent(process.argv[1]))' "$1"
  elif command -v python3 >/dev/null 2>&1; then
    python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""), end="")' "$1"
  else
    printf '%s' "$1" | sed 's/ /%20/g'
  fi
}

if [ "$1" = "--collections" ]; then
  if [ -n "$KB_SCOPE" ]; then
    get_path "collections?scope=$KB_SCOPE"
  else
    get_path "collections"
  fi
  exit 0
fi

if [ "$1" = "--documents" ]; then
  if [ -n "$2" ]; then
    post_json "documents" collection "$2"
  else
    post_json "documents"
  fi
  exit 0
fi

if [ "$1" = "--manifest" ]; then
  if [ -n "$2" ]; then
    post_json "manifest" collection "$2"
  else
    post_json "manifest"
  fi
  exit 0
fi

if [ "$1" = "--guide" ]; then
  if [ -n "$2" ]; then
    post_json "guide" collection "$2"
  else
    post_json "guide"
  fi
  exit 0
fi

if [ "$1" = "--get" ] && [ -n "$2" ]; then
  KEY=$(urlencode "$2")
  get_path "download?key=$KEY&mode=content"
  exit 0
fi

if [ -n "$2" ]; then
  COLLECTION="$1"
  QUERY="$2"
  TOP_K="${3:-10}"
else
  COLLECTION=""
  QUERY="$1"
  TOP_K="${2:-10}"
fi

if [ -z "$QUERY" ]; then
  echo '{"error":"Usage: kb-search.sh [collection] query [top_k]"}'
  exit 1
fi

if [ -n "$COLLECTION" ] && [ -n "$KB_SCOPE" ]; then
  post_json "search" collection "$COLLECTION" query "$QUERY" top_k "$TOP_K" scope "$KB_SCOPE"
elif [ -n "$COLLECTION" ]; then
  post_json "search" collection "$COLLECTION" query "$QUERY" top_k "$TOP_K"
elif [ -n "$KB_SCOPE" ]; then
  post_json "search" query "$QUERY" top_k "$TOP_K" scope "$KB_SCOPE"
else
  post_json "search" query "$QUERY" top_k "$TOP_K"
fi
