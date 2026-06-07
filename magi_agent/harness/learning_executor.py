"""Learning reflection executor — PR3 (real signal extraction + labeling).

Architecture:
    TranscriptSource ──read_since(watermark)──> SessionTrace tuple
            ▼
      harness/learning_executor  ── env gate ──> disabled no-op
            ├─ LocalFakeTranscriptSource (real source deferred to PR7)
            ├─ extract_signals  (deterministic, structural — no LLM)
            ├─ chronological_split → train / eval-holdout (no leakage)
            ├─ LocalFakeLabeler  (deterministic — PR7 swaps in LLM-backed)
            ├─ filter_noise / aggregate (train only) / dedup
            └─ LearningReflectionResult{status, candidates, watermark, counters}

Env gate: ``MAGI_LEARNING_REFLECTION_ENABLED`` (default OFF).
When off the executor returns ``status="disabled"`` with empty candidates and
**zero work** — no transcript read, no dispatch.  The OFF path is byte-identical
to PR2.

PR3 replaces the PR2 trivial candidate stub with real signal extraction +
labeling.  Candidates ONLY — no store writes, no policy activation.  PR7 will
replace ``LocalFakeTranscriptSource`` with the real transcript source (reading
``runtime/transcript.py`` / ``commit_boundary``) and ``LocalFakeLabeler`` with
an LLM-backed ``Labeler``.

No ``Literal[False]`` authority flags are flipped here.
No store writes.  No LLM calls.

Governed by: ``recipe:self-improvement.proposal@1`` (proposalOnly, governed,
requiredPolicyRefs = eval-observation-required + no-direct-mutation).
"""
from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, field_serializer

