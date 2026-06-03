"""Learning injection — PR5 dynamic-injection source (scope → retrieve).

Architecture:
    request scope (taskKind/tags/channel)
            ▼
    build_learning_recall_payload  ──store.retrieve(active-only + scope)──▶
            tuple[LearningRecallEntry]   (rule/example text only)
            ▼
    LearningRecallAdapter.recall  ──▶ RecallResult(MemoryRecord[...])
            ▼
    harness/memory_recall.MemoryRecallHarness  (namespace/redaction/projection)

The adapter is a **local-fake** provider (``openmagi_local_fake_provider =
True``) consumed through the existing ``memory_recall`` DI seam.  It performs
NO live recall, NO network, NO model calls.  When no store is injected (the
default) it returns ``recallAllowed=False`` with zero records, so the harness
yields nothing unless it is gated ON *and* a store is provided.

Real (live) recall binding — promoting beyond the local fake and wiring a real
transcript-backed store — is deferred to PR7.

Scope matching:
    ``store.retrieve`` filters active items by ``scope.task_kind`` exactly.  We
    layer an additional defensive cross-scope guard here so that ``channel`` and
    ``tags`` mismatches never leak across scopes when the store's retrieve is
    later extended.  ``kinds`` is restricted to ``rule``/``example`` (``eval``
    items are never injected as guidance).
"""
from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field

from magi_agent.learning.models import LearningItem, LearningKind, LearningScope
from magi_agent.learning.store import LearningStore
from magi_agent.memory.contracts import MemoryRecord, RecallRequest, RecallResult


#: Kinds eligible for injection as guidance.  ``eval`` items are never injected.
_INJECTABLE_KINDS: tuple[LearningKind, ...] = ("rule", "example")

#: Namespace ref the local-fake learning records are tagged with so the
#: memory_recall namespace admission keeps them in-scope.  Callers pass a
#: matching ``MemoryNamespacePolicy(namespaceRef=...)`` to the harness.
DEFAULT_LEARNING_NAMESPACE_REF: str = "memory-ns:learning.local"


class LearningRecallEntry(BaseModel):
    """A single recalled rule/example rendered to injectable guidance text."""

    model_config = ConfigDict(
        frozen=True,
        populate_by_name=True,
        extra="forbid",
        validate_default=True,
    )

    item_id: str = Field(alias="itemId")
    kind: LearningKind
    text: str
    source_ref: str = Field(alias="sourceRef")


def _render_item_text(item: LearningItem) -> str:
    """Render a learning item's content to a compact guidance string.

    ``rule`` → ``"Rule: when <when> then <then>"``.
    ``example`` → ``"Example: in <situation>, <behavior>"``.
    Falls back to the rationale when expected keys are absent.
    """
    content = dict(item.content)
    if item.kind == "rule":
        when = str(content.get("when", "")).strip()
        then = str(content.get("then", "")).strip()
        if when and then:
            return f"Rule: when {when} then {then}"
    elif item.kind == "example":
        situation = str(content.get("situation", "")).strip()
        behavior = str(content.get("behavior", "")).strip()
        if situation and behavior:
            return f"Example: in {situation}, {behavior}"
    return item.rationale.strip()


def _scope_matches(item: LearningItem, scope: LearningScope) -> bool:
    """Defensive cross-scope guard layered on top of ``store.retrieve``.

    task_kind must match exactly.  When the request scope pins a ``channel``,
    the item's channel (if set) must match.  Item tags, when the request scope
    pins tags, must intersect.  Unpinned request facets do not constrain.
    """
    if item.scope.task_kind != scope.task_kind:
        return False
    if scope.channel is not None and item.scope.channel is not None:
        if item.scope.channel != scope.channel:
            return False
    if scope.tags:
        if not (set(item.scope.tags) & set(scope.tags)):
            return False
    return True


