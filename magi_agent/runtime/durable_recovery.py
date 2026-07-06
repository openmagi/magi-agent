"""WS1 PR1d - StartupRecoverySweep: turn-level boot recovery orchestration.

On a fresh process / pod restart this sweep runs section 3.4:

  1. PRIMARY - durable background-task crash-resume. Calls
     :func:`recover_background_tasks` (PR1b): immediate dead-pid reclaim
     (IGNORING the still-valid lease) + ``release_stale_claims`` +
     ``driver.run_once``. This is the load-bearing value path and is
     AT-LEAST-ONCE: a partially-executed task whose child side-effect already
     fired re-fires on re-run (Correction E / critical 1). True exactly-once is
     a WS7-outbox prerequisite (section 9 gate 7).

  2. OPTIONAL - a WS1-local CONTEXT-ONLY foreground continuation, ONLY when
     ``MAGI_DURABLE_FOREGROUND_CONTINUATION_ENABLED`` is ON. For each
     non-superseded resumable checkpoint it evaluates the WS1-local predicate
     :func:`foreground_resume_admissible` (resumable + in-attempt + the Envelope
     transcript is present) and, on admission, increments
     ``resume_attempt_count`` BEFORE re-entry and re-enters ``_drive`` with the
     TEXT fold from ``replay_messages_up_to``. It does **NOT** call
     ``verify_resume_request`` and does **NOT** recompute persisted digests
     (Correction F: that gate refuses 100% of realistic foreground resumes - the
     policy-availability check always trips at cold boot and the ledger digest
     almost always advanced). ``verify_resume_request`` is retained as an
     armed unit-tested gate for the section 10 / background admission only.

Default-OFF: the whole sweep is a no-op unless
``MAGI_DURABLE_STARTUP_RECOVERY_ENABLED`` is ON. The foreground continuation
additionally requires ``MAGI_DURABLE_FOREGROUND_CONTINUATION_ENABLED``. The
OFF path is byte-identical at boot.

Fail-open: every step is wrapped so a corrupt/unreadable durable store logs and
returns, and boot continues with fresh turns (never a boot crash).

Forbidden imports: google.adk, network, subprocess. The work-queue store +
driver, the Envelope-log replay reader, the ``_drive`` re-entry, the status-event
sink, and ``verify_resume_request`` are all injected so the sweep is unit-testable
without an engine.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Protocol

from magi_agent.config.flags import flag_bool, flag_int

if TYPE_CHECKING:
    from magi_agent.missions.work_queue.driver import WorkQueueDriver
    from magi_agent.missions.work_queue.store import WorkQueueStore

logger = logging.getLogger(__name__)

_STARTUP_FLAG = "MAGI_DURABLE_STARTUP_RECOVERY_ENABLED"
_FOREGROUND_FLAG = "MAGI_DURABLE_FOREGROUND_CONTINUATION_ENABLED"
_MAX_ATTEMPTS_FLAG = "MAGI_DURABLE_MAX_RESUME_ATTEMPTS"
_DEFAULT_MAX_ATTEMPTS = 2


# Type aliases for the injected seams (kept structural / duck-typed so the sweep
# never imports the engine or the heavy session-log/ADK symbols at module top).
ReplayMessages = Callable[..., list[dict[str, str]]]
Drive = Callable[..., None]
Emit = Callable[..., None]
PidAlive = Callable[[int], bool]


class _ForegroundCheckpoint(Protocol):
    """Structural view of the ``StoredCheckpoint`` fields the sweep reads."""

    turn_id: str
    watermark_uuid: str | None
    cwd: str | None
    resume_attempt_count: int
    superseded: bool


class _CheckpointStore(Protocol):
    def list_resumable_turns(self) -> list[object]: ...

    def increment_resume_attempt(self, run_id: str, turn_id: str) -> int: ...


@dataclass(frozen=True)
class StartupRecoveryResult:
    """What the sweep did, for logging / surfacing.

    ``reclaimed_task_ids`` are the background tasks reclaimed by the dead-pid
    sweep (the PRIMARY path). ``resumed_turn_ids`` are the foreground turns that
    re-entered ``_drive`` (only ever non-empty when the foreground flag is ON and
    a checkpoint was admitted). ``skipped`` carries ``(turn_id, reason)`` for
    foreground turns refused by the WS1-local predicate.
    """

    reclaimed_task_ids: tuple[str, ...] = ()
    resumed_turn_ids: tuple[str, ...] = ()
    skipped: tuple[tuple[str, str], ...] = ()


@dataclass
class StartupRecoverySweep:
    """Orchestrate boot recovery (section 3.4). Constructed once at boot.

    All heavy collaborators are injected so the sweep is unit-testable and never
    pulls the engine / ADK into the import graph:

    - ``work_store`` / ``driver``: the existing work-queue store + dispatcher.
    - ``checkpoint_store``: the PR1a ``DurableCheckpointStore`` (only read for
      the OPTIONAL foreground path).
    - ``replay_messages``: ``cli.session_log.replay_messages_up_to`` (the
      CONTEXT-ONLY TEXT reader; no tool entries, no ADK import).
    - ``drive``: the ``_drive(initial_messages=...)`` re-entry (CLI path only).
    - ``emit``: a status-event sink ``emit(event, **fields)``.
    - ``verify_resume_request``: retained but NEVER called on the sweep path
      (injected only so a test can prove it is not called - Correction F).
    """

    work_store: WorkQueueStore
    driver: WorkQueueDriver
    checkpoint_store: _CheckpointStore | None = None
    replay_messages: ReplayMessages | None = None
    drive: Drive | None = None
    emit: Emit | None = None
    verify_resume_request: Callable[..., object] | None = None
    pid_alive: PidAlive | None = None
    now: int | None = None

    def run(self) -> StartupRecoveryResult:
        """Run the boot sweep. No-op + byte-identical when the master flag is OFF.

        Fail-open throughout: any unexpected error logs at debug and returns
        whatever was accomplished so far; boot always continues.
        """
        if not flag_bool(_STARTUP_FLAG):
            return StartupRecoveryResult()

        reclaimed: tuple[str, ...] = ()
        # ----- 1. PRIMARY: background dead-pid reclaim (drain first) --------
        try:
            from magi_agent.missions.work_queue.recovery import (  # noqa: PLC0415
                recover_background_tasks,
            )

            reclaimed = recover_background_tasks(
                self.work_store,
                self.driver,
                enabled=True,
                pid_alive=self.pid_alive,
                now=self.now,
            )
        except Exception:  # noqa: BLE001 - fail-open: boot continues
            logger.debug("durable startup sweep: background reclaim failed", exc_info=True)

        # ----- 1b. PR-M7 hosted projection: restart-recovery seam (7.2) -----
        # Tell the hosted receiver to abandon/resume its running missions from
        # before this boot. Fail-open and non-blocking; inert unless the hosted
        # projector config is present (naturally a no-op on OSS local).
        try:
            import datetime as _dt  # noqa: PLC0415

            from magi_agent.missions.projector import (  # noqa: PLC0415
                notify_restart_recovery,
            )

            notify_restart_recovery(
                started_at=_dt.datetime.now(tz=_dt.timezone.utc).isoformat()
            )
        except Exception:  # noqa: BLE001 - fail-open: boot continues
            logger.debug("durable startup sweep: restart-recovery projection failed", exc_info=True)

        # ----- 2. OPTIONAL: WS1-local context-only foreground continuation --
        resumed: list[str] = []
        skipped: list[tuple[str, str]] = []
        if flag_bool(_FOREGROUND_FLAG):
            try:
                self._run_foreground(resumed, skipped)
            except Exception:  # noqa: BLE001 - fail-open: boot continues
                logger.debug(
                    "durable startup sweep: foreground continuation failed",
                    exc_info=True,
                )

        return StartupRecoveryResult(
            reclaimed_task_ids=tuple(reclaimed),
            resumed_turn_ids=tuple(resumed),
            skipped=tuple(skipped),
        )

    # ------------------------------------------------------------------ #
    # Foreground continuation (OPTIONAL, flag ON)                         #
    # ------------------------------------------------------------------ #

    def _run_foreground(
        self, resumed: list[str], skipped: list[tuple[str, str]]
    ) -> None:
        if self.checkpoint_store is None:
            return
        rows = self.checkpoint_store.list_resumable_turns()
        max_attempts = _resolve_max_attempts()
        for row in rows:
            run_id = _run_id_of(row)
            turn_id = getattr(row, "turn_id", "")
            admissible, reason, messages = self._evaluate(row, max_attempts)
            if not admissible:
                self._emit("durable_resume_skipped", turn_id=turn_id, reason=reason)
                skipped.append((turn_id, reason or "unknown"))
                continue
            # No live re-drive consumer (serve path, or before a later PR wires a
            # terminal consumer): do NOT burn the resume-attempt budget or report a
            # resume that did not happen. The continuation is inert without a
            # consumer, so a premature flag flip cannot poison checkpoints.
            if self.drive is None:
                self._emit("durable_resume_no_consumer", turn_id=turn_id)
                skipped.append((turn_id, "no_drive_consumer"))
                continue
            # Increment BEFORE re-entry so a crash-on-resume still counts (E11).
            self.checkpoint_store.increment_resume_attempt(run_id, turn_id)
            self._emit("durable_resume_context_only", turn_id=turn_id)
            self.drive(initial_messages=messages)
            resumed.append(turn_id)

    def _evaluate(
        self, row: object, max_attempts: int
    ) -> tuple[bool, str | None, list[dict[str, str]]]:
        """The WS1-local ``foreground_resume_admissible`` evaluation.

        Returns ``(admissible, reason, messages)``. ``reason`` is set only on
        refusal. ``messages`` is the context-only TEXT fold on admission. It does
        NOT compute any digest and does NOT call ``verify_resume_request``
        (Correction F).
        """
        return foreground_resume_admissible(
            row,
            max_attempts=max_attempts,
            replay_messages=self.replay_messages,
        )

    def _emit(self, event: str, **fields: object) -> None:
        if self.emit is None:
            return
        try:
            self.emit(event, **fields)
        except Exception:  # noqa: BLE001 - a status sink must never break boot
            logger.debug("durable startup sweep: emit failed", exc_info=True)


def foreground_resume_admissible(
    row: object,
    *,
    max_attempts: int,
    replay_messages: ReplayMessages | None,
) -> tuple[bool, str | None, list[dict[str, str]]]:
    """WS1-local foreground-continuation predicate (Correction F).

    Admits a checkpoint row ONLY when ALL hold:

    1. non-superseded (the store already filters, but re-checked defensively),
    2. ``checkpoint.resumable is True`` (the section 0.5 side-effect classifier -
       the real safety property), reason ``not_resumable`` otherwise,
    3. ``resume_attempt_count < max_attempts`` (the E11/R6 bound), reason
       ``max_resume_attempts`` otherwise,
    4. the Envelope transcript is present: ``replay_messages_up_to`` returns a
       NON-EMPTY context-only TEXT fold, reason ``transcript_unavailable``
       otherwise (a WS1-local reason, distinct from the canonical
       ``verify_resume_request`` codes - E7).

    Returns ``(admissible, reason, messages)``. It does NOT compute or compare a
    digest and does NOT call ``verify_resume_request`` (it would refuse 100% of
    realistic foreground resumes).
    """
    if getattr(row, "superseded", False):
        return (False, "superseded", [])

    checkpoint = getattr(row, "checkpoint", None)
    resumable = bool(getattr(checkpoint, "resumable", False))
    if not resumable:
        return (False, "not_resumable", [])

    attempts = int(getattr(row, "resume_attempt_count", 0) or 0)
    if attempts >= max_attempts:
        return (False, "max_resume_attempts", [])

    watermark = getattr(row, "watermark_uuid", None)
    cwd = getattr(row, "cwd", None)
    if not watermark or not cwd or replay_messages is None:
        return (False, "transcript_unavailable", [])

    run_id = _run_id_of(row)
    try:
        messages = replay_messages(run_id, cwd=cwd, up_to_seq=watermark)
    except Exception:  # noqa: BLE001 - unreadable transcript == fresh start
        logger.debug("durable startup sweep: transcript replay failed", exc_info=True)
        return (False, "transcript_unavailable", [])

    if not messages:
        return (False, "transcript_unavailable", [])

    return (True, None, list(messages))


def run_startup_recovery_from_env(
    *,
    drive: Drive | None = None,
    emit: Emit | None = None,
) -> StartupRecoveryResult:
    """Boot-hook convenience: build the sweep from env and run it, fail-open.

    This is the CLI/serve entrypoint seam (design §3.4 / PR1d boot hooks). It is
    a strict no-op + byte-identical when ``MAGI_DURABLE_STARTUP_RECOVERY_ENABLED``
    is OFF: it returns immediately WITHOUT importing or touching the work-queue
    store, the checkpoint store, or the durable sqlite file.

    When the master flag is ON it composes the existing local work-queue store +
    driver (the PRIMARY background reclaim path) and, only when the foreground
    flag is additionally ON, the PR1a ``DurableCheckpointStore`` + the
    context-only ``replay_messages_up_to`` reader for the OPTIONAL foreground
    continuation. ``drive`` is the ``_drive(initial_messages=...)`` re-entry
    supplied by the live CLI turn (when there is a real terminal consumer); when
    it is ``None`` the foreground continuation still evaluates admissibility and
    increments the attempt counter but does not re-drive (serve path / no live
    consumer, design §10).

    Every construction step is wrapped: any error logs at debug and returns an
    empty result so boot always continues.
    """
    if not flag_bool(_STARTUP_FLAG):
        return StartupRecoveryResult()

    try:
        sweep = _build_sweep_from_env(drive=drive, emit=emit)
    except Exception:  # noqa: BLE001 - fail-open: boot continues with fresh turns
        logger.debug("durable startup sweep: construction failed", exc_info=True)
        return StartupRecoveryResult()

    if sweep is None:
        return StartupRecoveryResult()

    try:
        return sweep.run()
    except Exception:  # noqa: BLE001 - fail-open: boot continues with fresh turns
        logger.debug("durable startup sweep: run failed", exc_info=True)
        return StartupRecoveryResult()


def _build_sweep_from_env(
    *,
    drive: Drive | None,
    emit: Emit | None,
) -> StartupRecoverySweep | None:
    """Compose a :class:`StartupRecoverySweep` from the local env, or None.

    Imports are deferred to the function body so the OFF path (which never calls
    this) keeps the boot import graph byte-identical. The foreground collaborators
    are wired only when ``MAGI_DURABLE_FOREGROUND_CONTINUATION_ENABLED`` is ON.
    """
    from magi_agent.gateway.watchers import build_local_work_queue_driver
    from magi_agent.missions.work_queue.store import (
        SqliteWorkQueueStore,
        work_queue_db_path_from_env,
    )

    # The store handle the sweep reclaims through and the driver's own store both
    # resolve to the SAME local sqlite path (``work_queue_db_path_from_env``), so
    # the dead-pid reclaim and the dispatch tick operate on one DB. ``recover_
    # background_tasks`` (PR1b) takes store + driver separately by contract.
    store = SqliteWorkQueueStore(work_queue_db_path_from_env())
    driver = build_local_work_queue_driver()

    checkpoint_store: _CheckpointStore | None = None
    replay: ReplayMessages | None = None
    if flag_bool(_FOREGROUND_FLAG):
        from magi_agent.cli.session_log import replay_messages_up_to
        from magi_agent.storage.durable_checkpoint_store import (
            DurableCheckpointStore,
        )

        checkpoint_store = DurableCheckpointStore()
        replay = replay_messages_up_to

    return StartupRecoverySweep(
        work_store=store,
        driver=driver,
        checkpoint_store=checkpoint_store,
        replay_messages=replay,
        drive=drive,
        emit=emit,
    )


def _run_id_of(row: object) -> str:
    checkpoint = getattr(row, "checkpoint", None)
    return str(getattr(checkpoint, "run_id", "") or "")


def _resolve_max_attempts() -> int:
    value = flag_int(_MAX_ATTEMPTS_FLAG)
    if value is None or value < 1:
        return _DEFAULT_MAX_ATTEMPTS
    return value


__all__ = [
    "StartupRecoverySweep",
    "StartupRecoveryResult",
    "foreground_resume_admissible",
    "run_startup_recovery_from_env",
]
