"""Learning governance — business-logic service over the learning store.

This module is the HUMAN governance surface for the learning layer.  It wraps
:class:`magi_agent.learning.store.SqliteLearningStore` with list/get/approve/
edit/delete operations plus a manual reflection trigger and a deterministic
conflict heuristic.

Hard invariants (mirrors the store + policy contract):

* **Approval is the ONLY human path to ``active``.**  The service never writes
  ``status="active"`` directly; every activation goes through ``store.approve``
  which calls :func:`magi_agent.learning.policy.assert_activation_allowed`.  A
  rule with no recorded eval observation cannot be approved
  (``eval-observation-required``); the policy error is surfaced as a clean 4xx
  by the transport layer, never a 500.
* **Soft-delete only.**  ``delete`` archives; items remain retrievable.
* **Approver identity required for mutation.**  Approve/edit/delete carry an
  ``approver`` string; the transport layer rejects anonymous callers before
  reaching this service.

The conflict heuristic is intentionally simple and deterministic — same
``task_kind`` + same ``tags`` set, an ACTIVE rule with the same ``when`` trigger
but a *different* ``then`` action is treated as a contradiction.  Real semantic
conflict detection is out of scope (PR7+).  Conflicts WARN-AND-BLOCK: approve /
edit return a conflict unless the caller passes ``force=True``.
"""

from __future__ import annotations

from dataclasses import dataclass

from magi_agent.harness.cron_runtime import LearningReflectionCronJob
from magi_agent.learning.models import (
    LearningItem,
    LearningKind,
    LearningScope,
    LearningStatus,
)
from magi_agent.learning.policy import PolicyViolation
from magi_agent.learning.store import Page, SqliteLearningStore, _row_to_item


class LearningApiError(Exception):
    """Base class for governance-service errors carrying an HTTP-ish code."""

    #: HTTP status the transport layer should surface.
    status_code: int = 400
    #: Stable machine-readable error code.
    code: str = "learning_error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class LearningNotFoundError(LearningApiError):
    status_code = 404
    code = "not_found"


