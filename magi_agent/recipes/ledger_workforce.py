"""Multi-agent workforce mode for ledger-based orchestration (Phase 4).

Default-OFF.  This module extends :mod:`magi_agent.recipes.ledger_orchestrator`
with a ``multi_agent_workforce`` mode that assigns worker roles to plan steps
using a deterministic :class:`RoleAssignmentPolicy` and can batch independent
steps for parallel execution.

**Activation guard:** ``multi_agent_workforce`` mode requires BOTH:
  1. ``MAGI_LEDGER_ORCHESTRATOR_ENABLED=true``
  2. ``LedgerOrchestratorConfig.orchestration_mode == "multi_agent_workforce"``

The default is ``"single_agent"`` (Phase 3 behaviour).  Phase 4 is additive and
backwards-compatible.

Architecture notes
------------------
* :class:`RoleAssignmentPolicy` maps ``(fact_kind, step_evidence_hint) →
  ResearchChildRoleName`` using a frozen deterministic policy table.
* :func:`assign_worker_role` applies the policy to a :class:`LedgerPlanStep`.
* :func:`batch_independent_steps` partitions a set of pending steps into
  parallelisable batches (steps whose ``depends_on_fact_ids`` are all resolved
  and don't overlap each other's ``produces_fact_ids``).
* The workforce orchestrator calls the ``StepExecutor`` for each batch in an
  ``asyncio.gather``-compatible pattern (the gather is not performed in this
  module since the executor is synchronous in Phase 3; the batch structure is
  produced so callers can parallelize).
"""
from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from enum import Enum
from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from magi_agent.config.env import worker_routing_llm_enabled
from magi_agent.research.child_roles import ResearchChildRoleName, RESEARCH_CHILD_ROLE_NAMES
from magi_agent.recipes.ledger_task import (
    LedgerFact,
    LedgerFactKind,
    LedgerPlanStep,
    TaskLedgerContract,
)


# ---------------------------------------------------------------------------
# Orchestration mode enum
# ---------------------------------------------------------------------------

class LedgerOrchestrationMode(str, Enum):
    """Orchestration mode selector."""

    single_agent = "single_agent"
    """Phase 3 — one LLM handles all steps sequentially."""

    multi_agent_workforce = "multi_agent_workforce"
    """Phase 4 — steps are dispatched to role-specific workers; independent steps
    may run in parallel via ``asyncio.gather``.

    Requires the ``ResearchChildRunnerConfig.enabled`` flag to be True in the
    calling context (not enforced in this contract — checked by the caller).
    """


# ---------------------------------------------------------------------------
# Role assignment policy
# ---------------------------------------------------------------------------

# Deterministic mapping: (dominant_fact_kind, step_evidence_hint) → role.
# "step_evidence_hint" is a coarse category derived from the step description.
_ROLE_TABLE: dict[tuple[str, str], ResearchChildRoleName] = {
    # Lookup / search steps — regardless of fact kind.
    ("known_fact", "search"): "research_searcher",
    ("working_guess", "search"): "research_searcher",
    ("verified_intermediate", "search"): "research_searcher",
    ("open_question", "search"): "research_searcher",
    # Inspection steps — file/document inspection.
    ("known_fact", "inspect"): "source_inspector",
    ("working_guess", "inspect"): "source_inspector",
    ("verified_intermediate", "inspect"): "source_inspector",
    ("open_question", "inspect"): "source_inspector",
    # Mapping/extraction steps.
    ("known_fact", "map"): "claim_mapper",
    ("working_guess", "map"): "claim_mapper",
    ("verified_intermediate", "map"): "claim_mapper",
    ("open_question", "map"): "claim_mapper",
    # Verification steps.
    ("known_fact", "verify"): "research_verifier",
    ("working_guess", "verify"): "research_verifier",
    ("verified_intermediate", "verify"): "research_verifier",
    ("open_question", "verify"): "research_verifier",
    # Synthesis / summary steps.
    ("known_fact", "synthesize"): "synthesis_reviewer",
    ("working_guess", "synthesize"): "synthesis_reviewer",
    ("verified_intermediate", "synthesize"): "synthesis_reviewer",
    ("open_question", "synthesize"): "synthesis_reviewer",
    # Default — unknown evidence hint.
    ("known_fact", "unknown"): "claim_mapper",
    ("working_guess", "unknown"): "research_verifier",
    ("verified_intermediate", "unknown"): "synthesis_reviewer",
    ("open_question", "unknown"): "research_searcher",
}

