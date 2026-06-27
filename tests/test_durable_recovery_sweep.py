"""WS1 PR1d - StartupRecoverySweep (turn-level boot recovery orchestration).

The sweep orchestrates section 3.4:

  1. PRIMARY (always, when the master sweep flag is ON): durable background-task
     reclaim via ``recover_background_tasks`` (dead-pid reclaim + dispatch tick),
     AT-LEAST-ONCE (Correction E).
  2. OPTIONAL (only when ``MAGI_DURABLE_FOREGROUND_CONTINUATION_ENABLED`` is ON):
     a WS1-local CONTEXT-ONLY foreground continuation admitted by the WS1-local
     ``foreground_resume_admissible`` predicate (NOT ``verify_resume_request``,
     Correction F: that gate refuses 100% of realistic foreground resumes). It
     re-enters ``_drive`` with the TEXT fold from ``replay_messages_up_to``.

Default-OFF: the whole sweep is a no-op unless ``MAGI_DURABLE_STARTUP_RECOVERY_ENABLED``
is ON; the foreground continuation additionally requires its own flag. Fail-open:
a corrupt/unreadable store logs and returns, boot continues.

Design: WS1 durable crash-resume, PR1d StartupRecoverySweep (section 3.4 +
Correction E / Correction F).
"""
from __future__ import annotations

import pytest

from magi_agent.missions.work_queue.models import WorkTask
from magi_agent.missions.work_queue.runner import WorkTaskRunResult
from magi_agent.runtime.durable_checkpoint_emitter import (
    POLICY_SNAPSHOT_SENTINEL,
    CheckpointDigests,
    build_checkpoint,
)


# --------------------------------------------------------------------------- #
# Fakes                                                                        #
# --------------------------------------------------------------------------- #


_HEX = "a" * 64
_DIGEST_A = "sha256:" + _HEX
_DIGEST_B = "sha256:" + ("b" * 64)


def _digests() -> CheckpointDigests:
    return CheckpointDigests(
        state_digest=_DIGEST_A,
        ledger_head_digest=_DIGEST_A,
        effective_policy_snapshot_digest=POLICY_SNAPSHOT_SENTINEL,
        context_projection_digest=_DIGEST_A,
        policy_available=False,
    )


class _RecordingRunner:
    def __init__(self) -> None:
        self.runs: list[str] = []

    async def run_task(self, task: WorkTask) -> WorkTaskRunResult:
        self.runs.append(task.id)
        return WorkTaskRunResult(outcome="completed", summary=f"ran {task.id}")


def _claim_with_future_lease(store, task_id: str, *, worker_pid: int) -> None:
    store.create(WorkTask(id=task_id, title="x", status="ready", created_at=1))
    claimed = store.claim(task_id, claimer="w1", now=1000, worker_pid=worker_pid)
    assert claimed is not None and claimed.status == "running"


class _FakeCheckpointStore:
    """Minimal DurableCheckpointStore stand-in for the foreground path.

    Carries a list of ``StoredCheckpoint``-shaped rows (only the fields the
    sweep reads) and records ``increment_resume_attempt`` calls.
    """

    def __init__(self, rows: list[object]) -> None:
        self._rows = rows
        self.incremented: list[tuple[str, str]] = []
        self.raise_on_list = False

    def list_resumable_turns(self) -> list[object]:
        if self.raise_on_list:
            raise RuntimeError("corrupt durable db")
        return list(self._rows)

    def increment_resume_attempt(self, run_id: str, turn_id: str) -> int:
        self.incremented.append((run_id, turn_id))
        return 1


class _StoredRow:
    """StoredCheckpoint duck-type carrying just what the sweep needs."""

    def __init__(
        self,
        *,
        run_id: str,
        turn_id: str,
        resumable: bool,
        watermark_uuid: str,
        cwd: str,
        resume_attempt_count: int,
    ) -> None:
        self.checkpoint = build_checkpoint(
            run_id=run_id,
            turn_id=turn_id,
            step_id="step-0002",
            digests=_digests(),
            resumable=resumable,
        )
        self.turn_id = turn_id
        self.watermark_uuid = watermark_uuid
        self.evidence_line_count = 3
        self.cwd = cwd
        self.resume_attempt_count = resume_attempt_count
        self.superseded = False