class LearningConflictError(LearningApiError):
    """Raised when a contradicting active rule blocks approve/edit.

    Carries the conflicting item id(s) so the transport layer can surface them.
    """

    status_code = 409
    code = "scope_conflict"

    def __init__(
        self,
        message: str,
        *,
        conflicting_ids: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.conflicting_ids = conflicting_ids


class LearningPolicyError(LearningApiError):
    """A policy violation surfaced as a 4xx (never a 500)."""

    status_code = 422
    code = "policy_violation"


class LearningStateError(LearningApiError):
    """An illegal state transition (e.g. approving a non-proposed item)."""

    status_code = 409
    code = "invalid_state"


@dataclass(frozen=True)
class ConflictInfo:
    """Deterministic conflict heuristic result for a learning item."""

    has_conflict: bool
    conflicting_ids: tuple[str, ...]
    reason: str | None = None


@dataclass(frozen=True)
class ReflectionRunSummary:
    """Compact summary of a manual reflection trigger."""

    status: str
    candidates_produced: int
    items_proposed: int
    items_activated: int
    watermark: str | None


class LearningGovernanceService:
    """Business logic over a :class:`SqliteLearningStore`.

    The service holds a reference to the store and (optionally) a reflection
    cron job; it performs NO mounting / routing itself — that is the transport
    layer's job.
    """

    def __init__(
        self,
        store: SqliteLearningStore,
        *,
        tenant_id: str = "local",
        reflection_job: LearningReflectionCronJob | None = None,
    ) -> None:
        self._store = store
        self._tenant_id = tenant_id
        self._reflection_job = reflection_job

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def list_items(
        self,
        *,
        kind: LearningKind | None = None,
        status: LearningStatus | None = None,
        scope: LearningScope | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> Page:
        return self._store.list(
            tenant_id=self._tenant_id,
            kind=kind,
            status=status,
            scope=scope,
            limit=limit,
            cursor=cursor,
        )

    def get_item(self, item_id: str) -> LearningItem:
        item = self._store.get(item_id)
        if item is None:
            raise LearningNotFoundError(f"learning item not found: {item_id!r}")
        return item

    def detail(self, item_id: str) -> tuple[LearningItem, str | None, ConflictInfo]:
        """Return the item, its latest eval-observation ref, and conflict info.

        The eval-observation ref is looked up from the observations table when
        the item row does not already carry one (proposed rules record an
        observation but keep the row column empty until activation).
        """
        item = self.get_item(item_id)
        eval_ref = self._resolve_eval_observation_ref(item)
        conflict = self._detect_conflict(item)
        return item, eval_ref, conflict

    # ------------------------------------------------------------------
    # Mutations (all require an approver / actor)
    # ------------------------------------------------------------------

    def approve(
        self,
        item_id: str,
        *,
        approver: str,
        force: bool = False,
    ) -> LearningItem:
        """Human-approve a proposed *rule* (or other proposed item) → active.

        Enforces the eval-observation + approval-ref invariants through
        ``store.approve`` → ``policy.assert_activation_allowed``.  A contradicting
        active rule blocks approval unless *force* is set.
        """
        item = self.get_item(item_id)
        if item.status != "proposed":
            raise LearningStateError(
                f"cannot approve item {item_id!r}: status={item.status!r} "
                "(only proposed items can be approved)"
            )

        if not force:
            conflict = self._detect_conflict(item)
            if conflict.has_conflict:
                raise LearningConflictError(
                    "approval blocked by a contradicting active rule in the same "
                    "scope; pass force=true to override",
                    conflicting_ids=conflict.conflicting_ids,
                )

        eval_ref = self._resolve_eval_observation_ref(item)
        try:
            return self._store.approve(
                item_id,
                approver=approver,
                eval_observation_ref=eval_ref,
            )
        except PolicyViolation as exc:
            raise LearningPolicyError(str(exc)) from exc
        except ValueError as exc:
            # Defensive: store raises ValueError for state/argument issues.
            raise LearningStateError(str(exc)) from exc

    def edit(
        self,
        item_id: str,
        *,
        patch: dict[str, object],
        editor: str,
        force: bool = False,
    ) -> LearningItem:
        """Edit an item → new proposed version (supersedes chain).

        Runs the conflict heuristic against the POST-PATCH item before writing
        so an edit that introduces a contradiction is blocked (unless *force*).
        """
        item = self.get_item(item_id)

        if not force:
            projected = self._apply_patch_for_conflict_check(item, patch)
            conflict = self._detect_conflict(projected, exclude_id=item_id)
            if conflict.has_conflict:
                raise LearningConflictError(
                    "edit blocked by a contradicting active rule in the same "
                    "scope; pass force=true to override",
                    conflicting_ids=conflict.conflicting_ids,
                )

        try:
            return self._store.edit(item_id, patch=patch, editor=editor)
        except ValueError as exc:
            raise LearningApiError(str(exc)) from exc

    def delete(self, item_id: str, *, actor: str) -> LearningItem:
        """Soft-delete (archive) an item.  Never hard-deletes."""
        try:
            return self._store.archive(item_id, actor=actor)
        except KeyError as exc:
            raise LearningNotFoundError(
                f"learning item not found: {item_id!r}"
            ) from exc

    # ------------------------------------------------------------------
    # Manual reflection trigger
    # ------------------------------------------------------------------

    async def run_reflection(self) -> ReflectionRunSummary:
        """Trigger one reflection pass via the cron job (or a default job)."""
        job = self._reflection_job or LearningReflectionCronJob(store=self._store)
        result = await job.trigger_now()
        counters = result.counters or {}
        return ReflectionRunSummary(
            status=result.status,
            candidates_produced=int(counters.get("candidates_produced", 0)),
            items_proposed=int(counters.get("items_proposed", 0)),
            items_activated=int(counters.get("items_activated", 0)),
            watermark=result.watermark,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_eval_observation_ref(self, item: LearningItem) -> str | None:
        """Return the eval-observation ref for *item*, or ``None``.

        Prefers the ref already stamped on the item row (set at activation);
        otherwise looks up the most recent recorded observation for the item
        id from the observations table (proposed rules carry one there).
        """
        if item.eval_observation_ref:
            return item.eval_observation_ref
        conn = self._store._get_conn()  # noqa: SLF001 — sibling read of store conn
        row = conn.execute(
            """
            SELECT ref FROM learning_eval_observations
            WHERE item_id = ?
            ORDER BY created_at DESC, rowid DESC
            LIMIT 1
            """,
            (item.id,),
        ).fetchone()
        if row is None:
            return None
        return row["ref"]

    def _active_rules_in_scope(
        self,
        scope: LearningScope,
        *,
        exclude_id: str | None = None,
    ) -> tuple[LearningItem, ...]:
        conn = self._store._get_conn()  # noqa: SLF001 — sibling read of store conn
        rows = conn.execute(
            """
            SELECT * FROM learning_items
            WHERE tenant_id = ? AND status = 'active' AND kind = 'rule'
            """,
            (self._tenant_id,),
        ).fetchall()
        result: list[LearningItem] = []
        for row in rows:
            candidate = _row_to_item(row)
            if exclude_id is not None and candidate.id == exclude_id:
                continue
            if not self._same_scope(candidate.scope, scope):
                continue
            result.append(candidate)
        return tuple(result)

    @staticmethod
    def _same_scope(a: LearningScope, b: LearningScope) -> bool:
        return a.task_kind == b.task_kind and set(a.tags) == set(b.tags)

    def _detect_conflict(
        self,
        item: LearningItem,
        *,
        exclude_id: str | None = None,
    ) -> ConflictInfo:
        """Deterministic contradiction heuristic for rule items.

        Heuristic: another ACTIVE rule in the SAME scope (task_kind + tags) with
        the SAME ``when`` trigger but a DIFFERENT ``then`` action.  Non-rule
        items never conflict under this heuristic.
        """
        if item.kind != "rule":
            return ConflictInfo(has_conflict=False, conflicting_ids=())

        content = dict(item.content)
        when = content.get("when")
        then = content.get("then")
        conflicting: list[str] = []
        for other in self._active_rules_in_scope(item.scope, exclude_id=exclude_id):
            if other.id == item.id:
                continue
            other_content = dict(other.content)
            if other_content.get("when") == when and other_content.get("then") != then:
                conflicting.append(other.id)

        if conflicting:
            return ConflictInfo(
                has_conflict=True,
                conflicting_ids=tuple(conflicting),
                reason="same scope + same trigger, contradicting action",
            )
        return ConflictInfo(has_conflict=False, conflicting_ids=())

    @staticmethod
    def _apply_patch_for_conflict_check(
        item: LearningItem,
        patch: dict[str, object],
    ) -> LearningItem:
        """Project *patch* onto *item* (camelCase aliases) for conflict checking.

        Falls back to the unpatched item if the patch produces an invalid model
        (the store's edit() will raise the real error later).
        """
        try:
            data = item.model_dump(by_alias=True) | patch
            return LearningItem.model_validate(data)
        except Exception:  # noqa: BLE001 — conflict check is best-effort
            return item


__all__ = [
    "ConflictInfo",
    "LearningApiError",
    "LearningConflictError",
    "LearningGovernanceService",
    "LearningNotFoundError",
    "LearningPolicyError",
    "LearningStateError",
    "ReflectionRunSummary",
]