# Public, planner-facing "when to use" advertisement for each worker role.
# Data only — a planner prompt may surface these so the model can emit an
# explicit ``worker_role`` per step (honored when
# ``MAGI_WORKER_ROUTING_LLM_ENABLED`` is on).  Keys are the valid worker roles.
WORKER_ROLE_WHEN_TO_USE: dict[ResearchChildRoleName, str] = {
    "research_searcher": (
        "Use to look up, search, retrieve, or fetch external information for a "
        "step that needs new source material."
    ),
    "source_inspector": (
        "Use to open, read, parse, or extract content from a specific source, "
        "file, or attachment that is already identified."
    ),
    "claim_mapper": (
        "Use to identify, enumerate, or structure the claims and entities within "
        "gathered material into a mapped representation."
    ),
    "research_verifier": (
        "Use to verify, validate, confirm, or cross-check a working guess or claim "
        "against corroborating evidence."
    ),
    "synthesis_reviewer": (
        "Use to assemble, summarize, compose, or write the final synthesized answer "
        "from verified facts."
    ),
}

# Valid planner-provided worker roles (the keys the role table / honor path
# accepts).  ``LedgerPlanStep.worker_role`` defaults to ``"orchestrator"`` which
# is NOT a worker role and is treated as "no explicit role provided".
_VALID_WORKER_ROLES: frozenset[ResearchChildRoleName] = frozenset(RESEARCH_CHILD_ROLE_NAMES)

_SEARCH_KEYWORDS = frozenset({"search", "look", "find", "retrieve", "fetch", "web", "query"})
_INSPECT_KEYWORDS = frozenset({"inspect", "read", "open", "parse", "extract", "file", "attachment"})
_MAP_KEYWORDS = frozenset({"map", "extract", "identify", "list", "enumerate", "structure"})
_VERIFY_KEYWORDS = frozenset({"verify", "check", "validate", "confirm", "cross", "corroborate"})
_SYNTHESIZE_KEYWORDS = frozenset({"synthesize", "assemble", "summarize", "compose", "write", "final", "answer"})


def _infer_evidence_hint(description: str) -> str:
    """Derive a coarse evidence hint from a step description (lowercase keyword match)."""
    words = frozenset(description.lower().split())
    if words & _VERIFY_KEYWORDS:
        return "verify"
    if words & _SYNTHESIZE_KEYWORDS:
        return "synthesize"
    if words & _INSPECT_KEYWORDS:
        return "inspect"
    if words & _MAP_KEYWORDS:
        return "map"
    if words & _SEARCH_KEYWORDS:
        return "search"
    return "unknown"


def _dominant_fact_kind(
    depends_on_fact_ids: tuple[str, ...],
    task_ledger: TaskLedgerContract,
) -> str:
    """Return the 'worst' (most speculative) fact kind among the step's dependencies."""
    kinds = []
    for fid in depends_on_fact_ids:
        fact = task_ledger.fact_by_id(fid)
        if fact is not None:
            kinds.append(fact.kind.value)
    if not kinds:
        return "open_question"
    # Priority: open_question > working_guess > verified_intermediate > known_fact
    _PRIORITY = {
        "open_question": 4,
        "working_guess": 3,
        "verified_intermediate": 2,
        "known_fact": 1,
    }
    return max(kinds, key=lambda k: _PRIORITY.get(k, 0))


