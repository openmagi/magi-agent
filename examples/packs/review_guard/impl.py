"""Example third-party pack impls: a custom validator + a custom callback.

Both receive ONLY their typed contexts (magi_agent/packs/context.py) —
identical capability to first-party (no privilege). Copy this directory as a
starting template, or scaffold fresh shapes with `magi pack new <type> <name>`.
"""
from __future__ import annotations

from magi_agent.hooks.context import HookContext
from magi_agent.hooks.manifest import HookManifest, HookPoint
from magi_agent.hooks.result import HookResult
from magi_agent.packs.context import (
    CallbackProvideContext,
    ValidatorCtx,
    ValidatorVerdict,
)
from magi_agent.tools.manifest import ToolSource


def no_todo_validator(ctx: ValidatorCtx) -> ValidatorVerdict | None:
    """Deterministic check over the produced artifact: no TODO marker left."""
    summary = str(ctx.artifact.get("summary") or "")
    passed = "TODO" not in summary
    ctx.emit(passed=passed, detail=None if passed else "summary still contains TODO")
    return ctx.verdict()


def _audit_handler(context: HookContext) -> HookResult:
    """Pure-observe, non-blocking audit; never alters the turn outcome."""
    return HookResult(action="continue", reason="review-guard observed turn start")


def provide_audit_callback(context: CallbackProvideContext) -> None:
    context.register(
        HookManifest(
            name="review-guard-audit",
            point=HookPoint.BEFORE_TURN_START,
            description="Non-blocking audit marker at turn start (example).",
            source=ToolSource(kind="external", package="examples.review-guard"),
            priority=100,
            blocking=False,
            opt_out=True,
        ),
        _audit_handler,
    )
