"""Unified cross-verification recipe — fan out one prompt across N models, vote.

This recipe composes two EXISTING primitives into the "cross-verify across
models" capability:

1. :class:`~magi_agent.runtime.child_runner_boundary.LocalChildRunnerBoundary`
   — the child-runner boundary that runs each child turn behind a sanitised
   envelope.  One :class:`ChildTaskRequest` is built per model route, carrying
   the SAME ``prompt`` but that route's ``provider``/``model``.  The fan-out is
   concurrent (``asyncio.gather``) under a concurrency clamp, mirroring
   :class:`~magi_agent.recipes.research_child_runner.ResearchChildRunnerRecipe`.

2. :func:`~magi_agent.recipes.best_of_n.run_best_of_n` — the deterministic
   consensus voter.  After fan-out we hold one sanitised candidate summary per
   model.  ``run_best_of_n`` is fed a ``runner_fn`` that returns the i-th
   already-collected candidate (indexed by ``seed - base_seed``), so best_of_n
   performs a plurality vote over the cross-model answers WITHOUT re-running any
   model.  No re-sampling: the consensus is computed over the pre-computed
   per-model candidates.

Design constraints (mirrors the rest of the recipe layer):

* Default-OFF — gated by ``CrossVerifyConfig.enabled`` (default ``False``) and
  the ``MAGI_CROSS_VERIFY_ENABLED`` env var.  When disabled the recipe returns
  a no-op result and NEVER fans out (the ``child_runner_factory`` is not
  called).
* Never raises — a failed fan-out / failed vote degrades to a result with an
  empty consensus winner; exceptions are caught and counted, not propagated.
* Sanitisation — candidate summaries come from the boundary's already-sanitised
  envelope (``ChildRunnerResult.public_projection``); the recipe never reads or
  re-introduces raw transcript text.
* Injected child-runner seam — ``child_runner_factory(route)`` returns the
  child-runner object the boundary runs.  At runtime this is a REAL
  model-backed runner (``openmagi_live_provider=True``); in tests it is a fake
  stub (``openmagi_local_fake_provider=True``).  The recipe does NOT import any
  real runner, so it is network-free testable.  The boundary's OWN marker
  (live or fake) selects the execution path; a runner with neither marker is
  blocked by the boundary.
* Safety caps — spawn-depth (1) + max models (≤8) + concurrency clamp mirror
  the research child runner; each child is text-only via the injected runner
  (the recipe grants no tools).
"""
from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from magi_agent.recipes.best_of_n import (
    BestOfNConfig,
    BestOfNResult,
    ConsensusMode,
    run_best_of_n,
)
from magi_agent.runtime import (
    ChildRunnerConfig,
    ChildRunnerResult,
    ChildTaskRequest,
    LocalChildRunnerBoundary,
)
from magi_agent.tools.manifest import Budget


#: Environment-variable feature flag — disabled until explicitly set.
_CROSS_VERIFY_ENABLED_ENV = "MAGI_CROSS_VERIFY_ENABLED"

#: Hard cap on the number of model routes a single cross-verify run may fan
#: across.  Bounds runaway fan-out independently of the concurrency clamp.
_MAX_MODELS = 8

#: Spawn-depth ceiling for cross-verify children (no nested fan-out).
_MAX_SPAWN_DEPTH = 1

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)


def is_cross_verify_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Return whether the cross-verify env gate is set.

    Mirrors ``best_of_n``'s ``MAGI_BEST_OF_N_ENABLED`` convention.  When *env*
    is ``None`` the process environment is consulted.
    """
    source = env if env is not None else os.environ
    return bool(source.get(_CROSS_VERIFY_ENABLED_ENV))


ModelRoute = tuple[str, str]
"""A ``(provider, model)`` route the prompt is fanned across."""


class CrossVerifyCandidate(BaseModel):
    """One model's sanitised answer to the shared prompt."""

    model_config = _MODEL_CONFIG

    provider: str
    model: str
    #: Boundary child status: "ok" | "disabled" | "blocked" | "error".
    status: str
    #: Sanitised summary from the boundary envelope (empty when failed/blocked).
    summary: str = ""
    #: True when this candidate contributed to the consensus vote.
    counted: bool