class RoleAssignmentPolicy(BaseModel):
    """Deterministic typed policy for worker-role assignment.

    Maps ``(dominant_fact_kind × evidence_hint) → ResearchChildRoleName``.
    The policy table is frozen and sha256-digested for auditability.

    All assignments are made from the fixed ``_ROLE_TABLE`` — no LLM call.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        validate_default=True,
        populate_by_name=True,
    )

    policy_name: str = Field(default="default_research_role_policy")
    default_off: Literal[True] = Field(default=True)
    orchestration_mode: LedgerOrchestrationMode = Field(
        default=LedgerOrchestrationMode.multi_agent_workforce
    )

    @field_validator("policy_name")
    @classmethod
    def _validate_policy_name(cls, value: str) -> str:
        import re
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:!\-]{0,127}", value):
            raise ValueError("policy_name must be a public-safe identifier")
        return value

    def assign_role(
        self,
        step: LedgerPlanStep,
        task_ledger: TaskLedgerContract,
        *,
        env: Mapping[str, str] | None = None,
        emit: Callable[[dict[str, object]], object] | None = None,
    ) -> ResearchChildRoleName:
        """Assign the best worker role for ``step`` given current ``task_ledger``.

        Parameters
        ----------
        step:
            The plan step to assign a role to.
        task_ledger:
            Current task ledger (used to determine dependency fact kinds).
        env:
            Optional environment mapping for flag resolution (defaults to the
            process environment).
        emit:
            Optional best-effort sink for the ADVISORY ``worker_route_decided``
            decision event.  Default ``None`` → no-op.  The event is emitted ONLY
            when ``MAGI_WORKER_ROUTING_LLM_ENABLED`` is on, so flag-OFF callers
            (including the keyword fallback path that runs even when the flag is
            off) stay byte-identical.  Emission never raises and never changes the
            returned role.

        Returns
        -------
        ResearchChildRoleName
            The role that should execute this step.

        Notes
        -----
        When ``MAGI_WORKER_ROUTING_LLM_ENABLED`` is on **and** the step carries
        a valid explicit planner-provided ``worker_role`` (a
        :data:`ResearchChildRoleName`, not the ``"orchestrator"`` default), that
        role is honored directly and keyword inference is skipped.  When the flag
        is off, or the role is missing/invalid, behaviour is byte-identical to the
        keyword-inference path (``_infer_evidence_hint`` → role table).
        """
        flag_on = worker_routing_llm_enabled(env)
        if flag_on and step.worker_role in _VALID_WORKER_ROLES:
            role = step.worker_role
            _emit_worker_route_decided(emit, flag_on=flag_on, role=role, source="model")
            return role  # type: ignore[return-value]
        dominant = _dominant_fact_kind(step.depends_on_fact_ids, task_ledger)
        hint = _infer_evidence_hint(step.description)
        role = _ROLE_TABLE.get((dominant, hint))
        if role is None:
            # Fallback: use unknown hint row → "default" routing source.
            role = _ROLE_TABLE.get((dominant, "unknown"), "research_searcher")
            source = "default"
        else:
            source = "default" if hint == "unknown" else "keyword"
        _emit_worker_route_decided(emit, flag_on=flag_on, role=role, source=source)
        return role

    def policy_digest(self) -> str:
        """Deterministic sha256 of the policy table snapshot."""
        payload = {
            "policyName": self.policy_name,
            "roleTable": {
                f"{fk}:{hint}": role
                for (fk, hint), role in sorted(_ROLE_TABLE.items())
            },
            "defaultOff": True,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return f"sha256:{sha256(encoded).hexdigest()}"

    def public_projection(self) -> dict[str, object]:
        return {
            "policyName": self.policy_name,
            "orchestrationMode": self.orchestration_mode.value,
            "defaultOff": True,
            "policyDigest": self.policy_digest(),
        }


def project_worker_route_decided_event(
    *,
    role: ResearchChildRoleName,
    source: str,
) -> dict[str, object]:
    """Project a worker-route decision into a public-safe ADVISORY event dict.

    Mirrors the projection seam used elsewhere (``coding/repair_loop``): a pure
    function returning a JSON-safe dict carrying only the chosen worker ``role``
    and the ``source`` of the decision — ``"model"`` (honored planner role),
    ``"keyword"`` (``_infer_evidence_hint``), or ``"default"`` (table default
    row).  Never gating — purely for debuggability.
    """
    return {
        "type": "worker_route_decided",
        "role": role,
        "source": source,
    }


def _emit_worker_route_decided(
    emit: Callable[[dict[str, object]], object] | None,
    *,
    flag_on: bool,
    role: ResearchChildRoleName,
    source: str,
) -> None:
    """Best-effort emit of the advisory ``worker_route_decided`` event.

    Gated on ``flag_on`` (``MAGI_WORKER_ROUTING_LLM_ENABLED``) so that flag-OFF
    behaviour — including the keyword fallback path that runs even when the flag
    is off — emits NOTHING and stays byte-identical.  Fail-safe: no-op when no
    sink is supplied and swallows any emitter exception so telemetry can never
    raise or change the assigned role.
    """
    if not flag_on or emit is None:
        return
    try:
        emit(project_worker_route_decided_event(role=role, source=source))
    except Exception:
        # Advisory only — never let telemetry break routing.
        return


def assign_worker_role(
    step: LedgerPlanStep,
    task_ledger: TaskLedgerContract,
    *,
    policy: RoleAssignmentPolicy | None = None,
    env: Mapping[str, str] | None = None,
    emit: Callable[[dict[str, object]], object] | None = None,
) -> ResearchChildRoleName:
    """Convenience wrapper — assign a worker role to ``step``.

    Parameters
    ----------
    step:
        The plan step.
    task_ledger:
        Current task ledger.
    policy:
        Optional custom policy.  Defaults to ``RoleAssignmentPolicy()``.
    env:
        Optional environment mapping for flag resolution.
    emit:
        Optional best-effort sink for the advisory ``worker_route_decided``
        event (see :meth:`RoleAssignmentPolicy.assign_role`).

    Returns
    -------
    ResearchChildRoleName
        The assigned role.
    """
    _policy = policy or RoleAssignmentPolicy()
    return _policy.assign_role(step, task_ledger, env=env, emit=emit)


# ---------------------------------------------------------------------------
# Parallel batch construction
# ---------------------------------------------------------------------------

class StepBatch(BaseModel):
    """A set of steps that can execute in parallel.

    Steps in the same batch:
    - All have their ``depends_on_fact_ids`` satisfied in the current ledger.
    - Do not share any ``produces_fact_ids`` (no write conflicts).
    - Are all in ``"pending"`` status.

    The batch is given an assigned worker role for each step.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    batch_id: str
    steps: tuple[LedgerPlanStep, ...]
    assigned_roles: tuple[ResearchChildRoleName, ...]
    """Parallel to ``steps`` — role[i] is the role assigned to steps[i]."""

    default_off: Literal[True] = Field(default=True)

    @model_validator(mode="after")
    def _validate_shape(self) -> StepBatch:
        if len(self.steps) != len(self.assigned_roles):
            raise ValueError("steps and assigned_roles must have the same length")
        return self


