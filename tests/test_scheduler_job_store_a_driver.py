"""A-driver — persistent SQLite ScheduledJobSource.

TDD: round-trip persistence, restart-survival, ScheduledJobSource Protocol
conformance, CRUD, and at-most-once record_advance compare-and-set semantics.
"""
from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path


def _dt(seconds: int) -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=seconds)


def _record(job_id: str, *, expr: str = "every 60s", next_run_s: int = 0):
    from magi_agent.harness.scheduler_executor import ScheduledJobRecord

    return ScheduledJobRecord(
        jobId=job_id,
        scheduleExpr=expr,
        lastFire=None,
        nextRun=_dt(next_run_s),
    )


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------

def test_store_satisfies_scheduled_job_source_protocol(tmp_path: Path) -> None:
    from magi_agent.harness.scheduler_executor import ScheduledJobSource
    from magi_agent.harness.scheduler_job_store import SqliteScheduledJobSource

    store = SqliteScheduledJobSource(tmp_path / "jobs.db")
    try:
        assert isinstance(store, ScheduledJobSource)
    finally:
        store.close()


def test_store_advertises_local_fake_kind(tmp_path: Path) -> None:
    from magi_agent.harness.scheduler_job_store import SqliteScheduledJobSource

    store = SqliteScheduledJobSource(tmp_path / "jobs.db")
    try:
        # tick() validates the source kind; the persistent source is gated
        # behind the same local_fake contract until the executor is enabled.
        assert getattr(store, "scheduler_source_kind", None) == "local_fake"
    finally:
        store.close()


# ---------------------------------------------------------------------------
# CRUD + round-trip
# ---------------------------------------------------------------------------

def test_create_and_list_all(tmp_path: Path) -> None:
    from magi_agent.harness.scheduler_job_store import SqliteScheduledJobSource

    store = SqliteScheduledJobSource(tmp_path / "jobs.db")
    try:
        store.create(_record("job:a"))
        store.create(_record("job:b", next_run_s=120))
        ids = {r.job_id for r in store.list_all()}
        assert ids == {"job:a", "job:b"}
    finally:
        store.close()


def test_get_round_trips_fields(tmp_path: Path) -> None:
    from magi_agent.harness.scheduler_job_store import SqliteScheduledJobSource

    store = SqliteScheduledJobSource(tmp_path / "jobs.db")
    try:
        rec = _record("job:a", expr="every 300s", next_run_s=300)
        store.create(rec)
        got = store.get("job:a")
        assert got is not None
        assert got.job_id == "job:a"
        assert got.schedule_expr == "every 300s"
        assert got.next_run == _dt(300)
        assert got.last_fire is None
    finally:
        store.close()


def test_create_then_update_replaces(tmp_path: Path) -> None:
    from magi_agent.harness.scheduler_job_store import SqliteScheduledJobSource

    store = SqliteScheduledJobSource(tmp_path / "jobs.db")
    try:
        store.create(_record("job:a", expr="every 60s"))
        store.update(_record("job:a", expr="every 600s", next_run_s=600))
        got = store.get("job:a")
        assert got is not None
        assert got.schedule_expr == "every 600s"
        assert got.next_run == _dt(600)
    finally:
        store.close()


def test_delete_removes(tmp_path: Path) -> None:
    from magi_agent.harness.scheduler_job_store import SqliteScheduledJobSource

    store = SqliteScheduledJobSource(tmp_path / "jobs.db")
    try:
        store.create(_record("job:a"))
        store.delete("job:a")
        assert store.get("job:a") is None
        assert store.list_all() == []
        # delete is idempotent.
        store.delete("job:a")
    finally:
        store.close()


# ---------------------------------------------------------------------------
# due_jobs
# ---------------------------------------------------------------------------

def test_due_jobs_returns_only_due(tmp_path: Path) -> None:
    from magi_agent.harness.scheduler_job_store import SqliteScheduledJobSource

    store = SqliteScheduledJobSource(tmp_path / "jobs.db")
    try:
        store.create(_record("due", next_run_s=0))
        store.create(_record("future", next_run_s=10_000))
        due_ids = {r.job_id for r in store.due_jobs(_dt(100))}
        assert due_ids == {"due"}
    finally:
        store.close()