class CrossVerifyResult(BaseModel):
    """Consensus outcome of fanning one prompt across N models."""

    model_config = ConfigDict(
        frozen=True,
        arbitrary_types_allowed=True,
        populate_by_name=True,
        extra="forbid",
        validate_default=True,
        hide_input_in_errors=True,
    )

    enabled: bool
    #: The consensus winner summary ("" when disabled / all children failed).
    consensus: str
    #: Per-model candidates (sanitised summary + route + status), in route order.
    candidates: tuple[CrossVerifyCandidate, ...] = ()
    #: Number of model routes fanned out (after clamp/dedup).
    models_attempted: int = 0
    #: Number of children that produced a non-empty, countable summary.
    models_counted: int = 0
    #: Number of candidates that ACTUALLY voted (best_of_n n_attempted after
    #: budget cap); may be less than ``models_counted`` when a Budget truncates
    #: the vote width.  Confidence is interpretable as
    #: ``agreement_count / models_voted``.
    models_voted: int = 0
    #: Number of candidates that agreed with the consensus winner.
    agreement_count: int = 0
    #: Fraction of VOTED candidates agreeing with the winner (0.0 when none).
    confidence: float = 0.0
    #: The consensus mode that produced this result.
    consensus_mode: ConsensusMode = ConsensusMode.PLURALITY
    #: Reason codes for a disabled/degraded outcome (empty on success).
    reason_codes: tuple[str, ...] = ()


class CrossVerifyConfig(BaseModel):
    """Configuration for a cross-verify run (default-OFF)."""

    model_config = _MODEL_CONFIG

    enabled: bool = Field(
        default=False,
        description=(
            "Must be explicitly True to fan out; default-OFF. Can also be "
            "activated via the MAGI_CROSS_VERIFY_ENABLED env var."
        ),
    )
    models: tuple[ModelRoute, ...] = Field(
        default=(),
        description="(provider, model) routes to fan the prompt across.",
    )
    max_concurrency: int = Field(
        default=_MAX_MODELS,
        ge=1,
        le=_MAX_MODELS,
        alias="maxConcurrency",
        description="Upper bound on concurrent child fan-out.",
    )
    budget_ms: int = Field(
        default=5000,
        ge=0,
        alias="budgetMs",
        description="Per-child wall-clock budget forwarded to each child.",
    )
    budget_tokens: int = Field(
        default=768,
        ge=0,
        alias="budgetTokens",
        description="Per-child token budget forwarded to each child.",
    )
    consensus_mode: ConsensusMode = Field(
        default=ConsensusMode.PLURALITY,
        alias="consensusMode",
        description="Consensus strategy passed through to BestOfNConfig.",
    )

    @field_validator("models", mode="before")
    @classmethod
    def _coerce_models(cls, value: object) -> object:
        if value is None:
            return ()
        if isinstance(value, Sequence) and not isinstance(value, str | bytes):
            return tuple(value)
        return value

    @field_validator("models")
    @classmethod
    def _clamp_and_dedup_models(cls, value: tuple[ModelRoute, ...]) -> tuple[ModelRoute, ...]:
        return _resolve_routes(value)