class _SweepHarness:
    """Wires a StartupRecoverySweep with injected replay + drive + emit + verify."""

    def __init__(self, store, driver, ckpt_store, *, replay_result):
        self.store = store
        self.driver = driver
        self.ckpt_store = ckpt_store
        self.replay_result = replay_result
        self.drive_calls: list[list[dict[str, str]]] = []
        self.emitted: list[tuple[str, dict[str, object]]] = []
        self.verify_calls: list[object] = []
        self.replay_calls: list[dict[str, object]] = []

    def replay(self, session_id, *, cwd, up_to_seq, bot_id=""):
        self.replay_calls.append(
            {"session_id": session_id, "cwd": cwd, "up_to_seq": up_to_seq}
        )
        return list(self.replay_result)

    def drive(self, *, initial_messages):
        self.drive_calls.append(list(initial_messages))

    def emit(self, event, **fields):
        self.emitted.append((event, dict(fields)))

    def verify(self, *args, **kwargs):
        self.verify_calls.append((args, kwargs))
        raise AssertionError("verify_resume_request must NOT be called on the sweep path")

    def build(self):
        from magi_agent.runtime.durable_recovery import StartupRecoverySweep

        return StartupRecoverySweep(
            work_store=self.store,
            driver=self.driver,
            checkpoint_store=self.ckpt_store,
            replay_messages=self.replay,
            drive=self.drive,
            emit=self.emit,
            verify_resume_request=self.verify,
            pid_alive=lambda _pid: False,
            now=2000,
        )


def _harness(tmp_path, *, rows=None, replay_result=None):
    from magi_agent.missions.work_queue.driver import WorkQueueDriver
    from magi_agent.missions.work_queue.store import SqliteWorkQueueStore

    store = SqliteWorkQueueStore(tmp_path / "wq.db")
    runner = _RecordingRunner()
    driver = WorkQueueDriver(store, runner, claimer="dispatcher-0")
    ckpt_store = _FakeCheckpointStore(rows or [])
    h = _SweepHarness(store, driver, ckpt_store, replay_result=replay_result or [])
    h.runner = runner
    return h


_TEXT_FOLD = [
    {"role": "user", "content": "do a thing"},
    {"role": "assistant", "content": "working on it"},
]


# --------------------------------------------------------------------------- #
# 1. PRIMARY background at-least-once                                          #
# --------------------------------------------------------------------------- #


