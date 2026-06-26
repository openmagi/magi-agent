"""WS1 PR1e - profile activation + ON-path smoke (the #641-class guard).

PR1e flips the WS1 durable substrate ON for the local ``full`` profile (and
``lab``, which layers on top of ``full``):

  * ``MAGI_DURABLE_LOCAL_WRITES_ENABLED`` (the master sqlite-write gate),
  * ``MAGI_DURABLE_CHECKPOINTS_ENABLED`` (headless-tap checkpoint emission),
  * ``MAGI_DURABLE_STARTUP_RECOVERY_ENABLED`` (the boot sweep),
  * ``MAGI_WORK_QUEUE_EXECUTOR_ENABLED`` (the dispatcher tick), and
  * ``MAGI_CLI_SESSION_LOG_ENABLED`` (flipped 0 -> 1: the Envelope-log TEXT
    source the context-only foreground resume reads).

It deliberately does NOT enable ``MAGI_DURABLE_FOREGROUND_CONTINUATION_ENABLED``
(opt-in only; the v1 primary value is T1 background reclaim). The safe / eval /
off profiles keep every one of these OFF.

The ON-path smoke (:func:`test_on_path_smoke`) re-invokes the REAL CLI-entrypoint
boot hook ``run_startup_recovery_from_env`` (the same function ``cli/app.py``
calls, NOT a direct ``StartupRecoverySweep.run()``), so the #641-class "flag
flipped but the path never runs under realistic boot" false-green is caught.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

import pytest

from magi_agent.config.env import cli_session_log_enabled
from magi_agent.config.flags import flag_bool
from magi_agent.runtime.local_defaults import (
    apply_local_eval_runtime_defaults,
    apply_local_full_runtime_defaults,
)

# The four durable flags + the executor that the full profile activates.
_DURABLE_PROFILE_FLAGS = (
    "MAGI_DURABLE_LOCAL_WRITES_ENABLED",
    "MAGI_DURABLE_CHECKPOINTS_ENABLED",
    "MAGI_DURABLE_STARTUP_RECOVERY_ENABLED",
    "MAGI_WORK_QUEUE_EXECUTOR_ENABLED",
)
_SESSION_LOG_FLAG = "MAGI_CLI_SESSION_LOG_ENABLED"
_FOREGROUND_FLAG = "MAGI_DURABLE_FOREGROUND_CONTINUATION_ENABLED"

# Every MAGI_* knob the profile tests resolve must be cleared first so Kevin's
# exported shell env (MAGI_MEMORY_*=1, provider keys, ...) cannot give a false
# green (R4: non-hermetic suites are the documented hazard).
_HERMETIC_KEYS = (
    *_DURABLE_PROFILE_FLAGS,
    _SESSION_LOG_FLAG,
    _FOREGROUND_FLAG,
    "MAGI_RUNTIME_PROFILE",
    "MAGI_AGENT_LOCAL_FULL_RUNTIME_DEFAULTS",
)


@pytest.fixture
def hermetic_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for key in _HERMETIC_KEYS:
        monkeypatch.delenv(key, raising=False)
    yield


# --------------------------------------------------------------------------- #
# Profile activation (the #641-class guard at the resolution layer)            #
# --------------------------------------------------------------------------- #


def test_full_profile_enables_durable(hermetic_env: None) -> None:
    env: dict[str, str] = {}
    apply_local_full_runtime_defaults(env)
    # The flag_bool-registered durable flags + the executor resolve ON.
    for flag in _DURABLE_PROFILE_FLAGS:
        assert env.get(flag) == "1", flag
        assert flag_bool(flag, env=env) is True, flag
    # MAGI_CLI_SESSION_LOG_ENABLED is read via its dedicated helper (not the
    # flag registry), so assert it through cli_session_log_enabled.
    assert env.get(_SESSION_LOG_FLAG) == "1"
    assert cli_session_log_enabled(env) is True


def test_full_profile_stages_session_log_on(hermetic_env: None) -> None:
    env: dict[str, str] = {}
    apply_local_full_runtime_defaults(env)
    # The v3 replay source (the deliberate behavior flip), resolved through the
    # single-source-of-truth helper, NOT the obs MAGI_SESSION_TRANSCRIPT_ENABLED.
    assert cli_session_log_enabled(env) is True


def test_full_profile_does_not_enable_foreground_continuation(
    hermetic_env: None,
) -> None:
    env: dict[str, str] = {}
    apply_local_full_runtime_defaults(env)
    # Opt-in only: the v1 full profile delivers T1 background reclaim, never the
    # foreground continuation.
    assert _FOREGROUND_FLAG not in env
    assert flag_bool(_FOREGROUND_FLAG, env=env) is False


@pytest.mark.parametrize("profile", ["safe", "off", "minimal", "conservative"])
def test_safe_profile_keeps_durable_off(
    hermetic_env: None, profile: str
) -> None:
    env = {"MAGI_RUNTIME_PROFILE": profile}
    apply_local_full_runtime_defaults(env)
    for flag in (*_DURABLE_PROFILE_FLAGS, _FOREGROUND_FLAG):
        assert flag not in env, f"{profile}:{flag}"
        assert flag_bool(flag, env=env) is False, f"{profile}:{flag}"
    # Session log read via its dedicated helper, also OFF on safe profiles.
    assert _SESSION_LOG_FLAG not in env, f"{profile}:{_SESSION_LOG_FLAG}"
    assert cli_session_log_enabled(env) is False, profile


def test_eval_profile_keeps_durable_off(hermetic_env: None) -> None:
    # The eval overlay registers MAGI_CLI_SESSION_LOG_ENABLED at "0" and never
    # touches the durable flags; it must NOT inherit the full-profile flip.
    env: dict[str, str] = {}
    apply_local_eval_runtime_defaults(env)
    assert env.get(_SESSION_LOG_FLAG) == "0"
    assert cli_session_log_enabled(env) is False
    for flag in (*_DURABLE_PROFILE_FLAGS, _FOREGROUND_FLAG):
        assert flag not in env, flag


def test_explicit_durable_off_overrides_full_profile(hermetic_env: None) -> None:
    # setdefault semantics: an explicit operator "0" walks the feature back.
    env = {flag: "0" for flag in (*_DURABLE_PROFILE_FLAGS, _SESSION_LOG_FLAG)}
    apply_local_full_runtime_defaults(env)
    for flag in (*_DURABLE_PROFILE_FLAGS, _SESSION_LOG_FLAG):
        assert env[flag] == "0", flag


def test_lab_profile_inherits_durable_on(hermetic_env: None) -> None:
    # lab layers on top of the full overlay, so it inherits the activation.
    from magi_agent.runtime.local_defaults import apply_lab_runtime_defaults

    env: dict[str, str] = {}
    apply_lab_runtime_defaults(env)
    for flag in (*_DURABLE_PROFILE_FLAGS, _SESSION_LOG_FLAG):
        assert env.get(flag) == "1", flag
    # lab still does not opt into the foreground continuation.
    assert _FOREGROUND_FLAG not in env


# --------------------------------------------------------------------------- #
# Hosted overlay: durable checkpoints/recovery ON, local writes OFF (gate-1)   #
# --------------------------------------------------------------------------- #


def test_hosted_resilience_durable_writes_off_pending_gate1() -> None:
    from magi_agent.runtime.hosted_defaults import apply_hosted_runtime_defaults

    env = {"MAGI_DEPLOYMENT": "hosted", "MAGI_CONTROL_STAGE": "resilience"}
    apply_hosted_runtime_defaults(env)
    # Recovery + checkpoint emission are wired at resilience and above ...
    assert env["MAGI_DURABLE_CHECKPOINTS_ENABLED"] == "1"
    assert env["MAGI_DURABLE_STARTUP_RECOVERY_ENABLED"] == "1"
    # ... but the master sqlite-write gate stays OFF pending the section-9
    # gate-1 PVC sign-off, so the substrate is inert on hosted.
    assert env["MAGI_DURABLE_LOCAL_WRITES_ENABLED"] == "0"
    # And the foreground continuation stays OFF on hosted.
    assert _FOREGROUND_FLAG not in env


def test_hosted_off_stage_sets_no_durable_flags() -> None:
    from magi_agent.runtime.hosted_defaults import apply_hosted_runtime_defaults

    env = {"MAGI_DEPLOYMENT": "hosted", "MAGI_CONTROL_STAGE": "off"}
    apply_hosted_runtime_defaults(env)
    for flag in (
        "MAGI_DURABLE_CHECKPOINTS_ENABLED",
        "MAGI_DURABLE_STARTUP_RECOVERY_ENABLED",
        "MAGI_DURABLE_LOCAL_WRITES_ENABLED",
    ):
        assert flag not in env, flag


# --------------------------------------------------------------------------- #
# ON-path smoke - the #641 guard: re-invoke the REAL boot hook                 #
# --------------------------------------------------------------------------- #

_DEAD_PID = 424242  # never alive; reclaim_running_for_dead_pids must flip it


def _seed_envelope_log(cwd: Path, session_id: str) -> str:
    """Write a real Envelope log via the CLI write path; return the watermark."""
    from magi_agent.cli.session_log import SessionLog
    from magi_agent.runtime.events import RuntimeEvent

    log = SessionLog(bot_id="", session_id=session_id, cwd=str(cwd))
    log.append(
        RuntimeEvent(
            type="token",
            payload={"type": "text_delta", "delta": "do a thing"},
            turn_id="turn-1",
        )
    )
    watermark = log.append(
        RuntimeEvent(
            type="tool",
            payload={"type": "tool_end", "id": "call_1", "name": "read_file", "status": "ok"},
            turn_id="turn-1",
        )
    )
    log.close()
    return watermark


def _seed_evidence(cwd: Path, session_id: str) -> Path:
    evidence_dir = cwd / ".magi" / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    path = evidence_dir / f"{session_id}.jsonl"
    rows = [
        {"sessionId": session_id, "turnId": "turn-1", "toolName": "read_file", "status": "ok"},
    ]
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")
    return evidence_dir


def _emit_real_checkpoint(cwd: Path, session_id: str, watermark: str) -> None:
    """Write a real checkpoint via the actual headless-tap emit code path."""
    from magi_agent.cli.headless import CheckpointTapContext, _emit_tap_checkpoint
    from magi_agent.storage.durable_checkpoint_store import DurableCheckpointStore

    store = DurableCheckpointStore()  # env-built: resolves to MAGI_WORK_QUEUE_DB_PATH
    ctx = CheckpointTapContext(
        store=store,
        run_id=session_id,  # the boot sweep replays the Envelope log keyed by run_id
        turn_id="turn-1",
        session_id=session_id,
        cwd=str(cwd),
    )
    _emit_tap_checkpoint(
        ctx,
        watermark_uuid=watermark,
        step_id="step-0002",
        pending_tool_ids=(),
        last_completed_tool_name="read_file",
    )
    store.close()


def _enqueue_crashed_background_task(db_path: Path) -> str:
    """Create a background task claimed by a DEAD worker pid (a mid-turn crash)."""
    from magi_agent.missions.work_queue.models import WorkTask
    from magi_agent.missions.work_queue.store import SqliteWorkQueueStore

    store = SqliteWorkQueueStore(db_path)
    store.create(WorkTask(id="bg-1", title="bg", status="ready", created_at=1))
    claimed = store.claim("bg-1", claimer="dead-worker", now=1000, worker_pid=_DEAD_PID)
    assert claimed is not None and claimed.status == "running"
    return "bg-1"


def _activate_durable_env(
    monkeypatch: pytest.MonkeyPatch, db_path: Path, evidence_dir: Path
) -> None:
    # env-cleared activation mirroring the ON-path CI job (hermetic).
    for key in _HERMETIC_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("MAGI_WORK_QUEUE_DB_PATH", str(db_path))
    monkeypatch.setenv("MAGI_EVIDENCE_LEDGER_DIR", str(evidence_dir))
    monkeypatch.setenv("MAGI_EVIDENCE_LEDGER_LIFECYCLE_ENABLED", "1")
    for flag in _DURABLE_PROFILE_FLAGS:
        monkeypatch.setenv(flag, "1")
    monkeypatch.setenv(_SESSION_LOG_FLAG, "1")
    # Default-OFF live runner -> the safe stub dispatcher (no network/ADK).
    monkeypatch.delenv("MAGI_BACKGROUND_LIVE_RUNNER_ENABLED", raising=False)


def test_on_path_smoke(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """PRIMARY: the REAL boot hook reclaims a dead-pid background task.

    Enqueues a background task, claims it with a dead worker pid (a mid-turn
    crash), writes the Envelope log + a real checkpoint from the headless tap,
    then re-invokes ``run_startup_recovery_from_env`` (the app.py boot hook, NOT
    a hand-built sweep) and asserts at-least-once background reclaim. The
    foreground continuation is OFF, so no turn is re-driven.
    """
    from magi_agent.runtime.durable_recovery import run_startup_recovery_from_env

    db_path = tmp_path / "work_queue.db"
    session_id = "run-smoke-1"
    cwd = tmp_path / "ws"
    cwd.mkdir()

    evidence_dir = _seed_evidence(cwd, session_id)
    _activate_durable_env(monkeypatch, db_path, evidence_dir)

    task_id = _enqueue_crashed_background_task(db_path)
    watermark = _seed_envelope_log(cwd, session_id)
    _emit_real_checkpoint(cwd, session_id, watermark)

    # The headless tap actually wrote a checkpoint (proves the CLI-path emit).
    from magi_agent.storage.durable_checkpoint_store import DurableCheckpointStore

    rows = DurableCheckpointStore(db_path).list_resumable_turns()
    assert any(r.turn_id == "turn-1" and r.checkpoint.resumable for r in rows)

    # Re-invoke the REAL boot hook (the #641 guard). Foreground OFF.
    result = run_startup_recovery_from_env()

    # PRIMARY: at-least-once background reclaim happened on the real boot path.
    assert task_id in result.reclaimed_task_ids
    # No foreground re-drive (continuation flag OFF).
    assert result.resumed_turn_ids == ()

    # The reclaimed task is no longer held by the dead worker pid.
    from magi_agent.missions.work_queue.store import SqliteWorkQueueStore

    store = SqliteWorkQueueStore(db_path)
    reloaded = store.get(task_id)
    assert reloaded is not None
    assert reloaded.worker_pid != _DEAD_PID

    # + DIFFERENT-row dedupe seam is present on the same store the hook used.
    assert store.completed_task_for_key("nope", exclude_task_id="x") is None


def test_on_path_smoke_foreground_continuation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OPTIONAL variant: with the continuation flag ON, the REAL boot hook
    performs a context-only foreground re-entry.

    Proves the Envelope log + checkpoint are actually written from the headless
    tap on the CLI path: the boot hook reads them back and calls
    ``drive(initial_messages=...)`` with the replayed TEXT fold (no tool
    entries), admitted by the WS1-local predicate (NOT ``verify_resume_request``).
    """
    from magi_agent.runtime.durable_recovery import run_startup_recovery_from_env

    db_path = tmp_path / "work_queue.db"
    session_id = "run-smoke-2"
    cwd = tmp_path / "ws"
    cwd.mkdir()

    evidence_dir = _seed_evidence(cwd, session_id)
    _activate_durable_env(monkeypatch, db_path, evidence_dir)
    monkeypatch.setenv(_FOREGROUND_FLAG, "1")

    watermark = _seed_envelope_log(cwd, session_id)
    _emit_real_checkpoint(cwd, session_id, watermark)

    drive_calls: list[list[dict[str, str]]] = []

    def _drive(*, initial_messages: list[dict[str, str]]) -> None:
        drive_calls.append(list(initial_messages))

    result = run_startup_recovery_from_env(drive=_drive)

    # The boot hook read the real Envelope log + checkpoint and re-entered drive.
    assert "turn-1" in result.resumed_turn_ids
    assert len(drive_calls) == 1
    folded = drive_calls[0]
    # Context-only TEXT fold: carries the assistant text, NO tool entries.
    assert folded, "expected a non-empty replayed text fold"
    assert all("content" in m and "role" in m for m in folded)
    assert all("read_file" not in str(m.get("content", "")) for m in folded)