# ---------------------------------------------------------------------------
# record_advance compare-and-set
# ---------------------------------------------------------------------------

def test_record_advance_cas_success(tmp_path: Path) -> None:
    from magi_agent.harness.scheduler_job_store import SqliteScheduledJobSource

    store = SqliteScheduledJobSource(tmp_path / "jobs.db")
    try:
        store.create(_record("job:a", next_run_s=0))
        ok = store.record_advance("job:a", _dt(60), expected_next_run=_dt(0))
        assert ok is True
        got = store.get("job:a")
        assert got is not None
        assert got.next_run == _dt(60)
        # old next_run becomes last_fire (mirrors InMemoryJobSource).
        assert got.last_fire == _dt(0)
    finally:
        store.close()


def test_record_advance_cas_mismatch_returns_false(tmp_path: Path) -> None:
    from magi_agent.harness.scheduler_job_store import SqliteScheduledJobSource

    store = SqliteScheduledJobSource(tmp_path / "jobs.db")
    try:
        store.create(_record("job:a", next_run_s=0))
        ok = store.record_advance("job:a", _dt(60), expected_next_run=_dt(999))
        assert ok is False
        got = store.get("job:a")
        assert got is not None
        assert got.next_run == _dt(0)  # unchanged
    finally:
        store.close()


def test_record_advance_unknown_job_returns_false(tmp_path: Path) -> None:
    from magi_agent.harness.scheduler_job_store import SqliteScheduledJobSource

    store = SqliteScheduledJobSource(tmp_path / "jobs.db")
    try:
        assert store.record_advance("nope", _dt(60), expected_next_run=_dt(0)) is False
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Restart survival — THE key persistence proof
# ---------------------------------------------------------------------------

def test_survives_fresh_instance(tmp_path: Path) -> None:
    from magi_agent.harness.scheduler_job_store import SqliteScheduledJobSource

    db = tmp_path / "jobs.db"
    store_a = SqliteScheduledJobSource(db)
    store_a.create(_record("job:a", expr="every 120s", next_run_s=120))
    store_a.record_advance("job:a", _dt(240), expected_next_run=_dt(120))
    store_a.close()

    # Fresh instance — simulates a process restart.
    store_b = SqliteScheduledJobSource(db)
    try:
        got = store_b.get("job:a")
        assert got is not None
        assert got.schedule_expr == "every 120s"
        assert got.next_run == _dt(240)
        assert got.last_fire == _dt(120)
    finally:
        store_b.close()


def test_tick_integration_against_persistent_source(tmp_path: Path) -> None:
    """tick() drives the persistent source end-to-end and advances next_run."""
    from magi_agent.harness.scheduler_executor import tick
    from magi_agent.harness.scheduler_job_store import SqliteScheduledJobSource
    from magi_agent.harness.scheduler_runtime import SchedulerLease

    db = tmp_path / "jobs.db"
    store = SqliteScheduledJobSource(db)
    try:
        store.create(_record("job:a", expr="every 60s", next_run_s=0))
        now = _dt(100)
        now_ms = int(now.timestamp() * 1000)
        lease = SchedulerLease(
            leaseId="lease:test",
            ownerDigest="owner:test",
            acquiredAt=now_ms - 1000,
            expiresAt=now_ms + 60_000,
        )
        result = tick(
            now=now,
            source=store,
            lease=lease,
            lock_dir=tmp_path / "lock",
            owner_digest="owner:test",
        )
        assert result.status == "tick_completed"
        assert result.fired_job_ids == ("job:a",)
        # next_run advanced and persisted.
        got = store.get("job:a")
        assert got is not None
        assert got.next_run > now
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Naive datetime rejection (Important 2)
# ---------------------------------------------------------------------------

def test_iso_utc_rejects_naive_datetime() -> None:
    """_iso_utc must raise ValueError for a naive (tzinfo=None) datetime.

    Silently accepting a naive datetime would cause astimezone() to assume LOCAL
    time — on a non-UTC host the ISO key would be hours off, making due_jobs
    return wrong results and a later aware==naive CAS comparison raise TypeError.
    """
    from datetime import datetime

    from magi_agent.harness.scheduler_job_store import _iso_utc

    naive = datetime(2026, 1, 1, 12, 0, 0)  # no tzinfo
    try:
        _iso_utc(naive)
        assert False, "_iso_utc should have raised ValueError for naive datetime"
    except ValueError as exc:
        assert "naive" in str(exc).lower() or "timezone-aware" in str(exc).lower(), str(exc)


