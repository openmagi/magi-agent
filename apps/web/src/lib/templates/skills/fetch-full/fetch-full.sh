#!/usr/bin/env bash
# fetch-full.sh — Retrieve a previously-compacted tool_result by signed ref_id.
#
# Usage:
#   fetch-full.sh <signed_ref_id>
#   fetch-full.sh <signed_ref_id> --offset=N --length=N
#
# Requires env (injected by bot runtime):
#   CHAT_PROXY_URL     e.g. http://chat-proxy.clawy-system.svc:3001
#   GATEWAY_TOKEN      bot gateway token
#   SESSION_KEY        current OpenClaw session key (forwarded as X-Session-Key)

set -euo pipefail

if [ $# -lt 1 ]; then
  echo "Usage: fetch-full.sh <signed_ref_id> [--offset=N] [--length=N]" >&2
  exit 2
fi

REF_ID="$1"
shift || true

OFFSET="0"
LENGTH="20000"
for arg in "$@"; do
  case "$arg" in
    --offset=*) OFFSET="${arg#*=}" ;;
    --length=*) LENGTH="${arg#*=}" ;;
    *) echo "Unknown arg: $arg" >&2; exit 2 ;;
  esac
done

# Integer validation to prevent JSON injection into the request payload
if ! [[ "$OFFSET" =~ ^[0-9]+$ ]]; then
  echo "Invalid offset: must be non-negative integer" >&2; exit 2
fi
if ! [[ "$LENGTH" =~ ^[0-9]+$ ]]; then
  echo "Invalid length: must be non-negative integer" >&2; exit 2
fi

# ref_id format: UUID + "." + hex(16). Reject anything else before hitting the wire.
if ! [[ "$REF_ID" =~ ^[0-9a-fA-F-]{36}\.[0-9a-fA-F]{16}$ ]]; then
  echo "Invalid ref_id format" >&2; exit 2
fi

: "${CHAT_PROXY_URL:?CHAT_PROXY_URL not set}"
: "${GATEWAY_TOKEN:?GATEWAY_TOKEN not set}"
: "${SESSION_KEY:?SESSION_KEY not set}"

PAYLOAD=$(printf '{"ref_id":"%s","offset":%s,"length":%s}' "$REF_ID" "$OFFSET" "$LENGTH")

RESP=$(curl -sS -X POST "$CHAT_PROXY_URL/v1/fetch-full" \
  -H "Authorization: Bearer $GATEWAY_TOKEN" \
  -H "X-Session-Key: $SESSION_KEY" \
  -H "Content-Type: application/json" \
  --max-time 10 \
  --write-out "\n__STATUS__%{http_code}" \
  --data "$PAYLOAD")

STATUS="${RESP##*__STATUS__}"
BODY="${RESP%__STATUS__*}"

echo "$BODY"

case "$STATUS" in
  2*) exit 0 ;;
  *) exit 1 ;;
esac
