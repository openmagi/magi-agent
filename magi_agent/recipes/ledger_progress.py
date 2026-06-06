"""Progress ledger contracts — stall detection & per-step self-assessment.

Default-OFF.  All classes carry ``default_off: Literal[True] = True``.
No ADK runner, provider call, browser, or live execution is attached.

This module is built in two stages:
  Phase 0b — :class:`StallVerdict` + :func:`detect_stall` (shipped first).
  Phase 2   — :class:`ProgressLedgerEntry`, :class:`ProgressLedgerContract`,
               :func:`update_progress_ledger`, :func:`derive_step_verdict`.
"""
from __future__ import annotations

import json
from enum import Enum
from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="never",
    hide_input_in_errors=True,
)
_DIGEST_RE_STR = r"^sha256:[a-f0-9]{64}$"


# ---------------------------------------------------------------------------
# Stall verdict — Phase 0b
# ---------------------------------------------------------------------------

class StallKind(str, Enum):
    """Enumeration of all reasons a stall verdict may fire."""

    ok = "ok"
    stall_threshold_exceeded = "stall_threshold_exceeded"
    step_budget_exhausted = "step_budget_exhausted"
    token_budget_exhausted = "token_budget_exhausted"
    wall_budget_exhausted = "wall_budget_exhausted"
    replan_count_exhausted = "replan_count_exhausted"


