import sqlite3
from magi_agent.storage.migrations import run_migrations

def _tables(conn):
    return {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}

def test_migration_creates_work_queue_tables():
    conn = sqlite3.connect(":memory:")
    run_migrations(conn)
    t = _tables(conn)
    assert {"work_queue_tasks", "work_queue_task_links",
            "work_queue_task_events", "work_queue_task_runs"} <= t

def test_migration_is_idempotent():
    conn = sqlite3.connect(":memory:")
    assert run_migrations(conn) >= 1
    assert run_migrations(conn) == 0   # second run applies nothing new