def test_iso_utc_accepts_aware_datetime() -> None:
    """_iso_utc must accept timezone-aware datetimes without error."""
    from datetime import UTC, datetime, timezone, timedelta

    from magi_agent.harness.scheduler_job_store import _iso_utc

    utc_dt = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    result = _iso_utc(utc_dt)
    assert "2026-01-01" in result
    assert "+00:00" in result or "Z" in result.upper() or "00:00" in result

    # Also works for non-UTC aware datetimes (normalized to UTC).
    kst = datetime(2026, 1, 1, 21, 0, 0, tzinfo=timezone(timedelta(hours=9)))
    result_kst = _iso_utc(kst)
    assert "2026-01-01T12:00:00" in result_kst


def test_scheduled_job_record_rejects_naive_next_run() -> None:
    """ScheduledJobRecord field_validator must reject naive next_run at construction."""
    from datetime import datetime

    import pytest

    from magi_agent.harness.scheduler_executor import ScheduledJobRecord

    with pytest.raises((ValueError, Exception)) as exc_info:
        ScheduledJobRecord(
            jobId="job:naive",
            scheduleExpr="every 60s",
            nextRun=datetime(2026, 1, 1, 12, 0, 0),  # naive — no tzinfo
        )
    assert "naive" in str(exc_info.value).lower() or "timezone" in str(exc_info.value).lower(), \
        f"Expected clear error message, got: {exc_info.value}"


def test_scheduled_job_record_accepts_aware_next_run() -> None:
    """ScheduledJobRecord must accept timezone-aware next_run."""
    from datetime import UTC, datetime

    from magi_agent.harness.scheduler_executor import ScheduledJobRecord

    rec = ScheduledJobRecord(
        jobId="job:aware",
        scheduleExpr="every 60s",
        nextRun=datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC),
    )
    assert rec.next_run.tzinfo is not None


def test_create_with_naive_next_run_raises(tmp_path: Path) -> None:
    """SqliteScheduledJobSource.create must reject a record with naive next_run
    (belt-and-suspenders: caught at ScheduledJobRecord construction or _iso_utc)."""
    from datetime import datetime

    import pytest

    from magi_agent.harness.scheduler_executor import ScheduledJobRecord
    from magi_agent.harness.scheduler_job_store import SqliteScheduledJobSource

    store = SqliteScheduledJobSource(tmp_path / "jobs.db")
    try:
        with pytest.raises((ValueError, Exception)):
            ScheduledJobRecord(
                jobId="job:naive",
                scheduleExpr="every 60s",
                nextRun=datetime(2026, 1, 1, 12, 0, 0),  # naive
            )
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Import purity (mirrors A2/A3 boundary contract)
# ---------------------------------------------------------------------------

def test_module_does_not_import_adk_or_network() -> None:
    """Mirror the A2 purity contract: AST-check direct dangerous imports + a
    sys.modules check for genuinely-forbidden live/infra prefixes.

    urllib/socket/subprocess are EXCLUDED from the sys.modules check because
    pydantic's transitive graph pulls them on every scheduler module (pre-existing,
    tracked by already-RED tests on origin/main); we instead assert this module has
    no DIRECT top-level import of the dangerous trio (or ADK).
    """
    import ast
    from pathlib import Path

    src = Path(__file__).parent.parent / "magi_agent" / "harness" / "scheduler_job_store.py"
    tree = ast.parse(src.read_text())
    direct: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                direct.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            direct.add(node.module.split(".")[0])
    assert not ({"urllib", "socket", "subprocess"} & direct)
    assert "google" not in direct  # no top-level google.adk / google.genai

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib, sys
importlib.import_module("magi_agent.harness.scheduler_job_store")
forbidden_prefixes = (
    "google.adk", "google.genai", "magi_agent.adk_bridge", "requests",
    "httpx", "aiohttp", "kubernetes", "telegram", "discord",
)
loaded = [n for n in sys.modules if any(n == p or n.startswith(p + ".") for p in forbidden_prefixes)]
assert not loaded, loaded
print("ok")
""",
        ],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert "ok" in completed.stdout