def build_learning_recall_payload(
    store: LearningStore | None,
    *,
    tenant_id: str = "local",
    scope: LearningScope,
    kinds: tuple[LearningKind, ...] = _INJECTABLE_KINDS,
    k: int = 8,
) -> tuple[LearningRecallEntry, ...]:
    """Map a request *scope* to active, scope-matching learning entries.

    Deterministic and side-effect-free.  Returns an empty tuple when *store* is
    ``None`` (the default / disabled case).  Only ``active`` items are returned
    (``store.retrieve`` enforces ``status='active'``); ``proposed``/``archived``
    items never surface.  Cross-scope items are excluded by ``_scope_matches``.
    """
    if store is None:
        return ()
    injectable = tuple(kind for kind in kinds if kind in _INJECTABLE_KINDS)
    if not injectable:
        return ()
    items = store.retrieve(tenant_id=tenant_id, scope=scope, kinds=injectable, k=k)
    entries: list[LearningRecallEntry] = []
    for item in items:
        if not _scope_matches(item, scope):
            continue
        text = _render_item_text(item)
        if not text:
            continue
        entries.append(
            LearningRecallEntry(
                itemId=item.id,
                kind=item.kind,
                text=text,
                sourceRef=f"learning:{item.kind}:{item.id}",
            )
        )
    return tuple(entries)


def _recall_request_scope(request: RecallRequest) -> LearningScope:
    """Derive a ``LearningScope`` from a recall request's scope mapping."""
    raw = request.scope if isinstance(request.scope, Mapping) else {}
    task_kind = str(
        raw.get("taskKind") or raw.get("task_kind") or "general"
    )
    channel_raw = raw.get("channel")
    channel = str(channel_raw) if channel_raw is not None else None
    tags_raw = raw.get("tags")
    if isinstance(tags_raw, (list, tuple)):
        tags = tuple(str(tag) for tag in tags_raw)
    else:
        tags = ()
    return LearningScope(taskKind=task_kind, tags=tags, channel=channel)


class LearningRecallAdapter:
    """Local-fake memory-recall adapter backed by the learning store.

    Conforms to the ``memory_recall`` adapter contract:
      * ``openmagi_local_fake_provider = True`` (required by the harness gate),
      * ``recall(request, *, policy) -> RecallResult``.

    It maps the request scope to ``build_learning_recall_payload`` and emits
    public-safe ``MemoryRecord`` rows.  When no store is injected (default) it
    returns ``recallAllowed=False`` with zero records.

    TODO(PR7): bind the real (live) learning recall source here; until then this
    stays a pure local fake — no network, no model, no ADK runner/memory service.
    """

    #: Marker the memory_recall harness checks before consulting an adapter.
    openmagi_local_fake_provider: bool = True

    def __init__(
        self,
        *,
        store: LearningStore | None = None,
        tenant_id: str = "local",
        namespace_ref: str = DEFAULT_LEARNING_NAMESPACE_REF,
        k: int = 8,
    ) -> None:
        self.store = store
        self.tenant_id = tenant_id
        self.namespace_ref = namespace_ref
        self.k = k
        self.calls = 0

    async def recall(self, request: RecallRequest, *, policy: object) -> RecallResult:
        self.calls += 1
        _ = policy  # policy is enforced by the harness, not the adapter.
        if self.store is None:
            return RecallResult(
                providerId="local-fake-learning",
                records=(),
                recallAllowed=False,
                writeAllowed=False,
                promptProjectionAllowed=False,
                publicProjectionAllowed=False,
                reasonCodes=("local_fake_learning_store_unbound",),
            )
        scope = _recall_request_scope(request)
        entries = build_learning_recall_payload(
            self.store,
            tenant_id=self.tenant_id,
            scope=scope,
            k=min(self.k, request.limit),
        )
        records = tuple(
            MemoryRecord(
                id=entry.item_id,
                scope="bot",
                kind="note",
                body=entry.text,
                sourceRef=entry.source_ref,
                providerId="local-fake-learning",
                confidence="observed",
                visibility="public-safe",
                score=1.0,
                customMetadata={"namespaceRef": self.namespace_ref},
            )
            for entry in entries
        )
        return RecallResult(
            providerId="local-fake-learning",
            records=records,
            recallAllowed=bool(records),
            writeAllowed=False,
            promptProjectionAllowed=False,
            publicProjectionAllowed=True,
            reasonCodes=("local_fake_learning_recall",),
        )


__all__ = [
    "DEFAULT_LEARNING_NAMESPACE_REF",
    "LearningRecallAdapter",
    "LearningRecallEntry",
    "build_learning_recall_payload",
]
