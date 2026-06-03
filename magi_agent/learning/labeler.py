"""Signal labeling + candidate pipeline — PR3.

Turns the deterministic ``Signal`` objects from ``signals.py`` into
``LearningCandidate`` objects, via a pluggable ``Labeler``.  Everything here is
deterministic and dependency-free; the real LLM-backed labeler is deferred to
PR7 (see the ``Labeler`` Protocol seam below).  **No store writes**, **no LLM**,
**no network**.

Pipeline stages (all deterministic):

1. ``extract_signals`` (in ``signals.py``) per trace.
2. ``filter_noise`` — drop whitespace-only diffs and other one-off / pure
   formatting signals so they never become candidates.
3. ``Labeler.label`` — each surviving signal becomes a ``LabeledLearning``
   (or ``None`` to drop).  ``LocalFakeLabeler`` is the deterministic default.
4. ``chronological_split`` — order traces by ``ts``; an earlier portion is the
   train split (→ rule/example candidates) and a held-out later portion is the
   eval split (→ eval candidates).  This prevents train/test leakage so eval
   candidates measure generalization, not memorization.
5. ``dedup_candidates`` — collapse near-duplicate candidates using a simple,
   dependency-free normalized-token Jaccard similarity over lesson/content
   text.  Structured so a vector backend (cf. ``vector.py``) can swap in later.
6. ``aggregate_candidates`` — when the same pattern recurs across ``>= N``
   traces (``threshold``, default 3), promote it to a ``rule`` candidate.  This
   is why reflection is a *batch* pass.  Below threshold stays example/eval.

``build_candidates`` ties signal→label→candidate together for a tuple of traces.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from magi_agent.learning.candidates import LearningCandidate, SessionTrace
from magi_agent.learning.models import LearningKind, LearningScope, Provenance
from magi_agent.learning.signals import Signal, SignalKind, extract_signals


# ---------------------------------------------------------------------------
# Labeled learning model
# ---------------------------------------------------------------------------


LabelType = Literal["fact", "citation", "style", "strategy"]


class LabeledLearning(BaseModel):
    """A labeled lesson derived from a single signal.

    ``candidate_kind`` aligns with ``LearningKind`` ("rule"/"example"/"eval")
    from ``models.py``.  ``content`` carries the kind-appropriate shape that a
    promoted ``LearningItem`` will require (e.g. ``situation``/``behavior`` for
    an example).
    """

    model_config = ConfigDict(
        frozen=True,
        populate_by_name=True,
        extra="forbid",
        validate_default=True,
    )

    type: LabelType
    lesson: str
    candidate_kind: LearningKind = Field(alias="candidateKind")
    content: Mapping[str, Any]


# ---------------------------------------------------------------------------
# Labeler protocol + deterministic default
# ---------------------------------------------------------------------------


@runtime_checkable
class Labeler(Protocol):
    """Turns a ``Signal`` (+ originating trace) into a ``LabeledLearning``.

    Returning ``None`` drops the signal.  The REAL, LLM-backed implementation is
    deferred to PR7; it will implement THIS protocol and be injected in place of
    ``LocalFakeLabeler`` — no other pipeline code changes.
    """

    def label(self, signal: Signal, trace: SessionTrace) -> "LabeledLearning | None":
        ...  # pragma: no cover


#: Deterministic mapping from signal kind → (label type, candidate kind).
_SIGNAL_LABEL_MAP: dict[SignalKind, tuple[LabelType, LearningKind]] = {
    "diff": ("style", "example"),
    "redirect": ("strategy", "example"),
    "retry": ("strategy", "example"),
    "acceptance": ("fact", "example"),
}


class LocalFakeLabeler:
    """Deterministic, rule-based labeler — NO LLM, NO network (PR7 replaces).

    Maps each signal kind to a fixed label type + an ``example`` candidate kind,
    and builds a stable lesson string + ``situation``/``behavior`` content from
    the signal's structured evidence.  Given the same signal it always returns
    the same ``LabeledLearning``.

    The LLM-backed labeler (``LlmBackedLabeler`` in ``learning/live.py``) shipped
    in PR7 and is selected by the gated live layer; this deterministic fake
    remains the default for the OSS / test path.
    """

    def label(self, signal: Signal, trace: SessionTrace) -> LabeledLearning | None:
        mapping = _SIGNAL_LABEL_MAP.get(signal.kind)
        if mapping is None:  # pragma: no cover - all kinds mapped
            return None
        label_type, candidate_kind = mapping
        lesson = f"[{signal.kind}] {signal.summary}"
        return LabeledLearning(
            type=label_type,
            lesson=lesson,
            candidateKind=candidate_kind,
            content={
                "situation": signal.summary,
                "behavior": lesson,
            },
        )


# ---------------------------------------------------------------------------
# Noise filter
# ---------------------------------------------------------------------------


def _normalize_ws(text: str) -> str:
    """Collapse all whitespace runs to single spaces and strip ends."""
    return " ".join(text.split())


def filter_noise(
    signals: tuple[Signal, ...] | list[Signal], trace: SessionTrace
) -> tuple[Signal, ...]:
    """Drop one-off / pure-formatting signals.

    Currently: a ``diff`` signal whose draft and final are identical after
    whitespace normalization is pure formatting noise and is dropped.  Other
    signal kinds pass through unchanged.
    """
    kept: list[Signal] = []
    for sig in signals:
        if sig.kind == "diff":
            draft = trace.draft_output or ""
            if _normalize_ws(draft) == _normalize_ws(trace.final_output):
                continue  # whitespace-only diff → noise
        kept.append(sig)
    return tuple(kept)


# ---------------------------------------------------------------------------
# Chronological split (holdout / no-leakage)
# ---------------------------------------------------------------------------

#: Fraction of traces (by chronological order) reserved as the eval holdout.
_HOLDOUT_FRACTION: float = 0.25


def chronological_split(
    traces: tuple[SessionTrace, ...],
    *,
    holdout_fraction: float = _HOLDOUT_FRACTION,
) -> tuple[tuple[SessionTrace, ...], tuple[SessionTrace, ...]]:
    """Order *traces* by ``ts`` and split into (train, eval-holdout).

    The earliest ``(1 - holdout_fraction)`` of traces form the train split; the
    latest ``holdout_fraction`` form the held-out eval split.  The split point
    is deterministic (floor) and the two splits are disjoint and cover all
    traces — so an eval candidate is never derived from the same trace that
    seeded a train candidate (no leakage).  With a single trace, it goes to
    train and the holdout is empty.
    """
    ordered = tuple(sorted(traces, key=lambda t: t.ts))
    n = len(ordered)
    if n <= 1:
        return ordered, ()
    n_holdout = int(n * holdout_fraction)
    if n_holdout < 1:
        n_holdout = 1  # always reserve at least one trace as holdout when n>1
    split = n - n_holdout
    return ordered[:split], ordered[split:]


# ---------------------------------------------------------------------------
# Candidate construction
# ---------------------------------------------------------------------------


def _content_for_kind(
    kind: LearningKind, label: LabeledLearning
) -> dict[str, Any]:
    """Produce kind-appropriate content so a promoted LearningItem validates."""
    base = dict(label.content)
    if kind == "rule":
        return {"when": base.get("situation", label.lesson), "then": label.lesson}
    if kind == "eval":
        return {"input": base.get("situation", label.lesson), "expected": label.lesson}
    # example
    return {
        "situation": base.get("situation", label.lesson),
        "behavior": base.get("behavior", label.lesson),
    }


def _candidate_from_label(
    *,
    trace: SessionTrace,
    signal: Signal,
    label: LabeledLearning,
    kind: LearningKind,
) -> LearningCandidate:
    content = _content_for_kind(kind, label)
    return LearningCandidate(
        kind=kind,
        # TODO(PR5): derive task_kind from trace/signal for scope-based retrieval routing
        scope=LearningScope(taskKind="general", tags=(label.type,)),
        content=content,
        rationale=label.lesson,
        provenance=Provenance(
            sessionIds=(trace.session_id,),
            derivedBy="reflection",
            createdAt=trace.ts,
        ),
        sourceSignalRef=f"signal:{signal.kind}@{trace.session_id}",
    )


def build_candidates(
    traces: tuple[SessionTrace, ...],
    *,
    labeler: Labeler,
    as_eval: bool = False,
) -> tuple[LearningCandidate, ...]:
    """Signals → noise filter → labels → candidates, for a tuple of traces.

    When ``as_eval`` is True every produced candidate is forced to kind
    ``"eval"`` (used for the chronological holdout split).  Otherwise the
    candidate uses the label's own ``candidate_kind``.

    Thin wrapper over :func:`build_candidates_with_signal_count` that discards
    the signal count for callers that only need the candidates.
    """
    candidates, _signal_count = build_candidates_with_signal_count(
        traces, labeler=labeler, as_eval=as_eval
    )
    return candidates


def build_candidates_with_signal_count(
    traces: tuple[SessionTrace, ...],
    *,
    labeler: Labeler,
    as_eval: bool = False,
) -> tuple[tuple[LearningCandidate, ...], int]:
    """Like :func:`build_candidates`, but also return the total signal count.

    Signals are extracted exactly ONCE per trace here; the returned count is the
    number of extracted (pre-noise-filter) signals summed over all traces, so
    callers can report ``signals_extracted`` without a second extraction pass.
    Behavior is otherwise identical and deterministic.
    """
    out: list[LearningCandidate] = []
    total_signals = 0
    for trace in traces:
        extracted = extract_signals(trace)
        total_signals += len(extracted)
        signals = filter_noise(extracted, trace)
        for signal in signals:
            label = labeler.label(signal, trace)
            if label is None:
                continue
            kind: LearningKind = "eval" if as_eval else label.candidate_kind
            out.append(
                _candidate_from_label(
                    trace=trace, signal=signal, label=label, kind=kind
                )
            )
    return tuple(out), total_signals


# ---------------------------------------------------------------------------
# Dedup (normalized-token Jaccard; vector-backend-swappable)
# ---------------------------------------------------------------------------

#: Two candidates with Jaccard similarity >= this are treated as duplicates.
#: Chosen for the deterministic fake-labeler output; reassess at PR7 when the
#: LLM-authored rationale introduces natural wording variation.
_DEDUP_THRESHOLD: float = 0.85


def _candidate_text(candidate: LearningCandidate) -> str:
    """Stable text fingerprint of a candidate for similarity comparison."""
    parts = [candidate.rationale]
    for key in sorted(candidate.content):
        parts.append(f"{key}={candidate.content[key]}")
    return " ".join(parts)


def _tokens(text: str) -> frozenset[str]:
    return frozenset(_normalize_ws(text).lower().split())


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    # both-empty (union == 0) is already handled above, so union > 0 here.
    return inter / union


def dedup_candidates(
    candidates: tuple[LearningCandidate, ...],
    *,
    threshold: float = _DEDUP_THRESHOLD,
) -> tuple[LearningCandidate, ...]:
    """Collapse near-duplicate candidates, deterministically.

    First-seen-wins: iterate in input order, keep a candidate only if it is not
    near-duplicate (token Jaccard ``>= threshold`` AND same ``kind``) of an
    already-kept candidate.  Stable: same input → same output.

    The similarity function is intentionally isolated so a vector backend
    (``vector.py``) can later replace ``_jaccard`` without touching callers.
    """
    kept: list[LearningCandidate] = []
    kept_tokens: list[frozenset[str]] = []
    for cand in candidates:
        toks = _tokens(_candidate_text(cand))
        is_dup = False
        for prev, prev_toks in zip(kept, kept_tokens):
            if prev.kind == cand.kind and _jaccard(toks, prev_toks) >= threshold:
                is_dup = True
                break
        if not is_dup:
            kept.append(cand)
            kept_tokens.append(toks)
    return tuple(kept)


# ---------------------------------------------------------------------------
# Cross-session aggregation
# ---------------------------------------------------------------------------

#: Default number of distinct sessions a pattern must recur in to become a rule.
_DEFAULT_AGGREGATION_THRESHOLD: int = 3


def _pattern_key(candidate: LearningCandidate) -> str:
    """Key identifying the recurring pattern (label tags + normalized rationale)."""
    tags_sig = ",".join(sorted(candidate.scope.tags))
    return f"{tags_sig}::{_normalize_ws(candidate.rationale).lower()}"


def aggregate_candidates(
    candidates: tuple[LearningCandidate, ...],
    *,
    threshold: int = _DEFAULT_AGGREGATION_THRESHOLD,
) -> tuple[LearningCandidate, ...]:
    """Promote patterns recurring across ``>= threshold`` sessions to ``rule``.

    Groups candidates by ``_pattern_key`` (label tag + normalized rationale).
    For a group spanning ``>= threshold`` *distinct sessions*, emit a single
    ``rule`` candidate whose provenance aggregates all contributing session ids
    (this is why reflection is a batch pass).  Groups below threshold pass
    through unchanged (example/eval candidates).  Deterministic ordering: groups
    are processed in first-appearance order of their key.
    """
    # group preserving first-appearance order
    order: list[str] = []
    groups: dict[str, list[LearningCandidate]] = {}
    for cand in candidates:
        key = _pattern_key(cand)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(cand)

    out: list[LearningCandidate] = []
    for key in order:
        group = groups[key]
        session_ids: list[str] = []
        for c in group:
            for sid in c.provenance.session_ids:
                if sid not in session_ids:
                    session_ids.append(sid)
        if len(session_ids) >= threshold:
            out.append(_promote_to_rule(group, tuple(session_ids)))
        else:
            out.extend(group)
    return tuple(out)


def _promote_to_rule(
    group: list[LearningCandidate], session_ids: tuple[str, ...]
) -> LearningCandidate:
    """Build a single ``rule`` candidate from a recurring candidate group."""
    seed = group[0]
    situation = seed.content.get("situation") or seed.content.get("when") or seed.rationale
    return LearningCandidate(
        kind="rule",
        scope=seed.scope,
        content={"when": situation, "then": seed.rationale},
        rationale=(
            f"Recurring pattern across {len(session_ids)} sessions: {seed.rationale}"
        ),
        provenance=Provenance(
            sessionIds=session_ids,
            derivedBy="reflection",
            createdAt=seed.provenance.created_at,
        ),
        sourceSignalRef=f"aggregate:{len(session_ids)}:{seed.source_signal_ref}",
    )


__all__ = [
    "LabelType",
    "LabeledLearning",
    "Labeler",
    "LocalFakeLabeler",
    "aggregate_candidates",
    "build_candidates",
    "build_candidates_with_signal_count",
    "chronological_split",
    "dedup_candidates",
    "filter_noise",
]
