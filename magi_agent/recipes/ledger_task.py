"""Task ledger contract — facts, guesses, and plan for ledger-based orchestration.

Default-OFF.  All classes carry ``default_off: Literal[True] = True``.
No ADK runner, provider call, browser, or live execution is attached.

Design goals
------------
* Make the distinction between "known fact" (evidence-backed) and
  "working guess" (not yet corroborated) **structurally explicit**.
* Track provenance chains (``depends_on``) so that if a fact is invalidated,
  all downstream facts can be transitively identified.
* Enforce schema via frozen Pydantic + sha256 digest so the LLM cannot silently
  write bad state into the ledger.
* Integrate with :mod:`magi_agent.research.claim_graph` via
  ``LedgerFact.claim_ref_id`` and the ``kind_from_support_verdict`` helper.
"""
from __future__ import annotations

import json
import re
from enum import Enum
from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from magi_agent.research.child_roles import ResearchChildRoleName


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="never",
    hide_input_in_errors=True,
)

_PUBLIC_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:\-]{0,127}$")
_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_SECRET_TEXT_RE = re.compile(
    r"(?:Bearer\s+[A-Za-z0-9._~+/=-]{8,}|gh[opusr]_[A-Za-z0-9_]{8,}|"
    r"github_pat_[A-Za-z0-9_]{8,}|AKIA[0-9A-Z]{12,}|ASIA[0-9A-Z]{12,}|"
    r"AIza[0-9A-Za-z_-]{12,}|xox[baprs]-[A-Za-z0-9-]{8,}|"
    r"sk-(?:live|test)?[-_A-Za-z0-9]{8,}|\b\d{5,}:[A-Za-z0-9_-]{8,}\b|"
    r"[A-Z0-9_]*(?:SECRET|TOKEN|KEY|PASSWORD|COOKIE)[A-Z0-9_]*\s*[:=]\s*"
    r"[^,\s}{\n]{4,})",
    re.IGNORECASE,
)
_PRIVATE_PATH_RE = re.compile(
    r"(?:/Users/[^,\s\"']+|/home/[^,\s\"']+|/root/[^,\s\"']+|"
    r"/workspace/[^,\s\"']+|/data/bots/[^,\s\"']+|"
    r"/var/lib/kubelet/[^,\s\"']+|pvc-[A-Za-z0-9-]+)",
    re.IGNORECASE,
)
_UNSAFE_TEXT_RE = re.compile(
    r"https?://|file://|raw[_ -]?(?:source|transcript|tool|prompt|output|result|log)|"
    r"source[_ -]?(?:body|content|html|text)|hidden[_ -]?reasoning|"
    r"chain[_ -]?of[_ -]?thought|authorization|cookie|set-cookie|"
    r"api[_ -]?key|secret|model[_ -]?summary|model[_ -]?generated[_ -]?summary",
    re.IGNORECASE,
)

# Maximum length for the public description field on LedgerPlanStep
_MAX_DESCRIPTION_LEN = 240


# ---------------------------------------------------------------------------
# LedgerFactKind
# ---------------------------------------------------------------------------

class LedgerFactKind(str, Enum):
    """Classification of a task ledger fact."""

    known_fact = "known_fact"
    """Supported by evidence — ``ResearchClaimNode.support_verdict == "supported"``."""

    working_guess = "working_guess"
    """Not evaluated or weak — must not be used as a chain input without explicit
    upgrade (a ``research_verifier`` step is inserted first).
    """

    verified_intermediate = "verified_intermediate"
    """Intermediate value that passed a verification step and was promoted."""

    open_question = "open_question"
    """Identified gap needing resolution."""


def kind_from_support_verdict(verdict: str) -> LedgerFactKind:
    """Derive a :class:`LedgerFactKind` from a ``ResearchClaimSupportVerdict``.

    Parameters
    ----------
    verdict:
        One of ``"supported"``, ``"weak"``, ``"unsupported"``,
        ``"contradicted"``, ``"stale"``, ``"not_evaluated"``.

    Returns
    -------
    LedgerFactKind
        The most appropriate kind for the given verdict.
    """
    _VERDICT_MAP: dict[str, LedgerFactKind] = {
        "supported": LedgerFactKind.known_fact,
        "weak": LedgerFactKind.working_guess,
        "unsupported": LedgerFactKind.open_question,
        "contradicted": LedgerFactKind.open_question,
        "stale": LedgerFactKind.working_guess,
        "not_evaluated": LedgerFactKind.working_guess,
    }
    try:
        return _VERDICT_MAP[verdict]
    except KeyError:
        raise ValueError(f"unknown ResearchClaimSupportVerdict: {verdict!r}") from None


