#!/bin/sh
# Twitter/X API v2 wrapper — routes through chat-proxy OAuth integration
# Usage:
#   twitter.sh tweet "Hello world"
#   twitter.sh tweet "Reply text" <in_reply_to_tweet_id>
#   twitter.sh timeline [max_results]
#   twitter.sh mentions [max_results]
#   twitter.sh metrics
#   twitter.sh search "query" [max_results]
#   twitter.sh delete <tweet_id>
#
# Auth is handled automatically via OAuth tokens stored in chat-proxy.
# No manual API keys needed.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ACTION="$1"

if [ -z "$ACTION" ]; then
  echo '{"error":"Usage: twitter.sh <tweet|timeline|mentions|metrics|search|delete> [args]"}'
  exit 1
fi

case "$ACTION" in
  tweet)
    TEXT="$2"
    REPLY_TO="$3"
    if [ -z "$TEXT" ]; then
      echo '{"error":"Usage: twitter.sh tweet \"text\" [reply_to_id]"}'
      exit 1
    fi
    CHAR_COUNT=$(printf '%s' "$TEXT" | wc -m | tr -d ' ')
    if [ "$CHAR_COUNT" -gt 280 ]; then
      echo "{\"error\":\"Tweet too long: ${CHAR_COUNT}/280 chars\"}"
      exit 1
    fi
    if [ -n "$REPLY_TO" ]; then
      BODY="{\"text\":$(printf '%s' "$TEXT" | python3 -c 'import sys,json; print(json.dumps(sys.stdin.read()))'),\"reply_to\":\"${REPLY_TO}\"}"
    else
      BODY="{\"text\":$(printf '%s' "$TEXT" | python3 -c 'import sys,json; print(json.dumps(sys.stdin.read()))')}"
    fi
    echo "$BODY" | "$SCRIPT_DIR/integration-write.sh" twitter/tweet
    ;;

  timeline)
    MAX="${2:-10}"
    "$SCRIPT_DIR/integration.sh" "twitter/timeline?count=${MAX}"
    ;;

  mentions)
    MAX="${2:-10}"
    "$SCRIPT_DIR/integration.sh" "twitter/mentions?count=${MAX}"
    ;;

  metrics)
    "$SCRIPT_DIR/integration.sh" twitter/metrics
    ;;

  search)
    QUERY="$2"
    MAX="${3:-10}"
    if [ -z "$QUERY" ]; then
      echo '{"error":"Usage: twitter.sh search \"query\" [max_results]"}'
      exit 1
    fi
    ENC_QUERY=$(printf '%s' "$QUERY" | python3 -c 'import sys,urllib.parse; print(urllib.parse.quote(sys.stdin.read(), safe=""))')
    "$SCRIPT_DIR/integration.sh" "twitter/search?q=${ENC_QUERY}&count=${MAX}"
    ;;

  delete)
    TWEET_ID="$2"
    if [ -z "$TWEET_ID" ]; then
      echo '{"error":"Usage: twitter.sh delete <tweet_id>"}'
      exit 1
    fi
    echo "{\"tweet_id\":\"${TWEET_ID}\"}" | "$SCRIPT_DIR/integration-write.sh" twitter/delete
    ;;

  *)
    echo "{\"error\":\"Unknown action: $ACTION. Use: tweet, timeline, mentions, metrics, search, delete\"}"
    exit 1
    ;;
esac