async def run_cross_verify(
    *,
    prompt: str,
    models: Sequence[ModelRoute] | None = None,
    child_runner_factory: Callable[[ModelRoute], object],
    config: CrossVerifyConfig | None = None,
    budget: Budget | None = None,
    env: Mapping[str, str] | None = None,
    parent_execution_id: str = "cross-verify-parent",
    turn_id: str = "cross-verify-turn",
) -> CrossVerifyResult:
    """Fan *prompt* across *models* and return a cross-model consensus.

    Parameters
    ----------
    prompt:
        The SAME prompt sent to every model route.
    models:
        ``(provider, model)`` routes; falls back to ``config.models`` when
        ``None``.  Clamped to ``≤ _MAX_MODELS`` and de-duplicated.
    child_runner_factory:
        ``route -> child_runner`` factory.  The returned object is what the
        boundary runs for that route (a real runner at runtime; a fake in
        tests).  Called once per (clamped/deduped) route ONLY when the gate is
        on.  The recipe never imports a real runner — this is the network-free
        seam.
    config:
        ``CrossVerifyConfig``; defaults to a default-OFF config when ``None``.
    budget:
        Optional ``Budget`` forwarded to ``run_best_of_n`` (caps the vote width
        to ``budget.max_calls_per_turn`` if smaller than the model count).
    env:
        Optional environment mapping for the gate (defaults to ``os.environ``).
    """
    cfg = config or CrossVerifyConfig()
    routes = _resolve_routes(models if models is not None else cfg.models)

    # --- Default-OFF gate: never fan out when disabled --------------------
    gate_on = cfg.enabled or is_cross_verify_enabled(env)
    if not gate_on:
        return CrossVerifyResult(
            enabled=False,
            consensus="",
            consensus_mode=cfg.consensus_mode,
            reason_codes=("cross_verify_disabled",),
        )
    if not routes:
        return CrossVerifyResult(
            enabled=True,
            consensus="",
            consensus_mode=cfg.consensus_mode,
            reason_codes=("cross_verify_no_models",),
        )

    # --- Fan out the SAME prompt across routes (concurrent, clamped) ------
    try:
        children = await _fan_out(
            prompt=prompt,
            routes=routes,
            child_runner_factory=child_runner_factory,
            cfg=cfg,
            parent_execution_id=parent_execution_id,
            turn_id=turn_id,
        )
    except Exception:  # noqa: BLE001 — degrade, never raise out of the recipe
        return CrossVerifyResult(
            enabled=True,
            consensus="",
            models_attempted=len(routes),
            consensus_mode=cfg.consensus_mode,
            reason_codes=("cross_verify_fan_out_error",),
        )

    candidates, countable = _collect_candidates(routes, children)  # type: ignore[arg-type]

    # --- Vote via best_of_n over the pre-computed candidate summaries -----
    consensus = _vote(countable, cfg=cfg, budget=budget)

    return CrossVerifyResult(
        enabled=True,
        consensus=str(consensus.value),
        candidates=tuple(candidates),
        models_attempted=len(routes),
        models_counted=len(countable),
        models_voted=consensus.n_attempted,
        agreement_count=consensus.agreement_count,
        confidence=consensus.confidence,
        consensus_mode=cfg.consensus_mode,
        reason_codes=() if countable else ("cross_verify_no_countable_children",),
    )


async def _fan_out(
    *,
    prompt: str,
    routes: tuple[ModelRoute, ...],
    child_runner_factory: Callable[[ModelRoute], object],
    cfg: CrossVerifyConfig,
    parent_execution_id: str,
    turn_id: str,
) -> tuple[ChildRunnerResult | BaseException, ...]:
    """Run one child per route concurrently under a concurrency clamp."""
    clamp = max(1, min(cfg.max_concurrency, _MAX_MODELS, len(routes)))
    semaphore = asyncio.Semaphore(clamp)

    async def _run_one(index: int, route: ModelRoute) -> ChildRunnerResult:
        async with semaphore:
            return await _run_child(
                index=index,
                route=route,
                prompt=prompt,
                child_runner_factory=child_runner_factory,
                cfg=cfg,
                parent_execution_id=parent_execution_id,
                turn_id=turn_id,
            )

    return tuple(
        await asyncio.gather(
            *(_run_one(i, route) for i, route in enumerate(routes)),
            return_exceptions=True,
        )
    )


async def _run_child(
    *,
    index: int,
    route: ModelRoute,
    prompt: str,
    child_runner_factory: Callable[[ModelRoute], object],
    cfg: CrossVerifyConfig,
    parent_execution_id: str,
    turn_id: str,
) -> ChildRunnerResult:
    """Run a single route's child through the local boundary.

    The boundary executes whatever runner the factory injected — a real
    model-backed runner (``openmagi_live_provider=True``) at runtime, or a
    fake stub (``openmagi_local_fake_provider=True``) in tests.  Both gates
    (``liveChildRunnerEnabled`` and ``localFakeChildRunnerEnabled``) are enabled
    so the boundary's own marker on the injected runner selects the correct
    execution path.  A runner that carries neither marker is blocked by the
    boundary (expected for untrusted objects).
    """
    provider, model = route
    request = ChildTaskRequest(
        parentExecutionId=parent_execution_id,
        turnId=turn_id,
        taskId=f"cross-verify-{index}",
        objective=prompt,
        role="general",
        delivery="return",
        provider=provider,
        model=model,
        budgetTokens=cfg.budget_tokens,
        budgetMs=cfg.budget_ms,
        metadata={
            "crossVerifyRoute": f"{provider}:{model}",
            "spawnDepth": _MAX_SPAWN_DEPTH,
            "maxSpawnDepth": _MAX_SPAWN_DEPTH,
            "parentOwnsLifecycle": True,
        },
    )
    boundary = LocalChildRunnerBoundary(
        ChildRunnerConfig(
            enabled=True,
            localFakeChildRunnerEnabled=True,
            liveChildRunnerEnabled=True,
            childProvider=provider,
            childModel=model,
        ),
        child_runner=child_runner_factory(route),
    )
    return await boundary.run(request)