# ---------------------------------------------------------------------------
# LedgerFact
# ---------------------------------------------------------------------------

class LedgerFact(BaseModel):
    """A single task-ledger fact — either a known fact or a working guess.

    :attr:`value_digest` enables value-mismatch detection: if a later step
    retrieves a conflicting value for the same fact, the digest mismatch is
    detected and marks the fact as requiring re-retrieval.

    :attr:`depends_on` builds a provenance chain: if fact A was used to produce
    fact B and fact A is later found wrong, :func:`transitively_invalidated_fact_ids`
    can enumerate all transitively invalid facts.
    """

    model_config = _MODEL_CONFIG

    fact_id: str
    """Stable public identifier.  Must match ``_PUBLIC_ID_RE``."""

    kind: LedgerFactKind
    """Whether this fact is evidence-backed, speculative, verified, or an open question."""

    claim_ref_id: str | None = Field(default=None)
    """Links to ``ResearchClaimNode.claim_id`` in the running evidence graph."""

    value_digest: str | None = Field(default=None)
    """sha256 of the claimed value string — enables value-mismatch detection on update."""

    confidence: Literal["high", "medium", "low", "unknown"] = "unknown"

    depends_on: tuple[str, ...] = Field(default=())
    """fact_ids this fact's value depended on — tracks chain provenance."""

    public_label: str | None = Field(default=None)
    """Optional short human-readable label safe for prompt injection (max 80 chars)."""

    default_off: Literal[True] = Field(default=True)

    @field_validator("fact_id")
    @classmethod
    def _validate_fact_id(cls, value: str) -> str:
        return _public_id(value, "fact_id")

    @field_validator("claim_ref_id")
    @classmethod
    def _validate_claim_ref_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _public_id(value, "claim_ref_id")

    @field_validator("value_digest")
    @classmethod
    def _validate_value_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _DIGEST_RE.fullmatch(value):
            raise ValueError("value_digest must be a sha256 hex digest")
        return value

    @field_validator("depends_on")
    @classmethod
    def _validate_depends_on(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("depends_on must not contain duplicate fact_ids")
        for item in value:
            _public_id(item, "depends_on item")
        return value

    @field_validator("public_label")
    @classmethod
    def _validate_public_label(cls, value: str | None) -> str | None:
        if value is None:
            return None
        clean = value.strip()
        if not clean:
            raise ValueError("public_label must be non-empty when provided")
        if len(clean) > 80:
            raise ValueError("public_label must be at most 80 characters")
        _reject_unsafe_public_text(clean, "public_label")
        return clean

    def public_projection(self) -> dict[str, object]:
        return {
            "factId": self.fact_id,
            "kind": self.kind.value,
            "claimRefId": self.claim_ref_id,
            "valueDigest": self.value_digest,
            "confidence": self.confidence,
            "dependsOn": self.depends_on,
            "publicLabel": self.public_label,
            "defaultOff": True,
        }


def make_fact(
    *,
    fact_id: str,
    kind: LedgerFactKind,
    claim_ref_id: str | None = None,
    value_digest: str | None = None,
    confidence: Literal["high", "medium", "low", "unknown"] = "unknown",
    depends_on: tuple[str, ...] = (),
    public_label: str | None = None,
) -> LedgerFact:
    """Convenience factory for :class:`LedgerFact`."""
    return LedgerFact(
        fact_id=fact_id,
        kind=kind,
        claim_ref_id=claim_ref_id,
        value_digest=value_digest,
        confidence=confidence,
        depends_on=depends_on,
        public_label=public_label,
    )


def value_digest_for(value: str) -> str:
    """Compute the canonical sha256 digest for a fact value string."""
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{sha256(encoded).hexdigest()}"


# ---------------------------------------------------------------------------
# LedgerPlanStep
# ---------------------------------------------------------------------------

LedgerWorkerRole = ResearchChildRoleName | Literal["orchestrator"]


class LedgerPlanStep(BaseModel):
    """A single step in the task ledger plan.

    Steps are ordered by dependency: a step may not execute until all
    ``depends_on_fact_ids`` are resolved.  If any dependency is a
    ``working_guess``, the orchestrator inserts an implicit
    ``research_verifier`` step before this one.
    """

    model_config = _MODEL_CONFIG

    step_id: str
    description: str
    """Short description of the step (max 240 chars), safe for public logging."""

    worker_role: LedgerWorkerRole = "orchestrator"
    """Which worker role should execute this step."""

    depends_on_fact_ids: tuple[str, ...] = Field(default=())
    """Which facts this step consumes as inputs."""

    produces_fact_ids: tuple[str, ...] = Field(default=())
    """Which facts this step is expected to resolve."""

    status: Literal["pending", "in_progress", "completed", "skipped", "failed"] = "pending"

    replan_count: int = Field(default=0, ge=0)
    """How many times this step was regenerated by stall-detection."""

    default_off: Literal[True] = Field(default=True)

    @field_validator("step_id")
    @classmethod
    def _validate_step_id(cls, value: str) -> str:
        return _public_id(value, "step_id")

    @field_validator("description")
    @classmethod
    def _validate_description(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("description must be non-empty")
        if len(clean) > _MAX_DESCRIPTION_LEN:
            raise ValueError(f"description must be at most {_MAX_DESCRIPTION_LEN} characters")
        _reject_unsafe_public_text(clean, "description")
        return clean

    @field_validator("depends_on_fact_ids", "produces_fact_ids")
    @classmethod
    def _validate_fact_id_tuples(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("fact_id tuples must not contain duplicates")
        for item in value:
            _public_id(item, "fact_id ref")
        return value

    def public_projection(self) -> dict[str, object]:
        return {
            "stepId": self.step_id,
            "description": self.description,
            "workerRole": self.worker_role,
            "dependsOnFactIds": self.depends_on_fact_ids,
            "producesFactIds": self.produces_fact_ids,
            "status": self.status,
            "replanCount": self.replan_count,
            "defaultOff": True,
        }


def make_plan_step(
    *,
    step_id: str,
    description: str,
    worker_role: LedgerWorkerRole = "orchestrator",
    depends_on_fact_ids: tuple[str, ...] = (),
    produces_fact_ids: tuple[str, ...] = (),
    status: Literal["pending", "in_progress", "completed", "skipped", "failed"] = "pending",
    replan_count: int = 0,
) -> LedgerPlanStep:
    """Convenience factory for :class:`LedgerPlanStep`."""
    return LedgerPlanStep(
        step_id=step_id,
        description=description,
        worker_role=worker_role,
        depends_on_fact_ids=depends_on_fact_ids,
        produces_fact_ids=produces_fact_ids,
        status=status,
        replan_count=replan_count,
    )


# ---------------------------------------------------------------------------
# TaskLedgerContract
# ---------------------------------------------------------------------------

class TaskLedgerContract(BaseModel):
    """Immutable task ledger snapshot.

    The ledger tracks:
    * ``facts`` — the current set of known facts and working guesses.
    * ``plan`` — the ordered sequence of steps to resolve the objective.
    * ``objective_digest`` — sha256 of the task objective text (canonical identity).
    * ``ledger_digest`` — deterministic sha256 of the whole ledger state.

    Any mutation produces a new object with a new ``ledger_digest``.  The LLM
    only ever sees the :meth:`public_projection` output — it never writes directly
    to this object.
    """

    model_config = _MODEL_CONFIG

    ledger_id: str
    objective_digest: str
    """sha256 of the task objective text — canonical identity of what we're solving."""

    facts: tuple[LedgerFact, ...] = Field(default=())
    plan: tuple[LedgerPlanStep, ...] = Field(default=())

    acceptance_criteria_ref: str | None = Field(default=None)
    """criteria_set_id of the ResearchAcceptanceCriteriaSet governing done-ness."""

    ledger_digest: str
    """Deterministic sha256 over all fields — any mutation produces a new digest."""

    default_off: Literal[True] = Field(default=True)

    @field_validator("ledger_id")
    @classmethod
    def _validate_ledger_id(cls, value: str) -> str:
        return _public_id(value, "ledger_id")

    @field_validator("objective_digest", "ledger_digest")
    @classmethod
    def _validate_digest_fields(cls, value: str) -> str:
        if not _DIGEST_RE.fullmatch(value):
            raise ValueError("digest fields must be sha256 hex digests")
        return value

    @field_validator("acceptance_criteria_ref")
    @classmethod
    def _validate_criteria_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _public_id(value, "acceptance_criteria_ref")

    @field_validator("facts")
    @classmethod
    def _validate_facts(cls, value: tuple[LedgerFact, ...]) -> tuple[LedgerFact, ...]:
        ids = [f.fact_id for f in value]
        if len(set(ids)) != len(ids):
            raise ValueError("facts must have unique fact_ids")
        return value

    @field_validator("plan")
    @classmethod
    def _validate_plan(cls, value: tuple[LedgerPlanStep, ...]) -> tuple[LedgerPlanStep, ...]:
        ids = [s.step_id for s in value]
        if len(set(ids)) != len(ids):
            raise ValueError("plan steps must have unique step_ids")
        return value

    @model_validator(mode="after")
    def _validate_ledger_digest(self) -> TaskLedgerContract:
        expected = _compute_ledger_digest(self)
        if self.ledger_digest != expected:
            raise ValueError("ledger_digest must be bound to all ledger fields")
        return self

    # ------------------------------------------------------------------
    # Public projection
    # ------------------------------------------------------------------

    def public_projection(self) -> dict[str, object]:
        return {
            "ledgerId": self.ledger_id,
            "objectiveDigest": self.objective_digest,
            "facts": tuple(f.public_projection() for f in self.facts),
            "plan": tuple(s.public_projection() for s in self.plan),
            "acceptanceCriteriaRef": self.acceptance_criteria_ref,
            "ledgerDigest": self.ledger_digest,
            "defaultOff": True,
        }

    # ------------------------------------------------------------------
    # Fact lookup helpers
    # ------------------------------------------------------------------

    def fact_by_id(self, fact_id: str) -> LedgerFact | None:
        for fact in self.facts:
            if fact.fact_id == fact_id:
                return fact
        return None

    def known_facts(self) -> tuple[LedgerFact, ...]:
        return tuple(
            f for f in self.facts
            if f.kind in (LedgerFactKind.known_fact, LedgerFactKind.verified_intermediate)
        )

    def working_guesses(self) -> tuple[LedgerFact, ...]:
        return tuple(f for f in self.facts if f.kind == LedgerFactKind.working_guess)

    def open_questions(self) -> tuple[LedgerFact, ...]:
        return tuple(f for f in self.facts if f.kind == LedgerFactKind.open_question)


def make_task_ledger(
    *,
    ledger_id: str,
    objective_text: str,
    facts: tuple[LedgerFact, ...] = (),
    plan: tuple[LedgerPlanStep, ...] = (),
    acceptance_criteria_ref: str | None = None,
) -> TaskLedgerContract:
    """Factory — computes ``objective_digest`` and ``ledger_digest`` automatically.

    Parameters
    ----------
    ledger_id:
        Stable public identifier for this ledger instance.
    objective_text:
        The full task objective text.  Its sha256 is stored as ``objective_digest``;
        the raw text is never persisted in the contract.
    facts:
        Initial set of ledger facts (usually empty at construction time).
    plan:
        Initial plan steps (usually filled by the orchestrator after the first
        planning step).
    acceptance_criteria_ref:
        Optional link to a ``ResearchAcceptanceCriteriaSet`` governing done-ness.
    """
    objective_digest = _digest_for(objective_text)
    stub = _ledger_digest_payload(
        ledger_id=ledger_id,
        objective_digest=objective_digest,
        facts=facts,
        plan=plan,
        acceptance_criteria_ref=acceptance_criteria_ref,
    )
    digest = _digest_for(stub)
    return TaskLedgerContract(
        ledger_id=ledger_id,
        objective_digest=objective_digest,
        facts=facts,
        plan=plan,
        acceptance_criteria_ref=acceptance_criteria_ref,
        ledger_digest=digest,
    )


def update_task_ledger(
    ledger: TaskLedgerContract,
    *,
    facts: tuple[LedgerFact, ...] | None = None,
    plan: tuple[LedgerPlanStep, ...] | None = None,
    acceptance_criteria_ref: str | None = None,
) -> TaskLedgerContract:
    """Return a new :class:`TaskLedgerContract` with the given fields updated.

    All other fields are carried forward.  The new ``ledger_digest`` covers the
    updated state.
    """
    new_facts = facts if facts is not None else ledger.facts
    new_plan = plan if plan is not None else ledger.plan
    new_criteria = acceptance_criteria_ref if acceptance_criteria_ref is not None else ledger.acceptance_criteria_ref
    stub = _ledger_digest_payload(
        ledger_id=ledger.ledger_id,
        objective_digest=ledger.objective_digest,
        facts=new_facts,
        plan=new_plan,
        acceptance_criteria_ref=new_criteria,
    )
    digest = _digest_for(stub)
    return TaskLedgerContract(
        ledger_id=ledger.ledger_id,
        objective_digest=ledger.objective_digest,
        facts=new_facts,
        plan=new_plan,
        acceptance_criteria_ref=new_criteria,
        ledger_digest=digest,
    )


# ---------------------------------------------------------------------------
# Chain invalidation
# ---------------------------------------------------------------------------

def transitively_invalidated_fact_ids(
    ledger: TaskLedgerContract,
    invalidated_id: str,
) -> frozenset[str]:
    """Return all fact_ids that transitively depend on ``invalidated_id``.

    If fact A is invalidated and fact B ``depends_on`` A, fact B is also invalid
    — and so are all facts that depend on B, recursively.

    The ``invalidated_id`` itself is included in the result set.

    Parameters
    ----------
    ledger:
        The current task ledger.
    invalidated_id:
        The fact_id of the initially invalidated fact.

    Returns
    -------
    frozenset[str]
        All transitively invalid fact_ids (including ``invalidated_id``).
    """
    # Build the reverse dependency graph: fact_id -> set of fact_ids that depend on it.
    dependents: dict[str, set[str]] = {f.fact_id: set() for f in ledger.facts}
    for fact in ledger.facts:
        for dep in fact.depends_on:
            if dep in dependents:
                dependents[dep].add(fact.fact_id)

    # BFS from invalidated_id
    visited: set[str] = set()
    queue = [invalidated_id]
    while queue:
        current = queue.pop()
        if current in visited:
            continue
        visited.add(current)
        for child in dependents.get(current, set()):
            if child not in visited:
                queue.append(child)
    return frozenset(visited)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _public_id(value: str, field_name: str) -> str:
    clean = value.strip()
    if not clean:
        raise ValueError(f"{field_name} must be non-empty")
    _reject_unsafe_public_text(clean, field_name)
    if not _PUBLIC_ID_RE.fullmatch(clean):
        raise ValueError(f"{field_name} must be a digest-safe public id (got {clean!r})")
    return clean


def _reject_unsafe_public_text(value: str, field_name: str) -> None:
    if _PRIVATE_PATH_RE.search(value):
        raise ValueError(f"{field_name} must not contain private paths")
    if _SECRET_TEXT_RE.search(value):
        raise ValueError(f"{field_name} must not contain secret-shaped text")
    if _UNSAFE_TEXT_RE.search(value):
        raise ValueError(f"{field_name} must not contain unsafe text patterns")


def _digest_for(payload: object) -> str:
    material = json.dumps(
        _jsonable(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return f"sha256:{sha256(material.encode('utf-8')).hexdigest()}"


def _jsonable(value: object) -> object:
    from pydantic import BaseModel
    if isinstance(value, BaseModel):
        return value.model_dump(by_alias=True, mode="python", warnings=False)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, tuple | list):
        return [_jsonable(i) for i in value]
    return value


def _ledger_digest_payload(
    *,
    ledger_id: str,
    objective_digest: str,
    facts: tuple[LedgerFact, ...],
    plan: tuple[LedgerPlanStep, ...],
    acceptance_criteria_ref: str | None,
) -> dict[str, object]:
    return {
        "ledgerId": ledger_id,
        "objectiveDigest": objective_digest,
        "facts": [f.public_projection() for f in facts],
        "plan": [s.public_projection() for s in plan],
        "acceptanceCriteriaRef": acceptance_criteria_ref,
        "defaultOff": True,
    }


def _compute_ledger_digest(ledger: TaskLedgerContract) -> str:
    stub = _ledger_digest_payload(
        ledger_id=ledger.ledger_id,
        objective_digest=ledger.objective_digest,
        facts=ledger.facts,
        plan=ledger.plan,
        acceptance_criteria_ref=ledger.acceptance_criteria_ref,
    )
    return _digest_for(stub)


__all__ = [
    "LedgerFactKind",
    "LedgerFact",
    "LedgerPlanStep",
    "LedgerWorkerRole",
    "TaskLedgerContract",
    "kind_from_support_verdict",
    "make_fact",
    "make_plan_step",
    "make_task_ledger",
    "update_task_ledger",
    "transitively_invalidated_fact_ids",
    "value_digest_for",
]
