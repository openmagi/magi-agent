#!/bin/sh
# Knowledge Base write wrapper
# Usage:
#   kb-write.sh --create-collection "name"
#   kb-write.sh --delete-collection "name"
#   kb-write.sh --add "collection" "filename.md" "markdown content..."
#   kb-write.sh --add "collection" "filename.md" --stdin   (read content from stdin)
#   kb-write.sh --update "collection" "filename.md" "markdown content..."
#   kb-write.sh --update "collection" "filename.md" --stdin
#   kb-write.sh --delete "collection" "filename.md"

set -e

. "$(dirname "$0")/_transport.sh" 2>/dev/null || true

PROXY_URL="${CHAT_PROXY_URL:-http://chat-proxy.clawy-system.svc.cluster.local:3002}"
GATEWAY_TOKEN="${GATEWAY_TOKEN:-$OPENCLAW_GATEWAY_TOKEN}"

if [ -z "$GATEWAY_TOKEN" ] || [ -z "$BOT_ID" ]; then
  echo '{"ok":false,"error":"GATEWAY_TOKEN or BOT_ID not set"}'
  exit 1
fi

# ── Activity emit (live tool feed in UI) ──
. "$(dirname "$0")/_activity.sh" 2>/dev/null || true
clawy_activity_emit "kb-write" start

ACTION="$1"
BODY_FILE=$(mktemp)
trap 'rm -f "$BODY_FILE"; clawy_activity_emit "kb-write" end 2>/dev/null || true' EXIT

case "$ACTION" in
  --create-collection)
    if [ -z "$2" ]; then echo '{"ok":false,"error":"Usage: kb-write.sh --create-collection name"}'; exit 1; fi
    if [ -n "$KB_SCOPE" ]; then
      printf '{"name":"%s","scope":"%s"}' "$2" "$KB_SCOPE" > "$BODY_FILE"
    else
      printf '{"name":"%s"}' "$2" > "$BODY_FILE"
    fi
    RESULT=$(clawy_transport_request \
      --method POST \
      --url "$PROXY_URL/v1/integrations/knowledge-write/create-collection" \
      --header "Authorization: Bearer $GATEWAY_TOKEN" \
      --header "X-Bot-Id: $BOT_ID" \
      --header "Content-Type: application/json" \
      --body-file "$BODY_FILE")
    ;;

  --delete-collection)
    if [ -z "$2" ]; then echo '{"ok":false,"error":"Usage: kb-write.sh --delete-collection name"}'; exit 1; fi
    printf '{"name":"%s"}' "$2" > "$BODY_FILE"
    RESULT=$(clawy_transport_request \
      --method POST \
      --url "$PROXY_URL/v1/integrations/knowledge-write/delete-collection" \
      --header "Authorization: Bearer $GATEWAY_TOKEN" \
      --header "X-Bot-Id: $BOT_ID" \
      --header "Content-Type: application/json" \
      --body-file "$BODY_FILE")
    ;;

  --add|--update)
    ENDPOINT="add"
    if [ "$ACTION" = "--update" ]; then ENDPOINT="update"; fi
    COLLECTION="$2"
    FILENAME="$3"
    if [ -z "$COLLECTION" ] || [ -z "$FILENAME" ]; then
      echo "{\"ok\":false,\"error\":\"Usage: kb-write.sh $ACTION collection filename content|--stdin\"}"
      exit 1
    fi

    # Read content from stdin or 4th argument
    if [ "$4" = "--stdin" ]; then
      CONTENT=$(cat)
    else
      CONTENT="$4"
    fi

    if [ -z "$CONTENT" ]; then
      echo '{"ok":false,"error":"Content is empty"}'
      exit 1
    fi

    # Use python/node to safely JSON-encode content (handles newlines, quotes, unicode)
    if command -v node >/dev/null 2>&1; then
      node -e "
        var col = process.argv[1], fn = process.argv[2], c = process.argv[3], sc = process.argv[4];
        var body = {collection:col,filename:fn,content:c};
        if (sc) body.scope = sc;
        process.stdout.write(JSON.stringify(body));
      " "$COLLECTION" "$FILENAME" "$CONTENT" "$KB_SCOPE" > "$BODY_FILE"
    elif command -v python3 >/dev/null 2>&1; then
      python3 -c "
import json,sys
body = {'collection':sys.argv[1],'filename':sys.argv[2],'content':sys.argv[3]}
if sys.argv[4]: body['scope'] = sys.argv[4]
print(json.dumps(body, ensure_ascii=False),end='')
" "$COLLECTION" "$FILENAME" "$CONTENT" "${KB_SCOPE:-}" > "$BODY_FILE"
    else
      # Fallback: escape newlines and quotes manually
      ESCAPED=$(printf '%s' "$CONTENT" | sed 's/\\/\\\\/g; s/"/\\"/g; s/\t/\\t/g' | tr '\n' '\f' | sed 's/\f/\\n/g')
      if [ -n "$KB_SCOPE" ]; then
        printf '{"collection":"%s","filename":"%s","content":"%s","scope":"%s"}' "$COLLECTION" "$FILENAME" "$ESCAPED" "$KB_SCOPE" > "$BODY_FILE"
      else
        printf '{"collection":"%s","filename":"%s","content":"%s"}' "$COLLECTION" "$FILENAME" "$ESCAPED" > "$BODY_FILE"
      fi
    fi

    RESULT=$(clawy_transport_request \
      --method POST \
      --url "$PROXY_URL/v1/integrations/knowledge-write/$ENDPOINT" \
      --header "Authorization: Bearer $GATEWAY_TOKEN" \
      --header "X-Bot-Id: $BOT_ID" \
      --header "Content-Type: application/json" \
      --body-file "$BODY_FILE")
    ;;

  --delete)
    COLLECTION="$2"
    FILENAME="$3"
    if [ -z "$COLLECTION" ] || [ -z "$FILENAME" ]; then
      echo '{"ok":false,"error":"Usage: kb-write.sh --delete collection filename"}'
      exit 1
    fi
    printf '{"collection":"%s","filename":"%s"}' "$COLLECTION" "$FILENAME" > "$BODY_FILE"
    RESULT=$(clawy_transport_request \
      --method POST \
      --url "$PROXY_URL/v1/integrations/knowledge-write/delete" \
      --header "Authorization: Bearer $GATEWAY_TOKEN" \
      --header "X-Bot-Id: $BOT_ID" \
      --header "Content-Type: application/json" \
      --body-file "$BODY_FILE")
    ;;

  *)
    echo '{"ok":false,"error":"Usage: kb-write.sh --create-collection|--delete-collection|--add|--update|--delete ..."}'
    exit 1
    ;;
esac

if [ "$(clawy_transport_is_ok "$RESULT")" = "true" ]; then
  clawy_transport_body "$RESULT"
else
  clawy_transport_failure_json "$RESULT"
  exit 1
fi
