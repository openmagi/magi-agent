"""The single primitive every governed turn flows through.

All governed turns — top-level serve requests, CLI REPL turns, and child-agent
turns — funnel through ``run_governed_turn``.  The function accepts an optional
pre-built ``runtime`` so the CLI REPL can reuse its long-lived driver across
turns without rebuilding it per call.  The serve path and child paths pass
``runtime=None`` and receive a fresh runtime from ``_build_runtime``.
"""
from __future__ import annotations

import asyncio
from typing import AsyncGenerator

from magi_agent.runtime.turn_context import TurnContext


async def run_governed_turn(
    ctx: TurnContext,
    *,
    runtime: object | None = None,
) -> AsyncGenerator[object, object]:
    """Yield every event produced by one governed turn.

    Parameters
    ----------
    ctx:
        Immutable description of the turn (prompt, session_id, turn_id, and
        any harness-state fields the engine router or verifiers need).
    runtime:
        A pre-built ``HeadlessRuntime``-compatible object exposing ``.engine``
        (a ``MagiEngineDriver``) and ``.gate``.  When provided it is **reused
        as-is** (the CLI REPL keeps one runtime alive across turns).  When
        ``None`` a fresh runtime is built from *ctx* via ``_build_runtime``.
    """
    rt = runtime if runtime is not None else _build_runtime(ctx)
    cancel = asyncio.Event()
    stream = rt.engine.run_turn_stream(  # type: ignore[union-attr]
        None,
        ctx.to_turn_input(),
        cancel=cancel,
        gate=getattr(rt, "gate", None),
    )
    async for item in stream:
        yield item


def _build_runtime(ctx: TurnContext) -> object:
    """Build a minimal headless runtime from *ctx* (``runtime=None`` fallback).

    This is exercised by the serve path (Task 1.3) and future child-runner
    paths that do not pre-build a runtime.  The CLI REPL always passes its
    own runtime, so this path is not exercised by the REPL.

    All parameters have defaults in ``build_headless_runtime``; we forward the
    three that ``TurnContext`` carries.  Callers that need ``cwd``, ``bot_id``,
    ``owner_user_id``, etc. should build the runtime themselves and pass it in.
    """
    from magi_agent.cli.wiring import build_headless_runtime  # local import to avoid circular

    return build_headless_runtime(
        permission_mode="bypassPermissions",
        session_id=ctx.session_id,
        model=ctx.model,
    )
