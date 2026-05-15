#!/bin/sh
# Activity emit helper — POSTs tool invocation events to chat-proxy so the
# frontend can show a live activity feed ("agent-run", "kb-search", etc.).
#
# Best-effort only: fire-and-forget with a 2s timeout. Never blocks or fails
# the calling script. No-op if BOT_ID or OPENCLAW_GATEWAY_TOKEN is unset.
#
# Usage (in a wrapper script, after set -e and basic arg parsing):
#   . "$(dirname "$0")/_activity.sh"
#   clawy_activity_emit "agent-run" start
#   trap 'clawy_activity_emit "agent-run" end' EXIT
#
# Label convention: short kebab-case identifier visible to the user
# (e.g. "agent-run", "kb-search", "integration:notion").

clawy_activity_emit() {
  # $1 = label, $2 = phase (start|end)
  [ -z "$BOT_ID" ] && return 0
  [ -z "$OPENCLAW_GATEWAY_TOKEN" ] && return 0
  _label=$1
  _phase=$2
  [ -z "$_label" ] && return 0
  [ -z "$_phase" ] && _phase=start
  _url="${CHAT_PROXY_URL:-http://chat-proxy.clawy-system.svc.cluster.local:3002}/v1/bot-activity/emit"
  # Escape quotes/backslashes in the label so the JSON stays valid.
  _escaped=$(printf '%s' "$_label" | sed 's/\\/\\\\/g; s/"/\\"/g')
  (
    curl -sS -X POST -m 2 \
      -H "Authorization: Bearer $OPENCLAW_GATEWAY_TOKEN" \
      -H "Content-Type: application/json" \
      -d "{\"name\":\"$_escaped\",\"phase\":\"$_phase\"}" \
      "$_url" > /dev/null 2>&1
  ) &
  # Detach: we don't care about the background curl's exit code
  return 0
}
