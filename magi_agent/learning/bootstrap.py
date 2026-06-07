"""Learning bootstrap — PR9b (turnkey safe reflect tier on startup).

PR9a flipped the Learning Layer to a **layered opt-out** model: the SAFE reflect
tier (real LOCAL session read → DETERMINISTIC label → eval-gate → ``proposed``
items in the LOCAL store → dashboard for human approval) is ON by default, while
the OPT-IN tier (LLM labeler, prompt injection, live/authority writes) stays OFF.

But PR9a is config + readiness ONLY — nothing actually RUNS the reflection loop.
``run_reflection`` / ``LearningReflectionCronJob`` are library seams with no
caller in app startup.

PR9b adds the **bootstrap** that wires them up so a default install actually runs
the safe reflect tier in the background:

    operator's local sessions (``RealTranscriptSource`` over the SAME session DB
    the runtime writes) → structural signal extraction → deterministic label →
    eval gate → store ``proposed`` items → dashboard shows them for approval.

**No prompt injection, no LLM cost, no behavior change** until the operator opts
in.  Because this is now a DEFAULT-ON feature, EVERY path is **fail-open**: any
construction or per-pass error is caught + logged and NEVER propagates, so a
broken learning layer degrades silently and never crashes app startup, breaks a
request, or corrupts state.

This module NEVER flips a ``Literal[False]`` authority flag.  Binding the real
local transcript source + deterministic labeler is the **reflect tier**, which is
default-ready via PR9a's gate/readiness defaults; the three frozen attestation
flags on ``LearningReflectionConfig`` (``llm_attached`` /
``production_write_enabled`` / ``real_transcript_source_attached``) stay False.
The injection + live + LLM-labeler tiers remain config-gated opt-in.

No agent-core files are touched.  The bootstrap reads the durable session store
read-only via the ``SessionPersistenceReader`` Protocol (the same surface PR7's
``RealTranscriptSource`` uses) and writes ``proposed`` items to the LOCAL learning
store — exactly the safe operations PR9a designated.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from magi_agent.gates.learning_readiness import resolve_learning_reflect_tier_mode
from magi_agent.learning.config import LearningConfig, resolve_learning_config

if TYPE_CHECKING:  # pragma: no cover - import-time typing only
    from magi_agent.harness.learning_executor import LearningReflectionResult
    from magi_agent.learning.labeler import Labeler
    from magi_agent.learning.live import (
        LabelerModelClient,
        SessionPersistenceReader,
    )
    from magi_agent.learning.store import LearningStore

logger = logging.getLogger(__name__)

#: Labeler kind reported by :attr:`LearningBootstrap.labeler_kind`.
LabelerKind = str  # "deterministic" | "llm"


class LearningBootstrap:
    """Runs the safe reflect tier in the background on a default install.

    Lifecycle::

        await start()  → resolve config; if the safe reflect tier is on, build
                         the deps (learning store + real transcript source +
                         deterministic labeler + cron job) and schedule a thin
                         background timer that calls run_once() every interval.
                         If anything is off or a dep build fails → INERT no-op.
        run_once()     → one watermark-incremental reflection pass (cron
                         trigger_now → run_reflection → eval gate → propose).
                         Non-reentrant; fail-open.
        await stop()   → cancel the background task cleanly.

    Everything is fail-open: a disabled tier, a missing session DB, or a per-pass
    error never propagates — the bootstrap simply becomes/stays inert.
    """

    def __init__(
        self,
        *,
        learning_store: LearningStore | None = None,
        learning_store_factory: Callable[[], LearningStore] | None = None,
        session_reader: SessionPersistenceReader | None = None,
        session_reader_factory: Callable[[], SessionPersistenceReader] | None = None,
        app_name: str = "magi",
        user_id: str | None = None,
        tenant_id: str = "local",
        model_client: LabelerModelClient | None = None,
        config: LearningConfig | None = None,
    ) -> None:
        # Dependency seams.  A factory is preferred for fail-open testing (it can
        # raise during start() and be swallowed); a pre-built instance is used by
        # the common path and by tests that want to assert on the store after.
        self._learning_store = learning_store
        self._learning_store_factory = learning_store_factory
        self._session_reader = session_reader
        self._session_reader_factory = session_reader_factory
        self._app_name = app_name
        self._user_id = user_id
        self._tenant_id = tenant_id
        self._model_client = model_client
        self._explicit_config = config

        # Resolved-at-start state.
        self._cron: object | None = None
        self._labeler: Labeler | None = None
        self._store: LearningStore | None = None
        self._reader: SessionPersistenceReader | None = None
        self._active = False
        self._interval_seconds: int = 0
        self._labeler_kind: LabelerKind = "deterministic"

        # Background timer + non-reentrancy guard.
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Observable state (for tests / health surfaces)
    # ------------------------------------------------------------------

    @property
    def active(self) -> bool:
        """True once start() bound the deps and the reflect tier is on."""
        return self._active

    @property
    def labeler_kind(self) -> LabelerKind:
        """The labeler actually selected: ``"deterministic"`` or ``"llm"``."""
        return self._labeler_kind

    @property
    def watermark(self) -> str | None:
        """The cron job's current incremental watermark (or None)."""
        cron = self._cron
        return getattr(cron, "watermark", None)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Resolve config and, if the safe reflect tier is on, schedule the loop.

        Fail-open: ANY error here is caught + logged and leaves the bootstrap
        inert.  A no-op start (tier off or build failure) never raises.
        """
        try:
            self._start_inner()
        except Exception:  # noqa: BLE001 - fail-open: never crash startup
            logger.warning(
                "learning bootstrap start failed; learning layer is inert",
                exc_info=True,
            )
            self._active = False
            self._cron = None

    def _start_inner(self) -> None:
        config = self._explicit_config or resolve_learning_config()

        # Safe reflect tier gate: master AND reflection effective AND the PR9a
        # reflect-tier resolver agrees.  Anything off ⇒ inert no-op.
        if not config.reflection_effective:
            logger.debug("learning bootstrap: reflection tier off; inert")
            return
        if resolve_learning_reflect_tier_mode(config) != "reflect":
            logger.debug("learning bootstrap: reflect tier not ready; inert")
            return

        # Build deps (each may raise; the outer start() swallows + logs).
        store = self._resolve_learning_store()
        reader = self._resolve_session_reader()
        labeler = self._resolve_labeler(config)

        from magi_agent.harness.cron_runtime import LearningReflectionCronJob
        from magi_agent.harness.learning_executor import LearningReflectionConfig
        from magi_agent.learning.live import RealTranscriptSource

        source = RealTranscriptSource(
            store=reader, app_name=self._app_name, user_id=self._user_id
        )

        # Keep direct refs so stop() can close the underlying connections.
        self._store = store
        self._reader = reader
        # The cron job's trigger_now() uses run_reflection's DEFAULT
        # (deterministic) labeler; when an explicit non-default labeler is
        # selected it is applied via _run_reflection_with_labeler in run_once().
        self._labeler = labeler
        self._cron = LearningReflectionCronJob(
            source=source,
            store=store,
            # enabled=True so run_reflection's config-gate passes; the env gate is
            # also consulted by run_reflection and is ON by default (PR9a).
            config=LearningReflectionConfig(enabled=True),
            watermark=None,
        )
        # ``reflection_interval_hours`` is validated ``gt=0`` by the config model;
        # the ``max(1, ...)`` is defensive belt-and-suspenders against a forged
        # non-validated config so the loop never busy-spins on a zero interval.
        self._interval_seconds = max(1, config.reflection_interval_hours) * 3600
        self._active = True

        # Schedule the thin background timer.  Tests drive run_once() directly,
        # so the loop only sleeps then triggers — no heavy work inline.
        self._task = asyncio.create_task(
            self._loop(), name="learning-bootstrap-loop"
        )

    async def stop(self) -> None:
        """Cancel the background task + close store/reader (idempotent, safe)."""
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001 - never raise on shutdown
                logger.warning(
                    "learning bootstrap stop encountered an error", exc_info=True
                )

        # Close the learning store + session reader connections we own.  Each is
        # best-effort: a missing/raising close() must never propagate on shutdown.
        for resource in (self._store, self._reader):
            close = getattr(resource, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:  # noqa: BLE001 - never raise on shutdown
                    logger.warning(
                        "learning bootstrap resource close failed", exc_info=True
                    )
        self._store = None
        self._reader = None

    # ------------------------------------------------------------------
    # Reflection pass
    # ------------------------------------------------------------------

    async def run_once(self) -> "LearningReflectionResult | None":
        """Run ONE watermark-incremental reflection pass.

        * Returns ``None`` when the bootstrap is inert (tier off / build failed).
        * Non-reentrant: if a pass is already running this call SKIPS (returns
          ``None``) rather than double-processing or racing the watermark.
        * Fail-open: any error inside the pass is caught + logged and ``None`` is
          returned — the watermark is left to the cron job (unchanged on error).
        """
        if not self._active or self._cron is None:
            return None

        # Non-reentrancy: fast-path skip if a pass is already in flight.  The
        # cooperative single-threaded asyncio model makes the locked()-check +
        # acquire race-free (no preemption between them), so an overlapping call
        # skips instead of queueing behind the in-flight pass or double-advancing
        # the watermark.
        if self._lock.locked():
            return None
        async with self._lock:
            try:
                return await self._trigger()
            except Exception:  # noqa: BLE001 - fail-open per pass
                logger.warning(
                    "learning bootstrap reflection pass failed; skipping",
                    exc_info=True,
                )
                return None

    async def _trigger(self) -> "LearningReflectionResult | None":
        cron = self._cron
        assert cron is not None  # guarded by run_once()
        labeler = self._labeler
        if labeler is not None and self._labeler_kind != "deterministic":
            # Non-default labeler selected → run reflection directly so the
            # labeler is threaded through (the cron job's trigger_now does not
            # accept a labeler).  Watermark handled here to stay incremental.
            return await self._run_reflection_with_labeler(cron, labeler)
        # GOVERNANCE: auto_activate_examples=False so the default-ON reflect tier
        # leaves EVERY produced item ``proposed`` for human approval — nothing is
        # auto-activated (and thus injectable) without review.
        return await cron.trigger_now(
            tenant_id=self._tenant_id, auto_activate_examples=False
        )

    async def _run_reflection_with_labeler(
        self, cron: object, labeler: "Labeler"
    ) -> "LearningReflectionResult":
        from magi_agent.harness.learning_executor import run_reflection

        result = await run_reflection(
            source=getattr(cron, "_source", None),
            since=getattr(cron, "watermark", None),
            config=getattr(cron, "_config", None),
            store=getattr(cron, "_store", None),
            labeler=labeler,
            tenant_id=self._tenant_id,
            # GOVERNANCE: same as the trigger_now path — never auto-activate.
            auto_activate_examples=False,
        )
        if result.status == "ok" and result.watermark is not None:
            cron.watermark = result.watermark  # type: ignore[attr-defined]
        return result

    # ------------------------------------------------------------------
    # Background loop (thin — sleep then trigger)
    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._interval_seconds)
                await self.run_once()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - loop must never die loudly
            # An exited loop means NO MORE PASSES will run — escalate to error so
            # the silent degradation is at least visible in logs.
            logger.error("learning bootstrap loop exited on error", exc_info=True)

    # ------------------------------------------------------------------
    # Dep resolution
    # ------------------------------------------------------------------

    def _resolve_learning_store(self) -> "LearningStore":
        if self._learning_store is not None:
            return self._learning_store
        if self._learning_store_factory is not None:
            return self._learning_store_factory()
        from magi_agent.learning.store import SqliteLearningStore

        return SqliteLearningStore()

    def _resolve_session_reader(self) -> "SessionPersistenceReader":
        if self._session_reader is not None:
            return self._session_reader
        if self._session_reader_factory is not None:
            return self._session_reader_factory()
        # Default: read the SAME durable session DB the runtime writes
        # (.openmagi/sessions.db relative to cwd), read-only.  The agent core is
        # never touched.
        from magi_agent.storage.session_store import (
            SessionSqliteStore,
            SessionStoreConfig,
        )

        return SessionSqliteStore(config=SessionStoreConfig(enabled=True))

    def _resolve_labeler(self, config: LearningConfig) -> "Labeler":
        """Pick the labeler: deterministic default; LLM only when opted in.

        ``MAGI_LEARNING_LABELER=llm`` selects the LLM labeler ONLY when a model
        client is available; with no client it falls back to deterministic + logs
        (never crashes).  The frozen authority flags are untouched either way.
        """
        from magi_agent.learning.labeler import LocalFakeLabeler

        if config.llm_labeler_effective and self._model_client is not None:
            from magi_agent.learning.live import LlmBackedLabeler

            self._labeler_kind = "llm"
            return LlmBackedLabeler(model_client=self._model_client)

        if config.llm_labeler_effective and self._model_client is None:
            logger.warning(
                "learning bootstrap: LLM labeler requested but no model client "
                "available; falling back to the deterministic labeler"
            )
        self._labeler_kind = "deterministic"
        return LocalFakeLabeler()


__all__ = ["LabelerKind", "LearningBootstrap"]
