"""Learning governance dashboard API — FastAPI router.

Default-OFF HTTP surface for the human governance of the learning layer.  The
router is mounted onto the app ONLY when ``MAGI_LEARNING_DASHBOARD_ENABLED`` is
truthy; otherwise the app surface is byte-identical to before.

Authz (single-tenant, minimal — full multi-tenant authz is PR8):

* Read endpoints require the gateway token (``x-gateway-token``), matching the
  sibling product-admin router.
* Mutating endpoints (approve / edit / delete) additionally require an approver
  identity via the ``x-approver`` header; anonymous mutation is rejected with
  ``401`` BEFORE the service is invoked.

Policy errors raised by the service (eval-observation-required,
no-direct-mutation) surface as clean ``4xx`` responses, never ``500``.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from secrets import compare_digest
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from magi_agent.learning.api import (
    ConflictInfo,
    LearningApiError,
    LearningGovernanceService,
)
from magi_agent.learning.config import resolve_learning_config
from magi_agent.learning.models import LearningItem, LearningScope
from magi_agent.learning.store import SqliteLearningStore

#: Header carrying the request's tenant; absent → the OSS single-tenant default.
_TENANT_HEADER = "x-tenant"
_DEFAULT_TENANT = "local"

#: Role recorded in the approval audit for an authorized approver.  Kept generic
#: (OSS) — the hosted multi-tenant deployment can map richer roles via its own
#: ``is_approver`` resolver; here a passing resolver attests this single role.
_APPROVER_ROLE = "approver"

#: Approver-role resolver seam.  ``is_approver(tenant_id, approver) -> bool``
#: answers "does *approver* hold an approver role for *tenant_id*?".  The default
#: single-tenant implementation treats ANY non-empty approver identity as
#: authorized (byte-identical to PR6's header-presence check); a hosted
#: multi-tenant deployment injects a real role-store-backed resolver.
ApproverRoleResolver = Callable[[str, str], bool]


def _default_is_approver(_tenant_id: str, approver: str) -> bool:
    return bool(approver and approver.strip())

if TYPE_CHECKING:
    from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime


_DASHBOARD_ENV_VAR = "MAGI_LEARNING_DASHBOARD_ENABLED"
_TRUE_STRINGS = frozenset({"1", "true", "yes", "on"})


def learning_dashboard_enabled() -> bool:
    """True when the learning governance dashboard router should be mounted.

    PR9a layered opt-out: the dashboard is now mounted **by default** (safe
    tier).  It is OFF only when the master switch ``MAGI_LEARNING_ENABLED`` is
    explicitly falsy or ``MAGI_LEARNING_DASHBOARD_ENABLED`` is explicitly falsy.
    Resolution flows through :func:`resolve_learning_config`; master-off makes
    the surface byte-identical to the PR1–PR8 not-mounted state.
    """
    return resolve_learning_config().dashboard_effective


_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
)


# ---------------------------------------------------------------------------
# Response models (frozen pydantic v2, camelCase aliases)
# ---------------------------------------------------------------------------


class ConflictInfoModel(BaseModel):
    model_config = _MODEL_CONFIG

    has_conflict: bool = Field(alias="hasConflict")
    conflicting_ids: tuple[str, ...] = Field(default=(), alias="conflictingIds")
    reason: str | None = None

    @classmethod
    def from_info(cls, info: ConflictInfo) -> "ConflictInfoModel":
        return cls(
            hasConflict=info.has_conflict,
            conflictingIds=info.conflicting_ids,
            reason=info.reason,
        )


class LearningItemSummary(BaseModel):
    model_config = _MODEL_CONFIG

    id: str
    kind: str
    status: str
    scope: Mapping[str, object]
    rationale: str
    version: int
    supersedes: str | None = None

    @classmethod
    def from_item(cls, item: LearningItem) -> "LearningItemSummary":
        return cls(
            id=item.id,
            kind=item.kind,
            status=item.status,
            scope=item.scope.model_dump(by_alias=True),
            rationale=item.rationale,
            version=item.version,
            supersedes=item.supersedes,
        )


class LearningItemDetail(BaseModel):
    model_config = _MODEL_CONFIG

    id: str
    kind: str
    status: str
    scope: Mapping[str, object]
    content: Mapping[str, object]
    rationale: str
    provenance: Mapping[str, object]
    version: int
    supersedes: str | None = None
    eval_observation_ref: str | None = Field(default=None, alias="evalObservationRef")
    approval_ref: str | None = Field(default=None, alias="approvalRef")
    conflict: ConflictInfoModel

    @classmethod
    def from_item(
        cls,
        item: LearningItem,
        *,
        eval_observation_ref: str | None,
        conflict: ConflictInfo,
    ) -> "LearningItemDetail":
        return cls(
            id=item.id,
            kind=item.kind,
            status=item.status,
            scope=item.scope.model_dump(by_alias=True),
            content=dict(item.content),
            rationale=item.rationale,
            provenance=item.provenance.model_dump(by_alias=True),
            version=item.version,
            supersedes=item.supersedes,
            evalObservationRef=eval_observation_ref,
            approvalRef=item.approval_ref,
            conflict=ConflictInfoModel.from_info(conflict),
        )


class ListLearningsResponse(BaseModel):
    model_config = _MODEL_CONFIG

    items: tuple[LearningItemSummary, ...]
    next_cursor: str | None = Field(default=None, alias="nextCursor")


class ReflectionRunResponse(BaseModel):
    model_config = _MODEL_CONFIG

    status: str
    candidates_produced: int = Field(alias="candidatesProduced")
    items_proposed: int = Field(alias="itemsProposed")
    items_activated: int = Field(alias="itemsActivated")
    watermark: str | None = None


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class ApproveLearningRequest(BaseModel):
    model_config = _MODEL_CONFIG

    force: bool = False


class EditLearningRequest(BaseModel):
    model_config = _MODEL_CONFIG

    patch: Mapping[str, Any]
    force: bool = False


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def build_learning_dashboard_router(
    service: LearningGovernanceService | None = None,
    *,
    gateway_token: str,
    store: SqliteLearningStore | None = None,
    is_approver: ApproverRoleResolver | None = None,
    reflection_job: Any | None = None,
) -> APIRouter:
    """Build the learning governance router.

    Two construction modes, ONE code path:

    * **Legacy single-tenant** — pass a pre-built ``service`` (OSS / PR6).  The
      service's fixed ``tenant_id`` is used; the ``x-tenant`` header is ignored.
    * **Multi-tenant (PR8)** — pass a ``store`` (and optionally an
      ``is_approver`` role resolver).  A per-request service is built scoped to
      the request's ``x-tenant`` header (default ``"local"``), so the SAME store
      serves many tenants with query-level isolation.

    In BOTH modes mutations require (a) the gateway token, (b) a present approver
    identity (else 401), and (c) that the approver holds the approver ROLE for
    the tenant (else 403).  The default resolver authorizes any present approver
    — byte-identical to PR6 — so OSS single-tenant behavior is preserved.
    """
    # I1: refuse to build an unauthenticated dashboard.  An empty/None token
    # would let a blank ``x-gateway-token: `` header authenticate via
    # ``compare_digest("", "")`` — a silent auth bypass.  Fail loud at build.
    if not gateway_token:
        raise ValueError(
            "build_learning_dashboard_router requires a non-empty gateway_token; "
            "refusing to mount an unauthenticated learning dashboard"
        )
    if service is None and store is None:
        raise ValueError(
            "build_learning_dashboard_router requires either a service "
            "(single-tenant) or a store (multi-tenant)"
        )

    resolver: ApproverRoleResolver = is_approver or _default_is_approver
    # Record the explicit approver ROLE in the audit ONLY when a real role
    # resolver was injected (hosted multi-tenant).  In the default single-tenant
    # mode the bare identity is recorded — byte-identical to PR6's audit row.
    approval_role: str | None = _APPROVER_ROLE if is_approver is not None else None
    # I-2: a store/multi-tenant mount WITHOUT an injected approver-role resolver
    # has no way to authorize an approver per-tenant — the default resolver would
    # authorize ANY approver for ANY tenant, so honoring a caller-chosen
    # ``x-tenant`` would be name-only isolation.  In that case PIN the tenant to
    # the single-tenant default ("local") and refuse to honor a non-local
    # ``x-tenant``.  A hosted deployment that injects a real resolver keeps full
    # multi-tenant behavior; single-tenant OSS is byte-identical.
    _multi_tenant_authz = is_approver is not None
    router = APIRouter(prefix="/v1/learning", tags=["learning"])

    def _tenant(request: Request) -> str:
        # Legacy single-tenant mode pins the tenant to the injected service and
        # ignores the header.
        if service is not None:
            return service._tenant_id  # noqa: SLF001 — sibling read
        # Store mode WITHOUT a real role resolver: pin to "local" (ignore the
        # caller-chosen header) so a request cannot reach another tenant's data.
        if not _multi_tenant_authz:
            return _DEFAULT_TENANT
        raw = request.headers.get(_TENANT_HEADER)
        if raw is None or not raw.strip():
            return _DEFAULT_TENANT
        return raw.strip()

    def _service_for(request: Request) -> LearningGovernanceService:
        if service is not None:
            return service
        assert store is not None  # narrowed by the build-time guard above
        return LearningGovernanceService(
            store, tenant_id=_tenant(request), reflection_job=reflection_job
        )

    def _authorized(request: Request) -> bool:
        token = request.headers.get("x-gateway-token")
        return token is not None and compare_digest(token, gateway_token)

    def _unauthorized() -> JSONResponse:
        return JSONResponse(status_code=401, content={"error": "unauthorized"})

    def _approver(request: Request) -> str | None:
        approver = request.headers.get("x-approver")
        if approver is None or not approver.strip():
            return None
        return approver.strip()

    def _authorized_approver(request: Request) -> tuple[str | None, JSONResponse | None]:
        """Resolve the approver + verify the approver ROLE for the tenant.

        Returns ``(approver, None)`` when authorized, else ``(None, response)``
        with a 401 (anonymous) or 403 (present but not an approver-role
        identity) — anonymous and unauthorized are deliberately distinct.
        """
        approver = _approver(request)
        if approver is None:
            return None, JSONResponse(
                status_code=401, content={"error": "approver_required"}
            )
        # A hosted role resolver may reach an external role store; if that lookup
        # raises, surface a clean 503 (role check unavailable) rather than
        # letting the exception bubble into an opaque 500.
        try:
            is_approver_role = resolver(_tenant(request), approver)
        except Exception:
            return None, JSONResponse(
                status_code=503,
                content={"error": "role_check_unavailable"},
            )
        if not is_approver_role:
            return None, JSONResponse(
                status_code=403,
                content={"error": "approver_role_required"},
            )
        return approver, None

    def _error_response(exc: LearningApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.code, "message": str(exc)},
        )

    @router.get("/learnings")
    async def list_learnings(
        request: Request,
        kind: str | None = None,
        status: str | None = None,
        taskKind: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> JSONResponse:
        if not _authorized(request):
            return _unauthorized()
        scope = LearningScope(taskKind=taskKind) if taskKind is not None else None
        # I3: clamp caller-supplied limit to a sane window so a hostile/buggy
        # client cannot request an unbounded page.
        limit = min(max(limit, 1), 200)
        page = _service_for(request).list_items(
            kind=kind,  # type: ignore[arg-type]
            status=status,  # type: ignore[arg-type]
            scope=scope,
            limit=limit,
            cursor=cursor,
        )
        payload = ListLearningsResponse(
            items=tuple(LearningItemSummary.from_item(i) for i in page.items),
            nextCursor=page.next_cursor,
        )
        return JSONResponse(content=payload.model_dump(by_alias=True))

    @router.get("/learnings/{item_id}")
    async def get_learning(item_id: str, request: Request) -> JSONResponse:
        if not _authorized(request):
            return _unauthorized()
        try:
            item, eval_ref, conflict = _service_for(request).detail(item_id)
        except LearningApiError as exc:
            return _error_response(exc)
        detail = LearningItemDetail.from_item(
            item, eval_observation_ref=eval_ref, conflict=conflict
        )
        return JSONResponse(content=detail.model_dump(by_alias=True))

    @router.post("/learnings/{item_id}/approve")
    async def approve_learning(
        item_id: str,
        request: Request,
        body: ApproveLearningRequest | None = None,
    ) -> JSONResponse:
        if not _authorized(request):
            return _unauthorized()
        approver, denied = _authorized_approver(request)
        if denied is not None:
            return denied
        assert approver is not None
        force = bool(body.force) if body is not None else False
        try:
            item = _service_for(request).approve(
                item_id, approver=approver, role=approval_role, force=force
            )
        except LearningApiError as exc:
            return _error_response(exc)
        return JSONResponse(content=LearningItemSummary.from_item(item).model_dump(by_alias=True))

    @router.patch("/learnings/{item_id}")
    async def edit_learning(
        item_id: str,
        request: Request,
        body: EditLearningRequest,
    ) -> JSONResponse:
        if not _authorized(request):
            return _unauthorized()
        editor, denied = _authorized_approver(request)
        if denied is not None:
            return denied
        assert editor is not None
        try:
            item = _service_for(request).edit(
                item_id,
                patch=dict(body.patch),
                editor=editor,
                force=bool(body.force),
            )
        except LearningApiError as exc:
            return _error_response(exc)
        return JSONResponse(content=LearningItemSummary.from_item(item).model_dump(by_alias=True))

    @router.delete("/learnings/{item_id}")
    async def delete_learning(item_id: str, request: Request) -> JSONResponse:
        if not _authorized(request):
            return _unauthorized()
        actor, denied = _authorized_approver(request)
        if denied is not None:
            return denied
        assert actor is not None
        try:
            item = _service_for(request).delete(item_id, actor=actor)
        except LearningApiError as exc:
            return _error_response(exc)
        return JSONResponse(content=LearningItemSummary.from_item(item).model_dump(by_alias=True))

    @router.post("/reflection/run")
    async def run_reflection(request: Request) -> JSONResponse:
        # I2: an approver is required even though this is not a pure read —
        # triggering reflection writes proposed candidates into the store, so it
        # mutates the proposed queue and must carry an accountable actor.
        if not _authorized(request):
            return _unauthorized()
        _approver_id, denied = _authorized_approver(request)
        if denied is not None:
            return denied
        summary = await _service_for(request).run_reflection()
        payload = ReflectionRunResponse(
            status=summary.status,
            candidatesProduced=summary.candidates_produced,
            itemsProposed=summary.items_proposed,
            itemsActivated=summary.items_activated,
            watermark=summary.watermark,
        )
        return JSONResponse(content=payload.model_dump(by_alias=True))

    return router


def register_learning_dashboard_routes(app: FastAPI, runtime: OpenMagiRuntime) -> None:
    """Mount the learning governance router — ONLY when the env gate is ON.

    When ``MAGI_LEARNING_DASHBOARD_ENABLED`` is falsy this is a no-op, leaving
    the app surface byte-identical to the default build.
    """
    if not learning_dashboard_enabled():
        return

    # The store resolves its db path relative to the process cwd when no
    # workspace root is supplied — matching the reflection executor / cron job,
    # which construct ``SqliteLearningStore`` the same way.
    #
    # Built in store mode (PR8).  No ``is_approver`` role resolver is injected
    # here, so per the I-2 hardening the tenant is PINNED to ``"local"`` (a
    # caller-chosen ``x-tenant`` is ignored) — the default-mount therefore gives
    # real single-tenant isolation, never name-only isolation.  A hosted
    # deployment that injects a real role-store-backed resolver gets full
    # multi-tenant behavior with per-tenant ``x-tenant`` scoping.
    store = SqliteLearningStore()
    router = build_learning_dashboard_router(
        store=store, gateway_token=runtime.config.gateway_token
    )
    app.include_router(router)


__all__ = [
    "build_learning_dashboard_router",
    "learning_dashboard_enabled",
    "register_learning_dashboard_routes",
]