class StallVerdict(BaseModel):
    """Deterministic stall-detection result.

    Produced by :func:`detect_stall` on each step boundary.  The orchestrator
    inspects ``kind`` to decide whether to continue, re-plan, or terminate
    gracefully.  A sha256 digest over all fields makes the verdict tamper-
    evident and suitable for inclusion in the progress ledger audit chain.

    ``kind == StallKind.ok`` — budget is healthy; continue execution.
    Any other kind — some limit was crossed; re-plan or terminate.
    """

    model_config = _MODEL_CONFIG

    kind: StallKind
    """Why the stall detector fired (or ``ok`` if it did not)."""

    consecutive_stalled_steps: int = Field(default=0, ge=0)
    """Number of consecutive steps that produced no new facts."""

    stall_threshold: int = Field(default=3, ge=1)
    """The threshold that consecutive_stalled_steps is compared against."""

    total_steps_taken: int = Field(default=0, ge=0)
    """Total orchestration steps completed so far."""

    step_budget: int = Field(default=20, ge=1)
    """The step cap from the budget policy."""

    total_tokens_used: int = Field(default=0, ge=0)
    """Cumulative tokens consumed across all steps."""

    token_budget: int = Field(default=400_000, ge=1)
    """The token cap from the budget policy."""

    total_wall_ms: int = Field(default=0, ge=0)
    """Cumulative wall-clock time (ms) consumed across all steps."""

    wall_budget_ms: int = Field(default=240_000, ge=1)
    """The wall-clock cap from the budget policy (replaces SIGALRM)."""

    replan_count: int = Field(default=0, ge=0)
    """How many re-plans have been executed so far."""

    max_replan_count: int = Field(default=2, ge=0)
    """The maximum allowed re-plans from the budget policy."""

    default_off: Literal[True] = Field(default=True)
    """Authority flag — this capability is default-OFF."""

    @model_validator(mode="after")
    def _validate_kind_consistency(self) -> StallVerdict:
        """Verify that the reported kind is consistent with the numeric state."""
        if self.kind == StallKind.ok:
            # ok — all budgets must be within limits
            if self.consecutive_stalled_steps >= self.stall_threshold:
                raise ValueError(
                    "kind=ok but consecutive_stalled_steps >= stall_threshold"
                )
            if self.total_steps_taken >= self.step_budget:
                raise ValueError("kind=ok but total_steps_taken >= step_budget")
            if self.total_tokens_used >= self.token_budget:
                raise ValueError("kind=ok but total_tokens_used >= token_budget")
            if self.total_wall_ms >= self.wall_budget_ms:
                raise ValueError("kind=ok but total_wall_ms >= wall_budget_ms")
            if self.replan_count > self.max_replan_count:
                raise ValueError("kind=ok but replan_count > max_replan_count")
        return self

    # ------------------------------------------------------------------
    # Digest
    # ------------------------------------------------------------------

    def verdict_digest(self) -> str:
        """Deterministic sha256 over all verdict fields."""
        payload = {
            "kind": self.kind.value,
            "consecutiveStalledSteps": self.consecutive_stalled_steps,
            "stallThreshold": self.stall_threshold,
            "totalStepsTaken": self.total_steps_taken,
            "stepBudget": self.step_budget,
            "totalTokensUsed": self.total_tokens_used,
            "tokenBudget": self.token_budget,
            "totalWallMs": self.total_wall_ms,
            "wallBudgetMs": self.wall_budget_ms,
            "replanCount": self.replan_count,
            "maxReplanCount": self.max_replan_count,
            "defaultOff": True,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return f"sha256:{sha256(encoded).hexdigest()}"

    def public_projection(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "consecutiveStalledSteps": self.consecutive_stalled_steps,
            "stallThreshold": self.stall_threshold,
            "totalStepsTaken": self.total_steps_taken,
            "stepBudget": self.step_budget,
            "totalTokensUsed": self.total_tokens_used,
            "tokenBudget": self.token_budget,
            "totalWallMs": self.total_wall_ms,
            "wallBudgetMs": self.wall_budget_ms,
            "replanCount": self.replan_count,
            "maxReplanCount": self.max_replan_count,
            "defaultOff": True,
            "verdictDigest": self.verdict_digest(),
        }


def detect_stall(
    *,
    consecutive_stalled_steps: int,
    stall_threshold: int,
    total_steps_taken: int,
    step_budget: int,
    total_tokens_used: int,
    token_budget: int,
    total_wall_ms: int,
    wall_budget_ms: int,
    replan_count: int = 0,
    max_replan_count: int = 2,
) -> StallVerdict:
    """Deterministic stall gate — no LLM call, no heuristic, pure integer comparisons.

    Checks are evaluated in priority order so the most severe condition is
    reported when multiple limits are crossed simultaneously.

    Parameters
    ----------
    consecutive_stalled_steps:
        How many consecutive steps produced no new facts.
    stall_threshold:
        Fire ``stall_threshold_exceeded`` when
        ``consecutive_stalled_steps >= stall_threshold``.
    total_steps_taken:
        Total orchestration steps completed.
    step_budget:
        Maximum allowed steps.
    total_tokens_used:
        Cumulative tokens consumed.
    token_budget:
        Maximum allowed tokens.
    total_wall_ms:
        Cumulative elapsed wall-clock time (ms).
    wall_budget_ms:
        Maximum allowed wall-clock time (ms).  Principled replacement for the
        operator-level ``signal.alarm(300)`` SIGALRM hack.
    replan_count:
        How many re-plans have been executed.
    max_replan_count:
        Maximum allowed re-plans.

    Returns
    -------
    StallVerdict
        ``kind=ok`` when all budgets are healthy; otherwise the first violated
        kind in the priority order defined above.
    """
    common = dict(
        consecutive_stalled_steps=consecutive_stalled_steps,
        stall_threshold=stall_threshold,
        total_steps_taken=total_steps_taken,
        step_budget=step_budget,
        total_tokens_used=total_tokens_used,
        token_budget=token_budget,
        total_wall_ms=total_wall_ms,
        wall_budget_ms=wall_budget_ms,
        replan_count=replan_count,
        max_replan_count=max_replan_count,
    )
    # Priority 1 — wall-clock (most user-visible; replaces SIGALRM)
    if total_wall_ms >= wall_budget_ms:
        return StallVerdict(kind=StallKind.wall_budget_exhausted, **common)  # type: ignore[arg-type]
    # Priority 2 — token budget
    if total_tokens_used >= token_budget:
        return StallVerdict(kind=StallKind.token_budget_exhausted, **common)  # type: ignore[arg-type]
    # Priority 3 — step budget
    if total_steps_taken >= step_budget:
        return StallVerdict(kind=StallKind.step_budget_exhausted, **common)  # type: ignore[arg-type]
    # Priority 4 — replan budget (checked before stall so we can distinguish)
    if replan_count > max_replan_count:
        return StallVerdict(kind=StallKind.replan_count_exhausted, **common)  # type: ignore[arg-type]
    # Priority 5 — consecutive stall
    if consecutive_stalled_steps >= stall_threshold:
        return StallVerdict(kind=StallKind.stall_threshold_exceeded, **common)  # type: ignore[arg-type]
    return StallVerdict(kind=StallKind.ok, **common)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Progress step verdict — Phase 2
# ---------------------------------------------------------------------------

class ProgressStepVerdict(str, Enum):
    """Per-step self-assessment verdict recorded in the progress ledger."""

    advancing = "advancing"
    """New verified_intermediate or known_fact added — genuine forward progress."""

    speculative = "speculative"
    """Only working_guess facts added or updated — no verified progress."""

    stalled = "stalled"
    """No new facts of any kind; same plan step re-attempted."""

    contradicted = "contradicted"
    """A value_digest mismatch was detected — chain may be invalid."""

    budget_ok = "budget_ok"
    """Step completed well within its individual budget."""

    budget_warn = "budget_warn"
    """Step consumed > 75 % of its individual step budget — consider wrapping up."""

    budget_exceeded = "budget_exceeded"
    """Step budget blown; step was terminated early."""


# ---------------------------------------------------------------------------
# Progress ledger entry — Phase 2
# ---------------------------------------------------------------------------

class ProgressLedgerEntry(BaseModel):
    """Immutable record of a single orchestration step's outcome.

    Entries are appended to :class:`ProgressLedgerContract`; the sequence
    forms a tamper-evident audit chain via ``entry_digest``.
    """

    model_config = _MODEL_CONFIG

    entry_id: str = Field(min_length=1, max_length=128)
    """Stable public identifier for this entry (e.g. ``"entry:step-3-attempt-1"``)."""

    step_id: str = Field(min_length=1, max_length=128)
    """Which :class:`~magi_agent.recipes.ledger_task.LedgerPlanStep` this records."""

    step_verdict: ProgressStepVerdict
    """Self-assessment of whether this step made genuine forward progress."""

    facts_added: tuple[str, ...] = Field(default=())
    """fact_ids newly created this step."""

    facts_upgraded: tuple[str, ...] = Field(default=())
    """fact_ids promoted (e.g. working_guess → known_fact)."""

    facts_contradicted: tuple[str, ...] = Field(default=())
    """fact_ids whose value_digest mismatched — chain invalidation pending."""

    evidence_graph_digest: str | None = Field(default=None)
    """sha256 of the ResearchEvidenceGraph snapshot after this step (may be None
    if the step terminated before evidence was collected)."""

    tokens_used: int = Field(default=0, ge=0)
    """Token count consumed by this step."""

    wall_ms: int = Field(default=0, ge=0)
    """Wall-clock time consumed by this step (milliseconds)."""

    entry_digest: str
    """Deterministic sha256 over all fields — any mutation produces a new digest."""

    default_off: Literal[True] = Field(default=True)

    @field_validator("entry_id", "step_id")
    @classmethod
    def _validate_public_ids(cls, value: str) -> str:
        import re
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:\-]{0,127}", value):
            raise ValueError("entry_id and step_id must be digest-safe public refs")
        return value

    @field_validator("evidence_graph_digest")
    @classmethod
    def _validate_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        import re
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", value):
            raise ValueError("evidence_graph_digest must be a sha256 hex digest")
        return value

    @model_validator(mode="after")
    def _validate_entry_digest(self) -> ProgressLedgerEntry:
        expected = _entry_digest_for(self)
        if self.entry_digest != expected:
            raise ValueError("entry_digest must be bound to entry fields")
        return self

    def public_projection(self) -> dict[str, object]:
        return {
            "entryId": self.entry_id,
            "stepId": self.step_id,
            "stepVerdict": self.step_verdict.value,
            "factsAdded": self.facts_added,
            "factsUpgraded": self.facts_upgraded,
            "factsContradicted": self.facts_contradicted,
            "evidenceGraphDigest": self.evidence_graph_digest,
            "tokensUsed": self.tokens_used,
            "wallMs": self.wall_ms,
            "entryDigest": self.entry_digest,
            "defaultOff": True,
        }