def test_sweep_background_at_least_once(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_DURABLE_STARTUP_RECOVERY_ENABLED", "1")
    monkeypatch.delenv("MAGI_DURABLE_FOREGROUND_CONTINUATION_ENABLED", raising=False)

    h = _harness(tmp_path)
    _claim_with_future_lease(h.store, "t", worker_pid=424242)

    result = h.build().run()

    assert list(result.reclaimed_task_ids) == ["t"]
    assert "t" in h.runner.runs
    assert h.store.get("t").status == "completed"
    # No foreground machinery touched.
    assert h.drive_calls == []
    assert h.verify_calls == []


# --------------------------------------------------------------------------- #
# 2. OPTIONAL foreground continuation (flag ON)                               #
# --------------------------------------------------------------------------- #


def test_sweep_foreground_continuation_admissible(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_DURABLE_STARTUP_RECOVERY_ENABLED", "1")
    monkeypatch.setenv("MAGI_DURABLE_FOREGROUND_CONTINUATION_ENABLED", "1")
    monkeypatch.delenv("MAGI_DURABLE_MAX_RESUME_ATTEMPTS", raising=False)

    row = _StoredRow(
        run_id="run-1",
        turn_id="turn-1",
        resumable=True,
        watermark_uuid="U2",
        cwd=str(tmp_path),
        resume_attempt_count=0,
    )
    h = _harness(tmp_path, rows=[row], replay_result=_TEXT_FOLD)
    result = h.build().run()

    # Re-entered _drive with the TEXT fold (no tool entries).
    assert h.drive_calls == [_TEXT_FOLD]
    # Replay was asked for the watermark uuid (NEVER a chain index).
    assert h.replay_calls[0]["up_to_seq"] == "U2"
    # Attempt incremented BEFORE re-entry.
    assert h.ckpt_store.incremented == [("run-1", "turn-1")]
    # Status event emitted.
    assert any(e == "durable_resume_context_only" for e, _ in h.emitted)
    # verify_resume_request is NEVER called on this path (Correction F).
    assert h.verify_calls == []
    assert "turn-1" in result.resumed_turn_ids


def test_sweep_foreground_continuation_off_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_DURABLE_STARTUP_RECOVERY_ENABLED", "1")
    monkeypatch.delenv("MAGI_DURABLE_FOREGROUND_CONTINUATION_ENABLED", raising=False)

    row = _StoredRow(
        run_id="run-1",
        turn_id="turn-1",
        resumable=True,
        watermark_uuid="U2",
        cwd=str(tmp_path),
        resume_attempt_count=0,
    )
    h = _harness(tmp_path, rows=[row], replay_result=_TEXT_FOLD)
    _claim_with_future_lease(h.store, "t", worker_pid=424242)

    result = h.build().run()

    # Background reclaim still happened.
    assert list(result.reclaimed_task_ids) == ["t"]
    # NEVER re-entered _drive; the resumable checkpoint is ignored.
    assert h.drive_calls == []
    assert h.ckpt_store.incremented == []
    assert result.resumed_turn_ids == ()


# --------------------------------------------------------------------------- #
# 3. verify_resume_request armed-gate (UNIT; off the happy path)              #
# --------------------------------------------------------------------------- #


def test_verify_resume_request_refuses_on_ledger_mismatch():
    from magi_agent.runtime.checkpointing import verify_resume_request

    ckpt = build_checkpoint(
        run_id="run-1",
        turn_id="turn-1",
        step_id="step-0002",
        digests=_digests(),
        resumable=True,
    )

    # (a) ledger digest differs -> refuse with ledger_head_digest_mismatch.
    report = verify_resume_request(
        ckpt,
        ledgerHeadDigest=_DIGEST_B,
        effectivePolicySnapshotDigest=POLICY_SNAPSHOT_SENTINEL,
        effectivePolicySnapshotAvailable=True,
        authorityScopeWouldExpand=False,
        pendingApprovalExpired=False,
        requiredEvidenceMissing=False,
    )
    assert report.ok is False
    assert "ledger_head_digest_mismatch" in report.reason_codes

    # (b) matching digest + available=True + no expansions -> ok.
    report_ok = verify_resume_request(
        ckpt,
        ledgerHeadDigest=ckpt.ledger_head_digest,
        effectivePolicySnapshotDigest=POLICY_SNAPSHOT_SENTINEL,
        effectivePolicySnapshotAvailable=True,
        authorityScopeWouldExpand=False,
        pendingApprovalExpired=False,
        requiredEvidenceMissing=False,
    )
    assert report_ok.ok is True
    assert report_ok.reason_codes == ()

    # (c) policy snapshot unavailable -> refuse (Correction F1).
    report_unavail = verify_resume_request(
        ckpt,
        ledgerHeadDigest=ckpt.ledger_head_digest,
        effectivePolicySnapshotDigest=POLICY_SNAPSHOT_SENTINEL,
        effectivePolicySnapshotAvailable=False,
        authorityScopeWouldExpand=False,
        pendingApprovalExpired=False,
        requiredEvidenceMissing=False,
    )
    assert report_unavail.ok is False
    assert "effective_policy_snapshot_unavailable" in report_unavail.reason_codes


# --------------------------------------------------------------------------- #
# 4. Foreground refusal via the WS1-local predicate                           #
# --------------------------------------------------------------------------- #


def test_sweep_foreground_refused_when_not_resumable_or_over_attempts(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("MAGI_DURABLE_STARTUP_RECOVERY_ENABLED", "1")
    monkeypatch.setenv("MAGI_DURABLE_FOREGROUND_CONTINUATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_DURABLE_MAX_RESUME_ATTEMPTS", "2")

    not_resumable = _StoredRow(
        run_id="run-1",
        turn_id="turn-nr",
        resumable=False,
        watermark_uuid="U2",
        cwd=str(tmp_path),
        resume_attempt_count=0,
    )
    over_attempts = _StoredRow(
        run_id="run-1",
        turn_id="turn-oa",
        resumable=True,
        watermark_uuid="U2",
        cwd=str(tmp_path),
        resume_attempt_count=2,  # >= MAX_RESUME_ATTEMPTS
    )
    h = _harness(
        tmp_path, rows=[not_resumable, over_attempts], replay_result=_TEXT_FOLD
    )
    result = h.build().run()

    # Neither re-entered _drive.
    assert h.drive_calls == []
    assert h.verify_calls == []
    reasons = {f.get("reason") for e, f in h.emitted if e == "durable_resume_skipped"}
    assert "not_resumable" in reasons
    assert "max_resume_attempts" in reasons
    assert result.resumed_turn_ids == ()


# --------------------------------------------------------------------------- #
# 5. Transcript unavailable                                                   #
# --------------------------------------------------------------------------- #


def test_sweep_transcript_unavailable(tmp_path, monkeypatch):
    from magi_agent.runtime.checkpointing import _REASON_CODES

    monkeypatch.setenv("MAGI_DURABLE_STARTUP_RECOVERY_ENABLED", "1")
    monkeypatch.setenv("MAGI_DURABLE_FOREGROUND_CONTINUATION_ENABLED", "1")

    row = _StoredRow(
        run_id="run-1",
        turn_id="turn-1",
        resumable=True,
        watermark_uuid="U2",
        cwd=str(tmp_path),
        resume_attempt_count=0,
    )
    # replay returns [] -> Envelope log absent/unreadable.
    h = _harness(tmp_path, rows=[row], replay_result=[])
    result = h.build().run()

    assert h.drive_calls == []
    reasons = {f.get("reason") for e, f in h.emitted if e == "durable_resume_skipped"}
    assert "transcript_unavailable" in reasons
    # transcript_unavailable is a WS1-local reason, NOT a canonical verify code (E7).
    assert "transcript_unavailable" not in _REASON_CODES
    assert result.resumed_turn_ids == ()


# --------------------------------------------------------------------------- #
# 6. Background drains first                                                   #
# --------------------------------------------------------------------------- #


def test_sweep_drains_background_first(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_DURABLE_STARTUP_RECOVERY_ENABLED", "1")
    monkeypatch.setenv("MAGI_DURABLE_FOREGROUND_CONTINUATION_ENABLED", "1")

    order: list[str] = []

    row = _StoredRow(
        run_id="run-1",
        turn_id="turn-1",
        resumable=True,
        watermark_uuid="U2",
        cwd=str(tmp_path),
        resume_attempt_count=0,
    )
    h = _harness(tmp_path, rows=[row], replay_result=_TEXT_FOLD)
    _claim_with_future_lease(h.store, "t", worker_pid=424242)

    # Wrap reclaim + drive to record call order.
    orig_reclaim = h.store.reclaim_running_for_dead_pids

    def _reclaim(*a, **k):
        order.append("background")
        return orig_reclaim(*a, **k)

    h.store.reclaim_running_for_dead_pids = _reclaim  # type: ignore[assignment]

    orig_drive = h.drive

    def _drive(*, initial_messages):
        order.append("foreground")
        return orig_drive(initial_messages=initial_messages)

    h.drive = _drive

    h.build().run()

    assert order.index("background") < order.index("foreground")


# --------------------------------------------------------------------------- #
# 7. OFF no-op + fail-open                                                     #
# --------------------------------------------------------------------------- #


def test_sweep_off_noop(tmp_path, monkeypatch):
    from magi_agent.config.flags import flag_bool

    monkeypatch.delenv("MAGI_DURABLE_STARTUP_RECOVERY_ENABLED", raising=False)
    assert flag_bool("MAGI_DURABLE_STARTUP_RECOVERY_ENABLED") is False

    row = _StoredRow(
        run_id="run-1",
        turn_id="turn-1",
        resumable=True,
        watermark_uuid="U2",
        cwd=str(tmp_path),
        resume_attempt_count=0,
    )
    h = _harness(tmp_path, rows=[row], replay_result=_TEXT_FOLD)
    _claim_with_future_lease(h.store, "t", worker_pid=424242)

    result = h.build().run()

    assert list(result.reclaimed_task_ids) == []
    assert h.runner.runs == []
    assert h.drive_calls == []
    # Crashed row untouched (OFF path byte-identical).
    assert h.store.get("t").status == "running"
    # The checkpoint store is never even listed.
    assert h.ckpt_store.incremented == []


def test_sweep_failopen(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_DURABLE_STARTUP_RECOVERY_ENABLED", "1")
    monkeypatch.setenv("MAGI_DURABLE_FOREGROUND_CONTINUATION_ENABLED", "1")

    row = _StoredRow(
        run_id="run-1",
        turn_id="turn-1",
        resumable=True,
        watermark_uuid="U2",
        cwd=str(tmp_path),
        resume_attempt_count=0,
    )
    h = _harness(tmp_path, rows=[row], replay_result=_TEXT_FOLD)
    h.ckpt_store.raise_on_list = True  # corrupt durable DB at the foreground step

    # Must NOT raise: boot continues.
    result = h.build().run()

    # Foreground was skipped fail-open; no drive, no crash.
    assert h.drive_calls == []
    assert result.resumed_turn_ids == ()