def batch_independent_steps(
    task_ledger: TaskLedgerContract,
    *,
    policy: RoleAssignmentPolicy | None = None,
    batch_id_prefix: str = "batch",
    batch_number: int = 0,
    env: Mapping[str, str] | None = None,
    emit: Callable[[dict[str, object]], object] | None = None,
) -> StepBatch | None:
    """Identify the next batch of parallelisable pending steps.

    A step is eligible for the current batch when:
    1. Its status is ``"pending"``.
    2. All ``depends_on_fact_ids`` are in the task ledger.
    3. None of its ``depends_on_fact_ids`` is a ``working_guess`` (those need
       an implicit verifier step first — they are not batched).
    4. Its ``produces_fact_ids`` do not overlap with any other batch member's
       ``produces_fact_ids`` (prevents write conflicts).

    Parameters
    ----------
    task_ledger:
        Current task ledger.
    policy:
        Optional role assignment policy.
    batch_id_prefix:
        Prefix for the batch identifier.
    batch_number:
        Monotonically increasing batch counter (used for batch_id).

    Returns
    -------
    StepBatch | None
        The next parallelisable batch, or ``None`` if no steps are ready.
    """
    _policy = policy or RoleAssignmentPolicy()
    resolved_ids = frozenset(f.fact_id for f in task_ledger.facts)
    guess_ids = frozenset(f.fact_id for f in task_ledger.facts if f.kind == LedgerFactKind.working_guess)

    batch_steps: list[LedgerPlanStep] = []
    batch_produces: set[str] = set()
    batch_roles: list[ResearchChildRoleName] = []

    for step in task_ledger.plan:
        if step.status != "pending":
            continue
        deps = frozenset(step.depends_on_fact_ids)
        if not deps.issubset(resolved_ids):
            continue
        if deps & guess_ids:
            # Has speculative deps — requires implicit verifier first; skip batch.
            continue
        produces = frozenset(step.produces_fact_ids)
        if produces & batch_produces:
            # Write conflict with an already-batched step.
            continue
        role = _policy.assign_role(step, task_ledger, env=env, emit=emit)
        batch_steps.append(step)
        batch_produces |= produces
        batch_roles.append(role)

    if not batch_steps:
        return None

    return StepBatch(
        batch_id=f"{batch_id_prefix}:{batch_number}",
        steps=tuple(batch_steps),
        assigned_roles=tuple(batch_roles),
    )


__all__ = [
    "WORKER_ROLE_WHEN_TO_USE",
    "LedgerOrchestrationMode",
    "RoleAssignmentPolicy",
    "StepBatch",
    "assign_worker_role",
    "batch_independent_steps",
    "project_worker_route_decided_event",
]