def make_progress_ledger_entry(
    *,
    entry_id: str,
    step_id: str,
    step_verdict: ProgressStepVerdict,
    facts_added: tuple[str, ...] = (),
    facts_upgraded: tuple[str, ...] = (),
    facts_contradicted: tuple[str, ...] = (),
    evidence_graph_digest: str | None = None,
    tokens_used: int = 0,
    wall_ms: int = 0,
) -> ProgressLedgerEntry:
    """Factory — computes ``entry_digest`` automatically."""
    stub = {
        "entryId": entry_id,
        "stepId": step_id,
        "stepVerdict": step_verdict.value,
        "factsAdded": facts_added,
        "factsUpgraded": facts_upgraded,
        "factsContradicted": facts_contradicted,
        "evidenceGraphDigest": evidence_graph_digest,
        "tokensUsed": tokens_used,
        "wallMs": wall_ms,
        "defaultOff": True,
    }
    digest = _digest_for(stub)
    return ProgressLedgerEntry(
        entry_id=entry_id,
        step_id=step_id,
        step_verdict=step_verdict,
        facts_added=facts_added,
        facts_upgraded=facts_upgraded,
        facts_contradicted=facts_contradicted,
        evidence_graph_digest=evidence_graph_digest,
        tokens_used=tokens_used,
        wall_ms=wall_ms,
        entry_digest=digest,
    )


