"""C3 — SkillCurator: inactivity-triggered janitor for agent-authored learned items.

Mirrors Hermes' curator pattern: archive-only (never hard-delete), snapshot/
backup before mutating, pinned items exempt, human-approved active items NOT
archived.  Designed to compose with the Track-A scheduler (callable as a
scheduled job) or be invoked directly.

Env gates
---------
MAGI_SKILL_CURATOR_ENABLED   default OFF.  When off the run() is a pure no-op;
                              no reads, no writes, gate_off=True in the result.
MAGI_SKILL_CURATOR_SHADOW    default ON (shadow-first).  When on, the would-
                              archive set is computed but NO store mutations occur.
                              shadow performs NO mutation to learning_items and
                              writes NO snapshot; it DOES persist last_run_at to
                              prevent re-fire thrash.  shadow=True in the result.

Conservative archive rule
--------------------------
Only ``proposed`` items that are:
  1. agent-authored (``provenance.derived_by == "reflection"``), AND
  2. stale (``updated_at`` older than ``stale_days`` ago, default 30d), AND
  3. NOT pinned (``item.pinned is False``)
are archived.  ``active`` items (human-approved) are NEVER archived regardless
of age.  ``archived`` items are left as-is.

Snapshot / backup
-----------------
Before any mutating archive pass a snapshot blob (JSON list of item dicts in
their PRE-archive state) is written to ``learning_curator_snapshots`` table
(migration 7 in the learning store).  ``get_snapshot(ref)`` returns the list
so the pass is fully reversible.  Shadow mode writes no snapshot.

Inactivity trigger
------------------
``should_run_curator()`` is a pure predicate — it does NOT call the store.
The caller (scheduler job or direct invoke) is responsible for supplying
``last_run_at`` and ``last_activity_at``.  ``SkillCurator.run()`` persists
``last_run_at`` to ``learning_curator_state`` table after a successful
(non-gate-off) run.

Evidence + redaction
--------------------
An ``EvidenceRecord`` is emitted per run.  Fields contain:
  - archivedCount / wouldArchiveCount / pinnedExemptCount (ints)
  - snapshotRef (string or None)
  - tenantId digest (sha256 of tenant_id, not raw)
  - archivedItemDigests (list of sha256 of item ids, NOT raw ids)
  - mode: "shadow" / "live" / "gate_off"
No raw rationale, content, or session text appears in evidence.

Authority flags
---------------
All Literal[False].  This module NEVER spawns agents, NEVER calls network,
NEVER writes outside the injected store.

Scheduler integration
---------------------
``SkillCuratorJob`` wraps ``SkillCurator.run()`` as a plain callable compatible
with Track A's ``ScheduledJobSource`` pattern — the scheduler fires it; this
module provides NO loop driver (YAGNI: Track F provides the daemon).

Forbidden top-level imports: magi_agent.adk_bridge, google.adk, urllib, socket,
subprocess, http, requests — none appear in this module or its local import graph.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from magi_agent.evidence.types import EvidenceRecord, EvidenceSource
from magi_agent.learning.store import SqliteLearningStore
from magi_agent.ops.authority import FalseOnlyAuthorityModel

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_ENV_ENABLED = "MAGI_SKILL_CURATOR_ENABLED"
_ENV_SHADOW = "MAGI_SKILL_CURATOR_SHADOW"

_DEFAULT_INTERVAL_HOURS: float = 168.0       # 7 days
_DEFAULT_STALE_DAYS: int = 30
_DEFAULT_IDLE_THRESHOLD_SECONDS: float = 3600.0  # 1 hour idle = "not active"

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
)

# ---------------------------------------------------------------------------
# Pure inactivity-trigger predicate
# ---------------------------------------------------------------------------


def should_run_curator(
    *,
    now: datetime,
    last_run_at: datetime | None,
    last_activity_at: datetime | None,
    interval_hours: float = _DEFAULT_INTERVAL_HOURS,
    idle_threshold_seconds: float = _DEFAULT_IDLE_THRESHOLD_SECONDS,
) -> bool:
    """Pure predicate: True iff the curator should fire now.

    Conditions (both must hold):
    1. Interval elapsed: ``last_run_at`` is None OR ``now - last_run_at >= interval_hours``.
    2. System idle: ``last_activity_at`` is None OR
       ``now - last_activity_at >= idle_threshold_seconds``.

    This function has no side effects and touches no store.
    """
    # Condition 1: interval elapsed
    if last_run_at is not None:
        elapsed_hours = (now - last_run_at).total_seconds() / 3600.0
        if elapsed_hours < interval_hours:
            return False

    # Condition 2: idle (no recent activity)
    if last_activity_at is not None:
        idle_seconds = (now - last_activity_at).total_seconds()
        if idle_seconds < idle_threshold_seconds:
            return False

    return True


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class CuratorConfig(BaseModel):
    """Frozen config controlling curator gate + shadow behavior."""

    model_config = _MODEL_CONFIG

    enabled: bool = False
    shadow: bool = True
    stale_days: int = Field(default=_DEFAULT_STALE_DAYS, gt=0, alias="staleDays")
    interval_hours: float = Field(
        default=_DEFAULT_INTERVAL_HOURS, gt=0, alias="intervalHours"
    )
    idle_threshold_seconds: float = Field(
        default=_DEFAULT_IDLE_THRESHOLD_SECONDS, gt=0, alias="idleThresholdSeconds"
    )

    @classmethod
    def from_env(cls) -> CuratorConfig:
        """Build config from env vars (evaluated at runtime).

        Env vars read
        -------------
        MAGI_SKILL_CURATOR_ENABLED              truthy string → enabled (default OFF)
        MAGI_SKILL_CURATOR_SHADOW               falsy string → live (default ON/shadow)
        MAGI_SKILL_CURATOR_STALE_DAYS           integer days (default 30)
        MAGI_SKILL_CURATOR_INTERVAL_HOURS       float hours between runs (default 168)
        MAGI_SKILL_CURATOR_IDLE_THRESHOLD_SECONDS  float seconds idle before firing (default 3600)
        """
        enabled = _env_flag(_ENV_ENABLED, default=False)
        shadow = _env_shadow_flag(_ENV_SHADOW, default=True)

        stale_days_raw = os.environ.get("MAGI_SKILL_CURATOR_STALE_DAYS")
        stale_days = _DEFAULT_STALE_DAYS
        if stale_days_raw:
            try:
                stale_days = int(stale_days_raw)
            except (TypeError, ValueError):
                pass

        interval_hours_raw = os.environ.get("MAGI_SKILL_CURATOR_INTERVAL_HOURS")
        interval_hours = _DEFAULT_INTERVAL_HOURS
        if interval_hours_raw:
            try:
                interval_hours = float(interval_hours_raw)
            except (TypeError, ValueError):
                pass

        idle_threshold_raw = os.environ.get("MAGI_SKILL_CURATOR_IDLE_THRESHOLD_SECONDS")
        idle_threshold_seconds = _DEFAULT_IDLE_THRESHOLD_SECONDS
        if idle_threshold_raw:
            try:
                idle_threshold_seconds = float(idle_threshold_raw)
            except (TypeError, ValueError):
                pass

        return cls(
            enabled=enabled,
            shadow=shadow,
            staleDays=stale_days,
            intervalHours=interval_hours,
            idleThresholdSeconds=idle_threshold_seconds,
        )


def _env_flag(name: str, *, default: bool) -> bool:
    # I-2 PR A: delegates to the canonical truthy leaf.
    from magi_agent.config._truthy import env_bool  # noqa: PLC0415

    return env_bool(os.environ, name, default=default)


def _env_shadow_flag(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    clean = raw.strip().lower()
    if clean in {"0", "false", "no", "off"}:
        return False
    return True


# ---------------------------------------------------------------------------
# Authority flags (all Literal[False])
# ---------------------------------------------------------------------------


class CuratorAuthorityFlags(FalseOnlyAuthorityModel):
    """All authority flags are Literal[False] — the curator never spawns agents.

    Force-false is owned by the FalseOnlyAuthorityModel kernel.
    """

    agent_spawned: Literal[False] = Field(default=False, alias="agentSpawned")
    network_call_allowed: Literal[False] = Field(
        default=False, alias="networkCallAllowed"
    )
    live_tool_execution: Literal[False] = Field(
        default=False, alias="liveToolExecution"
    )
    production_channel_write: Literal[False] = Field(
        default=False, alias="productionChannelWrite"
    )


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


class CuratorResult(BaseModel):
    """Frozen result of a single curator run."""

    model_config = _MODEL_CONFIG

    # Gate / mode flags
    gate_off: bool = Field(default=False, alias="gateOff")
    shadow: bool = False

    # Counts
    archived_count: int = Field(default=0, alias="archivedCount")
    would_archive_count: int = Field(default=0, alias="wouldArchiveCount")
    pinned_exempt_count: int = Field(default=0, alias="pinnedExemptCount")
    active_exempt_count: int = Field(default=0, alias="activeExemptCount")

    # Snapshot ref (None in shadow mode or when nothing to archive)
    snapshot_ref: str | None = Field(default=None, alias="snapshotRef")

    # Evidence record
    evidence: EvidenceRecord | None = None

    # Authority flags
    authority_flags: CuratorAuthorityFlags = Field(
        default_factory=CuratorAuthorityFlags,
        alias="authorityFlags",
    )


# ---------------------------------------------------------------------------
# Redaction helpers
# ---------------------------------------------------------------------------


def _id_digest(raw_id: str) -> str:
    """SHA-256 digest of a raw item id — used in evidence to avoid leaking ids."""
    return "sha256:" + hashlib.sha256(raw_id.encode()).hexdigest()


def _tenant_digest(tenant_id: str) -> str:
    return "sha256:" + hashlib.sha256(tenant_id.encode()).hexdigest()


def _build_evidence(
    *,
    mode: str,
    archived_count: int,
    would_archive_count: int,
    pinned_exempt_count: int,
    active_exempt_count: int,
    snapshot_ref: str | None,
    archived_item_ids: list[str],
    tenant_id: str,
    now: datetime,
) -> EvidenceRecord:
    return EvidenceRecord(
        type="custom:SkillCuratorRun",
        status="ok",
        observedAt=int(now.astimezone(UTC).timestamp() * 1000),
        source=EvidenceSource(kind="execution_contract"),
        fields={
            "mode": mode,
            "archivedCount": archived_count,
            "wouldArchiveCount": would_archive_count,
            "pinnedExemptCount": pinned_exempt_count,
            "activeExemptCount": active_exempt_count,
            "snapshotRef": snapshot_ref,
            # Redacted: sha256 digests of item ids, NOT raw ids (always a list)
            "archivedItemDigests": list(_id_digest(i) for i in archived_item_ids),
            "tenantDigest": _tenant_digest(tenant_id),
        },
    )


# ---------------------------------------------------------------------------
# SkillCurator
# ---------------------------------------------------------------------------


class SkillCurator:
    """Inactivity-triggered janitor for agent-authored learned items.

    Usage::

        store = SqliteLearningStore(...)
        curator = SkillCurator(store=store)

        # Direct (explicit config)
        result = curator.run(now=datetime.now(UTC), tenant_id="local",
                             config=CuratorConfig(enabled=True, shadow=False))

        # From env
        result = curator.run(now=datetime.now(UTC), tenant_id="local",
                             config=CuratorConfig.from_env())

    The curator is also composable as a Track-A scheduler job via
    ``SkillCuratorJob``.
    """

    def __init__(self, store: SqliteLearningStore) -> None:
        self._store = store

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        *,
        now: datetime,
        tenant_id: str,
        config: CuratorConfig | None = None,
    ) -> CuratorResult:
        """Execute one curator pass.

        Returns a frozen ``CuratorResult``.  Always fail-open: any unexpected
        store error is caught, logged, and surfaced as a gate_off-style result
        so the caller is never broken.
        """
        cfg = config if config is not None else CuratorConfig.from_env()

        # Gate OFF
        if not cfg.enabled:
            return CuratorResult(
                gateOff=True,
                shadow=cfg.shadow,
                evidence=_build_evidence(
                    mode="gate_off",
                    archived_count=0,
                    would_archive_count=0,
                    pinned_exempt_count=0,
                    active_exempt_count=0,
                    snapshot_ref=None,
                    archived_item_ids=[],
                    tenant_id=tenant_id,
                    now=now,
                ),
            )

        try:
            return self._run_gated(now=now, tenant_id=tenant_id, config=cfg)
        except Exception:
            logger.exception(
                "SkillCurator.run() failed unexpectedly — returning gate_off result"
            )
            return CuratorResult(
                gateOff=True,
                evidence=_build_evidence(
                    mode="error",
                    archived_count=0,
                    would_archive_count=0,
                    pinned_exempt_count=0,
                    active_exempt_count=0,
                    snapshot_ref=None,
                    archived_item_ids=[],
                    tenant_id=tenant_id,
                    now=now,
                ),
            )

    def get_last_run_at(self, *, tenant_id: str = "local") -> datetime | None:
        """Return the persisted last_run_at for *tenant_id*, or None if never run."""
        conn = self._store._get_conn()
        row = conn.execute(
            "SELECT last_run_at FROM learning_curator_state WHERE tenant_id = ?",
            (tenant_id,),
        ).fetchone()
        if row is None or row[0] is None:
            return None
        try:
            return datetime.fromisoformat(row[0].replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return None

    def get_snapshot(
        self, snapshot_ref: str
    ) -> dict[str, Any] | None:
        """Return the snapshot dict for *snapshot_ref*, or None if not found."""
        conn = self._store._get_conn()
        row = conn.execute(
            "SELECT items_json FROM learning_curator_snapshots WHERE snapshot_ref = ?",
            (snapshot_ref,),
        ).fetchone()
        if row is None:
            return None
        items = json.loads(row[0])
        return {"items": items}

    def restore_from_snapshot(
        self, snapshot_ref: str, *, tenant_id: str = "local"
    ) -> int:
        """Restore items archived in a curator pass back to their pre-archive status.

        Reads the snapshot's ``{id, status, kind}`` rows and flips each item
        back to the captured pre-archive status via ``store._set_status_internal``.  Only
        items that are currently ``"archived"`` are touched — an item already
        back in ``"proposed"`` or ``"active"`` is silently skipped (idempotent).

        Parameters
        ----------
        snapshot_ref:
            The ``snapshotRef`` from a prior ``CuratorResult``.
        tenant_id:
            Tenant scope.  Items outside this tenant are ignored.

        Returns
        -------
        int
            Number of items successfully restored.

        Raises
        ------
        KeyError
            If *snapshot_ref* is not found.
        """
        snap = self.get_snapshot(snapshot_ref)
        if snap is None:
            raise KeyError(f"Snapshot not found: {snapshot_ref!r}")

        restored = 0
        for entry in snap["items"]:
            item_id = entry["id"]
            target_status = entry.get("status", "proposed")
            try:
                result = self._store._set_status_internal(
                    item_id,
                    target_status,
                    tenant_id=tenant_id,
                    expected_status="archived",
                )
                if result is not None:
                    restored += 1
                # else: item was not archived (already restored or different status) — skip
            except KeyError:
                logger.warning(
                    "SkillCurator.restore_from_snapshot: item %r not found — skipping",
                    item_id,
                )
        return restored

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run_gated(
        self,
        *,
        now: datetime,
        tenant_id: str,
        config: CuratorConfig,
    ) -> CuratorResult:
        conn = self._store._get_conn()
        stale_cutoff = now - timedelta(days=config.stale_days)
        stale_cutoff_iso = stale_cutoff.isoformat()

        # Identify candidate items for archival:
        # - status = "proposed"
        # - provenance.derived_by = "reflection" (agent-authored)
        # - updated_at < stale_cutoff
        # - NOT pinned
        # Active items are never candidates (conservative: only proposed).
        rows = conn.execute(
            """
            SELECT * FROM learning_items
            WHERE tenant_id = ?
              AND status = 'proposed'
              AND updated_at < ?
            ORDER BY id
            """,
            (tenant_id, stale_cutoff_iso),
        ).fetchall()

        from magi_agent.learning.store import _row_to_item

        candidates = []
        pinned_exempt = []
        active_exempt_count = 0  # always 0 here (we filter status='proposed')

        for row in rows:
            item = _row_to_item(row)
            # Only archive agent-authored items
            if item.provenance.derived_by != "reflection":
                continue
            if item.pinned:
                pinned_exempt.append(item)
                continue
            candidates.append(item)

        would_archive_count = len(candidates)
        pinned_exempt_count = len(pinned_exempt)

        # Shadow mode: compute but don't mutate
        if config.shadow:
            self._persist_last_run_at(conn, tenant_id=tenant_id, now=now)
            return CuratorResult(
                shadow=True,
                wouldArchiveCount=would_archive_count,
                pinnedExemptCount=pinned_exempt_count,
                activeExemptCount=active_exempt_count,
                snapshotRef=None,  # no mutation → no snapshot needed
                evidence=_build_evidence(
                    mode="shadow",
                    archived_count=0,
                    would_archive_count=would_archive_count,
                    pinned_exempt_count=pinned_exempt_count,
                    active_exempt_count=active_exempt_count,
                    snapshot_ref=None,
                    archived_item_ids=[],
                    tenant_id=tenant_id,
                    now=now,
                ),
            )

        # Live pass: snapshot THEN archive
        snapshot_ref: str | None = None
        archived_ids: list[str] = []

        if candidates:
            snapshot_ref = self._write_snapshot(
                conn, candidates=candidates, tenant_id=tenant_id, now=now
            )

            skipped_toctou = 0
            for item in candidates:
                try:
                    result_item = self._store.archive(
                        item.id,
                        actor="skill_curator",
                        tenant_id=tenant_id,
                        expected_status="proposed",
                    )
                    if result_item is None:
                        # Item changed status between the SELECT scan and now
                        # (e.g. auto-activated in a concurrent call) — skip it.
                        skipped_toctou += 1
                        logger.info(
                            "SkillCurator: item %r changed status before archive"
                            " (TOCTOU) — skipping (item left as-is)",
                            item.id,
                        )
                    else:
                        archived_ids.append(item.id)
                except Exception:
                    logger.warning(
                        "SkillCurator: failed to archive item %r — skipping",
                        item.id,
                    )

        self._persist_last_run_at(conn, tenant_id=tenant_id, now=now)

        return CuratorResult(
            archivedCount=len(archived_ids),
            wouldArchiveCount=would_archive_count,
            pinnedExemptCount=pinned_exempt_count,
            activeExemptCount=active_exempt_count,
            snapshotRef=snapshot_ref,
            evidence=_build_evidence(
                mode="live",
                archived_count=len(archived_ids),
                would_archive_count=would_archive_count,
                pinned_exempt_count=pinned_exempt_count,
                active_exempt_count=active_exempt_count,
                snapshot_ref=snapshot_ref,
                archived_item_ids=archived_ids,
                tenant_id=tenant_id,
                now=now,
            ),
        )

    def _write_snapshot(
        self,
        conn: Any,
        *,
        candidates: list[Any],
        tenant_id: str,
        now: datetime,
    ) -> str:
        """Serialize candidates to a snapshot row and return the ref."""
        snapshot_ref = f"curator-snap:{uuid.uuid4().hex}"
        # Serialize to lightweight dicts with id + status only (pre-archive state)
        items_data = [
            {"id": item.id, "status": item.status, "kind": item.kind}
            for item in candidates
        ]
        items_json = json.dumps(items_data)
        conn.execute(
            """
            INSERT INTO learning_curator_snapshots
                (snapshot_ref, tenant_id, created_at, items_json)
            VALUES (?, ?, ?, ?)
            """,
            (snapshot_ref, tenant_id, now.isoformat(), items_json),
        )
        conn.commit()
        return snapshot_ref

    def _persist_last_run_at(
        self, conn: Any, *, tenant_id: str, now: datetime
    ) -> None:
        conn.execute(
            """
            INSERT INTO learning_curator_state (tenant_id, last_run_at)
            VALUES (?, ?)
            ON CONFLICT(tenant_id) DO UPDATE SET last_run_at = excluded.last_run_at
            """,
            (tenant_id, now.isoformat()),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Scheduler job wrapper (Track-A compatible callable)
# ---------------------------------------------------------------------------


class SkillCuratorJob:
    """Wraps ``SkillCurator.run()`` as a callable for the Track-A scheduler.

    The scheduler fires this callable when the job is due; the curator
    applies its own inactivity gate (``should_run_curator``) before acting.

    Intentionally minimal — no loop driver (YAGNI: Track F provides that).
    """

    def __init__(
        self,
        store: SqliteLearningStore,
        *,
        tenant_id: str = "local",
        config: CuratorConfig | None = None,
        last_activity_provider: "object | None" = None,
    ) -> None:
        self._curator = SkillCurator(store=store)
        self._tenant_id = tenant_id
        self._config = config
        self._last_activity_provider = last_activity_provider

    def __call__(self, *, now: datetime | None = None) -> CuratorResult:
        """Invoke the curator, applying the inactivity gate."""
        _now = now if now is not None else datetime.now(UTC)
        cfg = self._config if self._config is not None else CuratorConfig.from_env()

        if not cfg.enabled:
            return self._curator.run(now=_now, tenant_id=self._tenant_id, config=cfg)

        last_run = self._curator.get_last_run_at(tenant_id=self._tenant_id)
        last_activity: datetime | None = None
        if self._last_activity_provider is not None:
            last_activity = getattr(self._last_activity_provider, "last_activity_at", None)

        if not should_run_curator(
            now=_now,
            last_run_at=last_run,
            last_activity_at=last_activity,
            interval_hours=cfg.interval_hours,
            idle_threshold_seconds=cfg.idle_threshold_seconds,
        ):
            logger.debug(
                "SkillCuratorJob: inactivity gate not met — skipping (tenant=%r)",
                self._tenant_id,
            )
            return CuratorResult(
                gateOff=True,
                evidence=_build_evidence(
                    mode="gate_inactivity",
                    archived_count=0,
                    would_archive_count=0,
                    pinned_exempt_count=0,
                    active_exempt_count=0,
                    snapshot_ref=None,
                    archived_item_ids=[],
                    tenant_id=self._tenant_id,
                    now=_now,
                ),
            )

        return self._curator.run(now=_now, tenant_id=self._tenant_id, config=cfg)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "CuratorAuthorityFlags",
    "CuratorConfig",
    "CuratorResult",
    "SkillCurator",
    "SkillCuratorJob",
    "should_run_curator",
]