from magi_agent.learning.config import resolve_learning_config
from magi_agent.learning.candidates import (
    LearningCandidate,
    LocalFakeTranscriptSource,
    SessionTrace,
    TranscriptSource,
)
from magi_agent.learning.labeler import Labeler
from magi_agent.learning.eval_gate import (
    MIN_EVAL_SAMPLE_SIZE,
    CheckSet,
    EvalGateConfig,
    EvalGateDecision,
    StaticCheckSet,
    run_eval_gate,
)
from magi_agent.learning.store import LearningStore
from magi_agent.learning.labeler import (
    LocalFakeLabeler,
    aggregate_candidates,
    build_candidates_with_signal_count,
    chronological_split,
    dedup_candidates,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Env variable that enables reflection (default OFF).
_REFLECTION_ENV_VAR: str = "MAGI_LEARNING_REFLECTION_ENABLED"

_TRUE_STRINGS = frozenset({"1", "true", "yes", "on"})

#: Default number of distinct sessions a pattern must recur in to become a rule.
_AGGREGATION_THRESHOLD: int = 3

#: Default deterministic checkset used by the eval gate when a store is injected
#: but no explicit checkset is supplied.  Neutral (no regression) over the
#: minimum sample size so the gate's sample-size guard is satisfied; the real
#: agent-driven evaluator is injected by callers (and lands in PR7).
_DEFAULT_GATE_CHECKSET: StaticCheckSet = StaticCheckSet(
    before=(1.0,) * MIN_EVAL_SAMPLE_SIZE,
    after=(1.0,) * MIN_EVAL_SAMPLE_SIZE,
)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def _reflection_enabled() -> bool:
    """Return the effective reflection gate under PR9a layered opt-out.

    The safe tier is now **default-ON**: with no ``MAGI_LEARNING_*`` env vars
    set this resolves to ``True``.  Resolution flows through
    :func:`resolve_learning_config`, so:

    * ``MAGI_LEARNING_ENABLED`` falsy (master off) ⇒ ``False`` (whole layer
      inert — byte-identical to the PR1–PR8 all-OFF state).
    * ``MAGI_LEARNING_REFLECTION_ENABLED`` explicitly set ⇒ overrides (can force
      off even with the safe tier otherwise on).
    * neither set ⇒ ``True`` (opt-out default).

    No ``Literal[False]`` authority flag is consulted or flipped here.
    """
    return resolve_learning_config().reflection_effective


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class LearningReflectionConfig(BaseModel):
    """Minimal configuration for the PR2 reflection executor skeleton.

    Authority flags (``llm_attached``, ``production_write_enabled``,
    ``real_transcript_source_attached``) are locked to ``Literal[False]``
    and validated to stay False regardless of supplied values.  Promotion to
    True is deferred to PR7.
    """

    model_config = ConfigDict(
        frozen=True,
        populate_by_name=True,
        extra="forbid",
        validate_default=True,
    )

    enabled: bool = False
    local_fake_enabled: bool = Field(default=True, alias="localFakeEnabled")

    #: PR2: no LLM attached — always False
    llm_attached: Literal[False] = Field(
        default=False,
        alias="llmAttached",
    )
    #: PR2: no production writes — always False
    production_write_enabled: Literal[False] = Field(
        default=False,
        alias="productionWriteEnabled",
    )
    #: PR2: real transcript source deferred to PR7 — always False
    real_transcript_source_attached: Literal[False] = Field(
        default=False,
        alias="realTranscriptSourceAttached",
    )

    @field_validator("llm_attached", mode="before")
    @classmethod
    def _force_llm_attached_false(cls, _value: object) -> bool:
        return False

    @field_validator("production_write_enabled", mode="before")
    @classmethod
    def _force_production_write_false(cls, _value: object) -> bool:
        return False

    @field_validator("real_transcript_source_attached", mode="before")
    @classmethod
    def _force_real_transcript_false(cls, _value: object) -> bool:
        return False

    @field_serializer("llm_attached", "production_write_enabled", "real_transcript_source_attached")
    def _serialize_false(self, _value: object) -> bool:
        return False


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


LearningReflectionStatus = Literal["disabled", "ok", "error"]


class LearningReflectionResult(BaseModel):
    """Return value from ``run_reflection()``."""

    model_config = ConfigDict(
        frozen=True,
        populate_by_name=True,
        extra="forbid",
        validate_default=True,
    )

    status: LearningReflectionStatus
    candidates: tuple[LearningCandidate, ...]
    #: Watermark to persist for the next incremental run.  ``None`` when
    #: the executor is disabled or no traces were read.
    watermark: str | None
    #: Best-effort ops counters: ``traces_read``, ``candidates_produced``.
    counters: dict[str, int]

    #: Eval-gate decisions, one per candidate, when a store was injected (the
    #: PR4 ON path).  ``None`` on the candidates-only / OFF path so that path
    #: stays byte-identical to PR3.  Surfaced for the PR6 dashboard.
    eval_gate_decisions: tuple[EvalGateDecision, ...] | None = Field(
        default=None, alias="evalGateDecisions"
    )

    #: Authority flags — all False in PR2
    llm_attached: Literal[False] = Field(
        default=False,
        alias="llmAttached",
    )
    production_write_enabled: Literal[False] = Field(
        default=False,
        alias="productionWriteEnabled",
    )
    real_transcript_source_attached: Literal[False] = Field(
        default=False,
        alias="realTranscriptSourceAttached",
    )

    @field_validator("llm_attached", mode="before")
    @classmethod
    def _force_llm_attached_false(cls, _value: object) -> bool:
        return False

    @field_validator("production_write_enabled", mode="before")
    @classmethod
    def _force_production_write_false(cls, _value: object) -> bool:
        return False

    @field_validator("real_transcript_source_attached", mode="before")
    @classmethod
    def _force_real_transcript_false(cls, _value: object) -> bool:
        return False

    @field_serializer("llm_attached", "production_write_enabled", "real_transcript_source_attached")
    def _serialize_false(self, _value: object) -> bool:
        return False


# ---------------------------------------------------------------------------
# Main executor
# ---------------------------------------------------------------------------


async def run_reflection(
    *,
    source: TranscriptSource | None = None,
    since: str | None = None,
    config: LearningReflectionConfig | None = None,
    store: LearningStore | None = None,
    checkset: CheckSet | None = None,
    eval_gate_config: EvalGateConfig | None = None,
    labeler: Labeler | None = None,
    tenant_id: str = "local",
    auto_activate_examples: bool = True,
) -> LearningReflectionResult:
    """Run a reflection pass over session transcripts.

    Gate logic:
    1. If ``MAGI_LEARNING_REFLECTION_ENABLED`` is falsy, returns
       ``status="disabled"`` immediately — zero work, no transcript read.
    2. Otherwise reads traces via *source* (filtered by *since* watermark),
       runs the deterministic signal-extraction + labeling pipeline
       (extract → split → label → noise-filter → dedup → aggregate), and
       returns ``status="ok"`` with the candidates tuple and advanced watermark.
       Candidates ONLY — no store writes.

    Args:
        source: Transcript source to read from.  Defaults to an empty
            ``LocalFakeTranscriptSource`` when ``None``.
        since: ISO-8601 watermark string.  Only traces with ``ts > since``
            are processed.  ``None`` reads all available traces.
        config: Executor configuration.  Defaults to
            ``LearningReflectionConfig()`` when ``None``.
        store: Optional injected learning store.  When ``None`` (default) the
            executor is candidates-only with ZERO store writes — byte-identical
            to PR3 / the OFF path.  When a store is injected AND the executor is
            gated ON, candidates are run through the PR4 eval gate
            (``eval_gate.run_eval_gate``), which proposes them and policy-gates
            activation.  Writing proposed candidates to the injected (local)
            store is intended local behavior; the ``production_write_enabled`` /
            ``llm_attached`` authority flags stay frozen (live mutation is PR7).
        checkset: Injected deterministic checkset used by the eval gate.  Only
            consulted when *store* is provided.
        eval_gate_config: Optional eval-gate thresholds.  Only consulted when
            *store* is provided.
        labeler: Optional injected ``Labeler``.  When ``None`` (default) the
            deterministic ``LocalFakeLabeler`` is used — byte-identical to
            PR1–PR6.  PR7's gated live layer injects the real
            ``LlmBackedLabeler`` here (behind ``MAGI_LEARNING_LIVE_ENABLED`` +
            readiness); the frozen authority flags stay ``Literal[False]``.
        tenant_id: Tenant the proposed/activated items are written under.
            Threaded into ``run_eval_gate`` so a non-``"local"`` tenant's
            reflection run writes inside its own tenant.  Defaults to ``"local"``
            so the single-tenant path stays byte-identical.
        auto_activate_examples: Threaded into ``run_eval_gate``.  When ``True``
            (default — preserves PR4 behaviour) passing examples auto-activate.
            The bootstrap / default-ON reflect tier passes ``False`` so EVERY
            kind stays ``proposed`` for human approval (governance: no
            auto-activation without review).

    Returns:
        ``LearningReflectionResult`` with ``status``, ``candidates``,
        ``watermark``, and ``counters``.
    """
    if config is None:
        # PR9a layered opt-out: when no explicit config is supplied, the default
        # ``enabled`` now follows the resolved safe-tier gate (default-ON) rather
        # than the legacy hard-OFF.  An explicit ``config`` still wins — its
        # ``enabled`` param keeps its original caller-control semantics.
        config = LearningReflectionConfig(enabled=_reflection_enabled())

    # --- Step 1: double gate — env AND config.enabled must both be true ---
    # Either condition being false results in an immediate disabled no-op.
    # ``_reflection_enabled()`` now resolves to ON by default (PR9a) and OFF only
    # when the master switch / reflection gate is explicitly disabled.
    if not _reflection_enabled() or not config.enabled:
        return LearningReflectionResult(
            status="disabled",
            candidates=(),
            watermark=None,
            # Keep the disabled-path counter schema uniform with the ok path so
            # callers never KeyError on, e.g., ``signals_extracted``.
            counters={
                "traces_read": 0,
                "signals_extracted": 0,
                "candidates_produced": 0,
            },
        )

    # --- Step 2: read traces ---
    # When no *source* is injected, the executor falls back to an empty
    # local-fake source.  The real transcript source (``RealTranscriptSource``,
    # PR7) is selected by the gated live layer and passed in via *source*; the
    # ``local_fake_enabled`` config flag no longer branches the default here
    # (both arms were identical), so it is left to the live layer's selection.
    if source is None:
        source = LocalFakeTranscriptSource(traces=())

    traces = await source.read_since(since)
    traces_read = len(traces)

    # --- Step 3: signal extraction + labeling ---
    # extract → chronological split (no leakage) → label → noise-filter →
    # aggregate (train only) → dedup.  The labeler is injected via the *labeler*
    # DI seam: when ``None`` (default / OFF / PR1–PR6 path) it is the
    # deterministic ``LocalFakeLabeler`` — byte-identical behaviour.  PR7's gated
    # live layer (``learning/live.py``) injects the real ``LlmBackedLabeler``
    # here behind the ``MAGI_LEARNING_LIVE_ENABLED`` gate + readiness stage; the
    # frozen authority flags stay ``Literal[False]`` regardless.
    # Candidates ONLY — the store is never touched here.
    if labeler is None:
        labeler = LocalFakeLabeler()
    train_traces, holdout_traces = chronological_split(traces)

    # Signals are extracted exactly once (inside build_candidates) per split;
    # sum the two split counts for the reported ``signals_extracted``.
    train_candidates, train_signals = build_candidates_with_signal_count(
        train_traces, labeler=labeler
    )
    eval_candidates, eval_signals = build_candidates_with_signal_count(
        holdout_traces, labeler=labeler, as_eval=True
    )
    signals_extracted = train_signals + eval_signals

    # Aggregate TRAIN candidates first so recurring per-session copies merge
    # (provenance/diversity) before dedup collapses them — otherwise dedup
    # would erase the recurrence and aggregation could never reach threshold.
    # Eval (holdout) candidates are kept OUT of aggregation so they never
    # contribute to rule promotion (train/eval isolation).
    aggregated_train = aggregate_candidates(
        train_candidates, threshold=_AGGREGATION_THRESHOLD
    )
    candidates = dedup_candidates(aggregated_train + eval_candidates)

    # --- Step 3b: optional eval gate (DI-gated; OFF path never reaches here) ---
    # When a store is injected, run candidates through the PR4 eval gate so
    # they are proposed and (for passing examples) policy-gated activated in the
    # *local* store.  When ``store is None`` this block is skipped entirely and
    # the executor stays candidates-only — byte-identical to PR3.
    gate_decisions: tuple[EvalGateDecision, ...] | None = None
    if store is not None:
        gate_checkset = checkset if checkset is not None else _DEFAULT_GATE_CHECKSET
        gate_decisions = run_eval_gate(
            candidates,
            store=store,
            checkset=gate_checkset,
            config=eval_gate_config,
            tenant_id=tenant_id,
            auto_activate_examples=auto_activate_examples,
        )

    # --- Step 4: advance watermark ---
    new_watermark: str | None = _max_ts(traces) if traces else since

    counters = {
        "traces_read": traces_read,
        "signals_extracted": signals_extracted,
        "candidates_produced": len(candidates),
    }
    # Learning-layer counters are added ONLY on the ON+store path, so the
    # candidates-only / OFF path stays byte-identical to PR3.
    if gate_decisions is not None:
        counters["items_activated"] = sum(1 for d in gate_decisions if d.activated)
        counters["items_proposed"] = sum(1 for d in gate_decisions if not d.activated)

    return LearningReflectionResult(
        status="ok",
        candidates=candidates,
        watermark=new_watermark,
        counters=counters,
        eval_gate_decisions=gate_decisions,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _max_ts(traces: tuple[SessionTrace, ...]) -> str | None:
    """Return the lexicographically largest ``ts`` in *traces*, or ``None``."""
    if not traces:
        return None
    return max(t.ts for t in traces)


__all__ = [
    "LearningReflectionConfig",
    "LearningReflectionResult",
    "LearningReflectionStatus",
    "run_reflection",
]