# ---------------------------------------------------------------------------
# Progress ledger contract — Phase 2
# ---------------------------------------------------------------------------

class ProgressLedgerContract(BaseModel):
    """Immutable snapshot of the progress ledger at a given point in time.

    The ledger accumulates :class:`ProgressLedgerEntry` objects produced after
    each orchestration step.  All aggregate counters are derived fields: they
    are computed at construction time from ``entries`` and validated against the
    declared values.

    ``progress_digest`` covers all fields — any mutation is detectable.
    """

    model_config = _MODEL_CONFIG

    progress_id: str = Field(min_length=1, max_length=128)
    task_ledger_id: str = Field(min_length=1, max_length=128)
    """Links to the :class:`~magi_agent.recipes.ledger_task.TaskLedgerContract`
    this progress ledger tracks."""

    entries: tuple[ProgressLedgerEntry, ...] = Field(default=())
    """Ordered sequence of step entries — the audit chain."""

    consecutive_stalled_steps: int = Field(ge=0)
    """Derived: how many consecutive entries have verdict == stalled."""

    total_steps_taken: int = Field(ge=0)
    """Total entries count."""

    total_tokens_used: int = Field(ge=0)
    """Cumulative tokens across all steps."""

    total_wall_ms: int = Field(ge=0)
    """Cumulative wall-clock time across all steps (ms)."""

    stall_threshold: int = Field(ge=1)
    """From budget policy — deterministic threshold, not a guess."""

    step_budget: int = Field(ge=1)
    replan_count: int = Field(default=0, ge=0)
    token_budget: int = Field(ge=1)
    wall_budget_ms: int = Field(ge=1)
    max_replan_count: int = Field(default=2, ge=0)

    progress_digest: str
    """Deterministic sha256 over all fields."""

    default_off: Literal[True] = Field(default=True)

    @field_validator("progress_id", "task_ledger_id")
    @classmethod
    def _validate_public_ids(cls, value: str) -> str:
        import re
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:\-]{0,127}", value):
            raise ValueError("progress_id and task_ledger_id must be digest-safe public refs")
        return value

    @field_validator("progress_digest")
    @classmethod
    def _validate_digest_format(cls, value: str) -> str:
        import re
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", value):
            raise ValueError("progress_digest must be a sha256 hex digest")
        return value

    @model_validator(mode="after")
    def _validate_derived_fields(self) -> ProgressLedgerContract:
        # Validate that aggregate counters match entries.
        actual_total = len(self.entries)
        actual_tokens = sum(e.tokens_used for e in self.entries)
        actual_wall = sum(e.wall_ms for e in self.entries)
        if self.total_steps_taken != actual_total:
            raise ValueError(
                f"total_steps_taken ({self.total_steps_taken}) must equal len(entries) ({actual_total})"
            )
        if self.total_tokens_used != actual_tokens:
            raise ValueError(
                f"total_tokens_used ({self.total_tokens_used}) must equal sum of entry tokens ({actual_tokens})"
            )
        if self.total_wall_ms != actual_wall:
            raise ValueError(
                f"total_wall_ms ({self.total_wall_ms}) must equal sum of entry wall_ms ({actual_wall})"
            )
        # Validate consecutive stalled count.
        actual_consecutive = _count_consecutive_stalled(self.entries)
        if self.consecutive_stalled_steps != actual_consecutive:
            raise ValueError(
                f"consecutive_stalled_steps ({self.consecutive_stalled_steps}) "
                f"must equal derived value ({actual_consecutive})"
            )
        # Validate digest.
        expected = _progress_digest_for(self)
        if self.progress_digest != expected:
            raise ValueError("progress_digest must be bound to progress ledger fields")
        return self

    def public_projection(self) -> dict[str, object]:
        return {
            "progressId": self.progress_id,
            "taskLedgerId": self.task_ledger_id,
            "entries": tuple(e.public_projection() for e in self.entries),
            "consecutiveStalledSteps": self.consecutive_stalled_steps,
            "totalStepsTaken": self.total_steps_taken,
            "totalTokensUsed": self.total_tokens_used,
            "totalWallMs": self.total_wall_ms,
            "stallThreshold": self.stall_threshold,
            "stepBudget": self.step_budget,
            "replanCount": self.replan_count,
            "tokenBudget": self.token_budget,
            "wallBudgetMs": self.wall_budget_ms,
            "maxReplanCount": self.max_replan_count,
            "progressDigest": self.progress_digest,
            "defaultOff": True,
        }

    def current_stall_verdict(self) -> StallVerdict:
        """Convenience: run detect_stall with the current ledger state."""
        return detect_stall(
            consecutive_stalled_steps=self.consecutive_stalled_steps,
            stall_threshold=self.stall_threshold,
            total_steps_taken=self.total_steps_taken,
            step_budget=self.step_budget,
            total_tokens_used=self.total_tokens_used,
            token_budget=self.token_budget,
            total_wall_ms=self.total_wall_ms,
            wall_budget_ms=self.wall_budget_ms,
            replan_count=self.replan_count,
            max_replan_count=self.max_replan_count,
        )


