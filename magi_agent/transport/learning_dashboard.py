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
from collections.abc import Mapping
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
from magi_agent.learning.models import LearningItem, LearningScope
from magi_agent.learning.store import SqliteLearningStore

if TYPE_CHECKING:
    from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime


_DASHBOARD_ENV_VAR = "MAGI_LEARNING_DASHBOARD_ENABLED"
_TRUE_STRINGS = frozenset({"1", "true", "yes", "on"})


def learning_dashboard_enabled() -> bool:
    """True when the learning governance dashboard router should be mounted."""
    return os.environ.get(_DASHBOARD_ENV_VAR, "").lower() in _TRUE_STRINGS


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
    service: LearningGovernanceService,
    *,
    gateway_token: str,
) -> APIRouter:
    # I1: refuse to build an unauthenticated dashboard.  An empty/None token
    # would let a blank ``x-gateway-token: `` header authenticate via
    # ``compare_digest("", "")`` — a silent auth bypass.  Fail loud at build.
    if not gateway_token:
        raise ValueError(
            "build_learning_dashboard_router requires a non-empty gateway_token; "
            "refusing to mount an unauthenticated learning dashboard"
        )
    router = APIRouter(prefix="/v1/learning", tags=["learning"])

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
        page = service.list_items(
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
            item, eval_ref, conflict = service.detail(item_id)
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
        approver = _approver(request)
        if approver is None:
            return JSONResponse(
                status_code=401,
                content={"error": "approver_required"},
            )
        force = bool(body.force) if body is not None else False
        try:
            item = service.approve(item_id, approver=approver, force=force)
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
        editor = _approver(request)
        if editor is None:
            return JSONResponse(
                status_code=401,
                content={"error": "approver_required"},
            )
        try:
            item = service.edit(
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
        actor = _approver(request)
        if actor is None:
            return JSONResponse(
                status_code=401,
                content={"error": "approver_required"},
            )
        try:
            item = service.delete(item_id, actor=actor)
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
        if _approver(request) is None:
            return JSONResponse(
                status_code=401,
                content={"error": "approver_required"},
            )
        summary = await service.run_reflection()
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
    store = SqliteLearningStore()
    service = LearningGovernanceService(store)
    router = build_learning_dashboard_router(
        service, gateway_token=runtime.config.gateway_token
    )
    app.include_router(router)


__all__ = [
    "build_learning_dashboard_router",
    "learning_dashboard_enabled",
    "register_learning_dashboard_routes",
]
