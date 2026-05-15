#!/bin/sh
# AEF heartbeat-check.sh — session liveness probe (B5 at Open Magi layer)
#
# Determines whether an agent session is alive, stalled, or dead by examining:
#   1) openclaw subagents list — is session registered?
#   2) log file mtime — has output advanced recently?
#   3) state file mtime (optional) — has the step written progress?
#
# Usage: heartbeat-check.sh --session-id pipeline-step-3a \
#                           --log /workspace/plans/pipeline/X/step-3a-agent.log \
#                           [--state /workspace/plans/pipeline/X/state.json] \
#                           [--stalled-threshold 900]      # seconds, default 900 (15min)
#                           [--dead-threshold 1800]        # seconds, default 1800 (30min)
#                           [--pipeline-id X]              # for CP2 reporting
#
# Output (stdout, JSON):
#   {"status":"alive","session_id":"...","last_activity_sec":12,"in_subagents_list":true}
#   {"status":"stalled","session_id":"...","last_activity_sec":920,"reason":"no_log_output"}
#   {"status":"dead","session_id":"...","last_activity_sec":2100,"reason":"subagent_missing_and_log_stale"}
#
# Exit codes: 0 = alive, 1 = stalled, 2 = dead, 3 = bad args

set -e

SESSION_ID=""
LOG=""
STATE=""
STALLED_THRESHOLD="${AEF_HEARTBEAT_STALLED:-900}"
DEAD_THRESHOLD="${AEF_HEARTBEAT_DEAD:-1800}"
PIPELINE_ID=""

while [ $# -gt 0 ]; do
  case "$1" in
    --session-id) SESSION_ID="$2"; shift 2 ;;
    --log) LOG="$2"; shift 2 ;;
    --state) STATE="$2"; shift 2 ;;
    --stalled-threshold) STALLED_THRESHOLD="$2"; shift 2 ;;
    --dead-threshold) DEAD_THRESHOLD="$2"; shift 2 ;;
    --pipeline-id) PIPELINE_ID="$2"; shift 2 ;;
    *) echo "{\"error\":\"unknown arg: $1\"}" >&2; exit 3 ;;
  esac
done

if [ -z "$SESSION_ID" ]; then
  echo '{"error":"--session-id required"}' >&2
  exit 3
fi

NOW=$(date +%s)

# ── Helper: get mtime in seconds since epoch (cross-platform) ──
get_mtime() {
  F="$1"
  [ -f "$F" ] || { echo 0; return; }
  # BSD (macOS) stat vs GNU stat
  stat -f %m "$F" 2>/dev/null || stat -c %Y "$F" 2>/dev/null || echo 0
}

# ── Helper: CP2 event reporting (fire-and-forget) ──
report_event() {
  EVENT="$1"
  DETAILS="$2"
  if [ -n "$PIPELINE_ID" ] && [ -x "$(command -v pipeline-report.sh)" ]; then
    pipeline-report.sh "$PIPELINE_ID" "$EVENT" "$SESSION_ID" "$DETAILS" >/dev/null 2>&1 &
  fi
}

# ── Signal 1: is session active per openclaw? ──
IN_LIST="false"
if openclaw subagents list --json 2>/dev/null | grep -q "\"sessionId\":\"$SESSION_ID\""; then
  IN_LIST="true"
fi

# ── Signal 2: log file mtime ──
LOG_MTIME=0
LAST_ACTIVITY=999999
if [ -n "$LOG" ] && [ -f "$LOG" ]; then
  LOG_MTIME=$(get_mtime "$LOG")
  if [ "$LOG_MTIME" -gt 0 ]; then
    LAST_ACTIVITY=$((NOW - LOG_MTIME))
  fi
fi

# ── Signal 3: state file mtime (optional — stronger signal of forward progress) ──
STATE_MTIME=0
if [ -n "$STATE" ] && [ -f "$STATE" ]; then
  STATE_MTIME=$(get_mtime "$STATE")
fi

# ── Decision tree ──
# Prefer more recent of log vs state
NEWEST_MTIME="$LOG_MTIME"
if [ "$STATE_MTIME" -gt "$NEWEST_MTIME" ]; then
  NEWEST_MTIME="$STATE_MTIME"
fi
[ "$NEWEST_MTIME" -gt 0 ] && LAST_ACTIVITY=$((NOW - NEWEST_MTIME))

# Case A: in subagents list AND recent activity → alive
if [ "$IN_LIST" = "true" ] && [ "$LAST_ACTIVITY" -lt "$STALLED_THRESHOLD" ]; then
  echo "{\"status\":\"alive\",\"session_id\":\"$SESSION_ID\",\"last_activity_sec\":$LAST_ACTIVITY,\"in_subagents_list\":true}"
  exit 0
fi

# Case B: not in list AND recent activity (just finished, log flushed) → alive→about-to-complete
if [ "$IN_LIST" = "false" ] && [ "$LAST_ACTIVITY" -lt 60 ]; then
  echo "{\"status\":\"alive\",\"session_id\":\"$SESSION_ID\",\"last_activity_sec\":$LAST_ACTIVITY,\"in_subagents_list\":false,\"note\":\"likely just completed\"}"
  exit 0
fi

# Case C: in list but no recent activity past stalled threshold → stalled (hung)
if [ "$IN_LIST" = "true" ] && [ "$LAST_ACTIVITY" -ge "$STALLED_THRESHOLD" ]; then
  echo "{\"status\":\"stalled\",\"session_id\":\"$SESSION_ID\",\"last_activity_sec\":$LAST_ACTIVITY,\"reason\":\"subagent_alive_but_no_log_output\"}"
  report_event "step_stalled" "last_activity_sec=$LAST_ACTIVITY reason=hung"
  exit 1
fi

# Case D: not in list AND activity past dead threshold → dead (phantom or crashed)
if [ "$IN_LIST" = "false" ] && [ "$LAST_ACTIVITY" -ge "$DEAD_THRESHOLD" ]; then
  echo "{\"status\":\"dead\",\"session_id\":\"$SESSION_ID\",\"last_activity_sec\":$LAST_ACTIVITY,\"reason\":\"subagent_missing_and_log_stale\"}"
  report_event "step_stalled" "last_activity_sec=$LAST_ACTIVITY reason=dead"
  exit 2
fi

# Case E: not in list AND activity between stalled and dead thresholds → stalled
if [ "$IN_LIST" = "false" ] && [ "$LAST_ACTIVITY" -ge "$STALLED_THRESHOLD" ]; then
  echo "{\"status\":\"stalled\",\"session_id\":\"$SESSION_ID\",\"last_activity_sec\":$LAST_ACTIVITY,\"reason\":\"subagent_missing_log_partial\"}"
  report_event "step_stalled" "last_activity_sec=$LAST_ACTIVITY reason=partial"
  exit 1
fi

# Default: ambiguous — treat as alive to avoid false-positive kills
echo "{\"status\":\"alive\",\"session_id\":\"$SESSION_ID\",\"last_activity_sec\":$LAST_ACTIVITY,\"in_subagents_list\":$IN_LIST,\"note\":\"ambiguous\"}"
exit 0