def make_progress_ledger(
    *,
    progress_id: str,
    task_ledger_id: str,
    entries: tuple[ProgressLedgerEntry, ...] = (),
    stall_threshold: int,
    step_budget: int,
    token_budget: int,
    wall_budget_ms: int,
    replan_count: int = 0,
    max_replan_count: int = 2,
) -> ProgressLedgerContract:
    """Factory — computes all derived fields and digest automatically."""
    consecutive = _count_consecutive_stalled(entries)
    total_steps = len(entries)
    total_tokens = sum(e.tokens_used for e in entries)
    total_wall = sum(e.wall_ms for e in entries)
    stub: dict[str, object] = {
        "progressId": progress_id,
        "taskLedgerId": task_ledger_id,
        "entries": tuple(e.public_projection() for e in entries),
        "consecutiveStalledSteps": consecutive,
        "totalStepsTaken": total_steps,
        "totalTokensUsed": total_tokens,
        "totalWallMs": total_wall,
        "stallThreshold": stall_threshold,
        "stepBudget": step_budget,
        "replanCount": replan_count,
        "tokenBudget": token_budget,
        "wallBudgetMs": wall_budget_ms,
        "maxReplanCount": max_replan_count,
        "defaultOff": True,
    }
    digest = _digest_for(stub)
    return ProgressLedgerContract(
        progress_id=progress_id,
        task_ledger_id=task_ledger_id,
        entries=entries,
        consecutive_stalled_steps=consecutive,
        total_steps_taken=total_steps,
        total_tokens_used=total_tokens,
        total_wall_ms=total_wall,
        stall_threshold=stall_threshold,
        step_budget=step_budget,
        replan_count=replan_count,
        token_budget=token_budget,
        wall_budget_ms=wall_budget_ms,
        max_replan_count=max_replan_count,
        progress_digest=digest,
    )


