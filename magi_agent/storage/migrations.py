from __future__ import annotations

import sqlite3
from collections.abc import Sequence

MIGRATIONS: Sequence[tuple[int, str]] = (
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            app_name TEXT NOT NULL,
            user_id TEXT NOT NULL,
            state_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_app_user ON sessions(app_name, user_id);
        """,
    ),
    (
        2,
        """
        CREATE TABLE IF NOT EXISTS session_metadata (
            session_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
            agent_name TEXT,
            model TEXT,
            provider TEXT,
            tokens_in INTEGER NOT NULL DEFAULT 0,
            tokens_out INTEGER NOT NULL DEFAULT 0,
            tokens_cache_read INTEGER NOT NULL DEFAULT 0,
            tokens_cache_write INTEGER NOT NULL DEFAULT 0,
            cost_usd REAL NOT NULL DEFAULT 0.0,
            turn_count INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        );
        """,
    ),
    (
        3,
        """
        CREATE TABLE IF NOT EXISTS goal_states (
            session_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
            goal_json TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        );
        """,
    ),
    (
        4,
        # A-driver: persistent ScheduledJobSource backing store.  Each row holds
        # the full ScheduledJobRecord JSON (job_json) so a fresh
        # SqliteScheduledJobSource instance can reconstruct scheduled jobs after a
        # restart.  next_run_utc is a denormalized ISO-8601 UTC string mirrored
        # from the record so due_jobs() can range-scan in SQL.  No FK to sessions:
        # scheduled jobs are session-independent (they may outlive any session).
        """
        CREATE TABLE IF NOT EXISTS scheduled_jobs (
            job_id TEXT PRIMARY KEY,
            schedule_expr TEXT NOT NULL,
            next_run_utc TEXT NOT NULL,
            job_json TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        );
        CREATE INDEX IF NOT EXISTS idx_scheduled_jobs_next_run
            ON scheduled_jobs(next_run_utc);
        """,
    ),
    (
        5,
        """
        CREATE TABLE IF NOT EXISTS work_queue_tasks (
            id                   TEXT PRIMARY KEY,
            title                TEXT NOT NULL,
            body                 TEXT,
            assignee             TEXT,
            status               TEXT NOT NULL,
            priority             INTEGER NOT NULL DEFAULT 0,
            tenant               TEXT,
            session_id           TEXT,
            idempotency_key      TEXT,
            claim_lock           TEXT,
            claim_expires        INTEGER,
            worker_pid           INTEGER,
            last_heartbeat_at    INTEGER,
            current_run_id       INTEGER,
            consecutive_failures INTEGER NOT NULL DEFAULT 0,
            max_retries          INTEGER,
            goal_mode            INTEGER NOT NULL DEFAULT 0,
            goal_max_turns       INTEGER,
            result               TEXT,
            last_failure_error   TEXT,
            created_at           INTEGER NOT NULL,
            started_at           INTEGER,
            completed_at         INTEGER
        );
        CREATE TABLE IF NOT EXISTS work_queue_task_links (
            parent_id TEXT NOT NULL,
            child_id  TEXT NOT NULL,
            PRIMARY KEY (parent_id, child_id)
        );
        CREATE TABLE IF NOT EXISTS work_queue_task_events (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id    TEXT NOT NULL,
            run_id     INTEGER,
            kind       TEXT NOT NULL,
            payload    TEXT,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS work_queue_task_runs (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id             TEXT NOT NULL,
            status              TEXT NOT NULL,
            claim_lock          TEXT,
            claim_expires       INTEGER,
            worker_pid          INTEGER,
            last_heartbeat_at   INTEGER,
            started_at          INTEGER NOT NULL,
            ended_at            INTEGER,
            outcome             TEXT,
            summary             TEXT,
            error               TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_wq_status ON work_queue_tasks(status);
        CREATE INDEX IF NOT EXISTS idx_wq_idem   ON work_queue_tasks(idempotency_key);
        CREATE INDEX IF NOT EXISTS idx_wq_links_child  ON work_queue_task_links(child_id);
        CREATE INDEX IF NOT EXISTS idx_wq_events_task  ON work_queue_task_events(task_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_wq_runs_task    ON work_queue_task_runs(task_id, started_at);
        """,
    ),
    (
        6,
        # PR-M7 hosted MissionProjector identity mapping (design section 5.4).
        # One row per projected WorkTask: the hosted mission id chat-proxy
        # generated for ``idempotencyKey = "wq:<task_id>"`` plus the last mission
        # status we projected (so re-projection is idempotent). Consumed by the
        # projector (task_id -> mission_id) and later by PR-M8's reconciler
        # (mission_id -> task_id resolution + poll cursor), hence the reverse
        # index on mission_id.
        """
        CREATE TABLE IF NOT EXISTS mission_projection (
            task_id               TEXT PRIMARY KEY,
            mission_id            TEXT,
            last_projected_status TEXT,
            updated_at            INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_mission_projection_mission
            ON mission_projection(mission_id);
        """,
    ),
)


def run_migrations(conn: sqlite3.Connection) -> int:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS _schema_version (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        )
        """
    )
    row = conn.execute("SELECT MAX(version) FROM _schema_version").fetchone()
    current = row[0] if row[0] is not None else 0

    applied = 0
    for version, sql in MIGRATIONS:
        if version <= current:
            continue
        conn.executescript(sql)
        conn.execute("INSERT INTO _schema_version (version) VALUES (?)", (version,))
        applied += 1

    conn.commit()
    return applied
