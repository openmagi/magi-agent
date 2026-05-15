#!/bin/sh
# Prune old OpenClaw session files and stale locks.
# Keeps the N most recent .jsonl files (by mtime), deletes the rest.
# Removes ALL .lock files on boot (container restart = all PIDs are stale).
# Runs at gateway startup — before openclaw gateway run.

SESSIONS_DIR="${HOME}/.openclaw/agents/main/sessions"
KEEP="${SESSION_PRUNE_KEEP:-50}"

[ -d "$SESSIONS_DIR" ] || exit 0

# ── Remove stale lock files ──
# On container boot, no previous process can still be alive — all locks are stale.
lock_count=$(find "$SESSIONS_DIR" -maxdepth 1 -name '*.lock' -type f | wc -l)
if [ "$lock_count" -gt 0 ]; then
  find "$SESSIONS_DIR" -maxdepth 1 -name '*.lock' -type f -delete
  echo "[prune-sessions] Removed ${lock_count} stale lock files" >&2
fi

# ── Prune old session files ──
count=$(find "$SESSIONS_DIR" -maxdepth 1 -name '*.jsonl' -type f | wc -l)

if [ "$count" -le "$KEEP" ]; then
  exit 0
fi

deleted=$(ls -1t "$SESSIONS_DIR"/*.jsonl | tail -n +"$((KEEP + 1))" | xargs rm -f 2>/dev/null && echo "$((count - KEEP))")
echo "[prune-sessions] Pruned ${deleted:-0} old session files (kept $KEEP of $count)" >&2

# ── Clean stale task records ──
# Tasks stuck in "running" for >24h are ghosts — the process is long gone.
TASKS_DB="${HOME}/.openclaw/cli-state/tasks/runs.sqlite"
if [ -f "$TASKS_DB" ] && command -v sqlite3 >/dev/null 2>&1; then
  THRESHOLD=$(( $(date +%s) - 86400 ))000
  NOW=$(date +%s)000
  stale_cleaned=$(sqlite3 "$TASKS_DB" "
    UPDATE task_runs
    SET status='completed', ended_at=$NOW, terminal_outcome='cancelled', terminal_summary='Auto-cleaned: stale task >24h'
    WHERE status='running' AND started_at < $THRESHOLD;
    SELECT changes();
  " 2>/dev/null)
  if [ -n "$stale_cleaned" ] && [ "$stale_cleaned" -gt 0 ]; then
    echo "[prune-sessions] Cleaned ${stale_cleaned} stale task records (>24h running)" >&2
  fi
fi