def update_progress_ledger(
    progress: ProgressLedgerContract,
    entry: ProgressLedgerEntry,
    *,
    replan_count: int | None = None,
) -> ProgressLedgerContract:
    """Return a new :class:`ProgressLedgerContract` with ``entry`` appended.

    ``replan_count`` can be incremented here when the entry records a re-plan.
    All derived fields are recomputed; the new ``progress_digest`` covers the
    updated state.
    """
    new_entries = (*progress.entries, entry)
    new_replan = replan_count if replan_count is not None else progress.replan_count
    return make_progress_ledger(
        progress_id=progress.progress_id,
        task_ledger_id=progress.task_ledger_id,
        entries=new_entries,  # type: ignore[arg-type]
        stall_threshold=progress.stall_threshold,
        step_budget=progress.step_budget,
        token_budget=progress.token_budget,
        wall_budget_ms=progress.wall_budget_ms,
        replan_count=new_replan,
        max_replan_count=progress.max_replan_count,
    )


def derive_step_verdict(
    facts_added: tuple[str, ...],
    facts_upgraded: tuple[str, ...],
    facts_contradicted: tuple[str, ...],
    *,
    tokens_used: int,
    per_step_token_budget: int,
) -> ProgressStepVerdict:
    """Deterministic step verdict — no LLM call, pure data inspection.

    Parameters
    ----------
    facts_added:
        fact_ids newly created during the step.
    facts_upgraded:
        fact_ids promoted (e.g. working_guess → known_fact).
    facts_contradicted:
        fact_ids whose value digest mismatched.
    tokens_used:
        Tokens consumed by this step.
    per_step_token_budget:
        The per-step token cap from the budget policy.

    Returns
    -------
    ProgressStepVerdict
        The most appropriate verdict given the supplied data.
    """
    if facts_contradicted:
        return ProgressStepVerdict.contradicted
    if tokens_used >= per_step_token_budget:
        return ProgressStepVerdict.budget_exceeded
    if facts_upgraded:
        # Upgraded facts = genuine progress (guess → fact)
        return ProgressStepVerdict.advancing
    if facts_added:
        return ProgressStepVerdict.speculative
    return ProgressStepVerdict.stalled


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _count_consecutive_stalled(entries: tuple[ProgressLedgerEntry, ...]) -> int:
    """Count trailing consecutive stalled entries (from the most recent)."""
    count = 0
    for entry in reversed(entries):
        if entry.step_verdict == ProgressStepVerdict.stalled:
            count += 1
        else:
            break
    return count


def _digest_for(payload: object) -> str:
    material = json.dumps(_jsonable(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
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


def _entry_digest_for(entry: ProgressLedgerEntry) -> str:
    payload = {
        "entryId": entry.entry_id,
        "stepId": entry.step_id,
        "stepVerdict": entry.step_verdict.value,
        "factsAdded": list(entry.facts_added),
        "factsUpgraded": list(entry.facts_upgraded),
        "factsContradicted": list(entry.facts_contradicted),
        "evidenceGraphDigest": entry.evidence_graph_digest,
        "tokensUsed": entry.tokens_used,
        "wallMs": entry.wall_ms,
        "defaultOff": True,
    }
    return _digest_for(payload)


def _progress_digest_for(progress: ProgressLedgerContract) -> str:
    payload = {
        "progressId": progress.progress_id,
        "taskLedgerId": progress.task_ledger_id,
        "entries": [e.public_projection() for e in progress.entries],
        "consecutiveStalledSteps": progress.consecutive_stalled_steps,
        "totalStepsTaken": progress.total_steps_taken,
        "totalTokensUsed": progress.total_tokens_used,
        "totalWallMs": progress.total_wall_ms,
        "stallThreshold": progress.stall_threshold,
        "stepBudget": progress.step_budget,
        "replanCount": progress.replan_count,
        "tokenBudget": progress.token_budget,
        "wallBudgetMs": progress.wall_budget_ms,
        "maxReplanCount": progress.max_replan_count,
        "defaultOff": True,
    }
    return _digest_for(payload)


__all__ = [
    # Phase 0b
    "StallKind",
    "StallVerdict",
    "detect_stall",
    # Phase 2
    "ProgressStepVerdict",
    "ProgressLedgerEntry",
    "ProgressLedgerContract",
    "make_progress_ledger_entry",
    "make_progress_ledger",
    "update_progress_ledger",
    "derive_step_verdict",
]
