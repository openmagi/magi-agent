"""First-party deterministic validator impl (no privilege, typed-ctx only).

Receives ONLY the narrow ``ValidatorCtx`` (D5) — identical capability to any
user-authored validator. Supported iff the runtime observed this validator's
public ref this turn (a tool emitted it into ``artifact["observedRefs"]``).
Emits a verdict via the typed-context API and returns it.
"""
from __future__ import annotations

from magi_agent.packs.context import ValidatorCtx, ValidatorVerdict

_REF = "verifier:sourceOpened@1"


def source_opened_validator(ctx: ValidatorCtx) -> ValidatorVerdict | None:
    observed = ctx.artifact.get("observedRefs") or ()
    passed = ctx.ref in tuple(observed)
    ctx.emit(
        passed=passed,
        detail=None if passed else "source-opened ref not observed this turn",
    )
    return ctx.verdict()