def _collect_candidates(
    routes: tuple[ModelRoute, ...],
    children: tuple[ChildRunnerResult | BaseException, ...],
) -> tuple[list[CrossVerifyCandidate], list[str]]:
    """Build per-route candidates + the list of countable (non-empty) summaries.

    Failed / blocked / disabled children are recorded but NOT counted in the
    vote (mirrors best_of_n's failed-sample handling).  Summaries are read from
    the boundary's already-sanitised public projection — no raw text is touched.

    Elements that are ``BaseException`` instances (raised inside ``_fan_out``
    when ``return_exceptions=True``) are treated as failed children with
    ``status="error"`` and an empty summary.
    """
    candidates: list[CrossVerifyCandidate] = []
    countable: list[str] = []
    for route, child in zip(routes, children, strict=True):
        provider, model = route
        if isinstance(child, BaseException):
            candidates.append(
                CrossVerifyCandidate(
                    provider=provider,
                    model=model,
                    status="error",
                    summary="",
                    counted=False,
                )
            )
            continue
        summary = _summary_from_child(child)
        is_countable = child.status == "ok" and bool(summary)
        candidates.append(
            CrossVerifyCandidate(
                provider=provider,
                model=model,
                status=child.status,
                summary=summary,
                counted=is_countable,
            )
        )
        if is_countable:
            countable.append(summary)
    return candidates, countable


def _summary_from_child(child: ChildRunnerResult) -> str:
    """Extract the sanitised summary from a child's public projection."""
    projection = child.public_projection()
    envelope = projection.get("childEnvelope")
    if not isinstance(envelope, Mapping):
        return ""
    summary = envelope.get("summary")
    return summary if isinstance(summary, str) else ""


def _vote(
    countable: Sequence[str],
    *,
    cfg: CrossVerifyConfig,
    budget: Budget | None,
) -> BestOfNResult[Any]:
    """Vote over pre-computed candidate summaries via ``run_best_of_n``.

    best_of_n drives ``runner_fn(task, *, workspace_root, seed, **kwargs)`` for
    ``seed in [base_seed, base_seed + n)``.  We use ``base_seed=0`` and a
    runner that returns the candidate at ``seed`` — so best_of_n votes over the
    already-collected summaries WITHOUT re-running any model.  ``enabled=True``
    here is the LOCAL voter switch; the cross-verify gate above already
    authorised the run.

    When ``countable`` is empty (all children failed / were filtered out) the
    function short-circuits and returns a zero-valued ``BestOfNResult`` directly
    WITHOUT calling ``run_best_of_n``, avoiding the misleading
    ``agreement_count=1 / confidence=1.0`` artefact that a disabled n=1 stub
    would otherwise produce.
    """
    if not countable:
        return BestOfNResult(
            value="",
            confidence=0.0,
            n_attempted=0,
            n_successful=0,
            agreement_count=0,
            samples=(),
            consensus_mode=cfg.consensus_mode,
        )

    candidates = tuple(countable)

    def candidate_runner(_task: Any, *, workspace_root: str, seed: int, **_kw: Any) -> str:
        # seed runs [0, n) because base_seed=0; index directly into candidates.
        return candidates[seed] if 0 <= seed < len(candidates) else ""

    return run_best_of_n(
        candidate_runner,
        task=None,
        config=BestOfNConfig(
            n=len(candidates),
            base_seed=0,
            enabled=True,
            consensus_mode=cfg.consensus_mode,
        ),
        budget=budget,
    )


def _resolve_routes(value: Sequence[ModelRoute]) -> tuple[ModelRoute, ...]:
    """Clamp + dedup ad-hoc ``models=`` overrides (config path already validated)."""
    clean: list[ModelRoute] = []
    seen: set[ModelRoute] = set()
    for item in value or ():
        if not isinstance(item, tuple | list) or len(item) != 2:
            continue
        provider, model = item
        if not isinstance(provider, str) or not isinstance(model, str):
            continue
        provider = provider.strip()
        model = model.strip()
        if not provider or not model:
            continue
        route: ModelRoute = (provider, model)
        if route in seen:
            continue
        seen.add(route)
        clean.append(route)
        if len(clean) >= _MAX_MODELS:
            break
    return tuple(clean)


__all__ = [
    "CrossVerifyCandidate",
    "CrossVerifyConfig",
    "CrossVerifyResult",
    "ModelRoute",
    "is_cross_verify_enabled",
    "run_cross_verify",
]
