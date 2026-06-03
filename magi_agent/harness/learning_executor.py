"""Learning reflection executor — PR3 (real signal extraction + labeling).

Architecture:
    TranscriptSource ──read_since(watermark)──> SessionTrace tuple
            ▼
      harness/learning_executor  ── env gate ──> disabled no-op
            ├─ LocalFakeTranscriptSource (real source deferred to PR7)
            ├─ extract_signals  (deterministic, structural — no LLM)
            ├─ chronological_split → train / eval-holdout (no leakage)
            ├─ LocalFakeLabeler  (deterministic — PR7 swaps in LLM-backed)
            ├─ filter_noise / dedup / cross-session aggregate
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

from magi_agent.learning.candidates import (
    LearningCandidate,
    LocalFakeTranscriptSource,
    SessionTrace,
    TranscriptSource,
)
from magi_agent.learning.labeler import (
    LocalFakeLabeler,
    aggregate_candidates,
    build_candidates,
    chronological_split,
    dedup_candidates,
)
from magi_agent.learning.signals import extract_signals


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Env variable that enables reflection (default OFF).
_REFLECTION_ENV_VAR: str = "MAGI_LEARNING_REFLECTION_ENABLED"

_TRUE_STRINGS = frozenset({"1", "true", "yes", "on"})

#: Default number of distinct sessions a pattern must recur in to become a rule.
_AGGREGATION_THRESHOLD: int = 3


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def _reflection_enabled() -> bool:
    """Return True only when the env gate is explicitly set to a truthy value."""
    return os.environ.get(_REFLECTION_ENV_VAR, "").lower() in _TRUE_STRINGS


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

    Returns:
        ``LearningReflectionResult`` with ``status``, ``candidates``,
        ``watermark``, and ``counters``.
    """
    if config is None:
        config = LearningReflectionConfig()

    # --- Step 1: double gate — env AND config.enabled must both be true ---
    # Either condition being false results in an immediate disabled no-op.
    if not _reflection_enabled() or not config.enabled:
        return LearningReflectionResult(
            status="disabled",
            candidates=(),
            watermark=None,
            counters={"traces_read": 0, "candidates_produced": 0},
        )

    # --- Step 2: read traces (local-fake only in PR2) ---
    # ``local_fake_enabled`` gates whether the default source is the local-fake
    # stub or the real transcript source (deferred to PR7).  Until PR7 lands,
    # the real source is unavailable, so when ``local_fake_enabled`` is False
    # we still fall back to an empty local-fake so the executor remains safe;
    # PR7 will replace this branch with a real source attachment.
    if source is None:
        if config.local_fake_enabled:
            source = LocalFakeTranscriptSource(traces=())
        else:
            # TODO(PR7): attach real transcript source when local_fake_enabled=False.
            source = LocalFakeTranscriptSource(traces=())

    traces = await source.read_since(since)
    traces_read = len(traces)

    # --- Step 3: deterministic signal extraction + labeling (no LLM) ---
    # extract → chronological split (no leakage) → label → noise-filter →
    # dedup → cross-session aggregate.  Labeler is the deterministic
    # ``LocalFakeLabeler``; PR7 swaps in an LLM-backed ``Labeler``.
    # Candidates ONLY — the store is never touched here.
    signals_extracted = sum(len(extract_signals(t)) for t in traces)

    labeler = LocalFakeLabeler()
    train_traces, holdout_traces = chronological_split(traces)

    train_candidates = build_candidates(train_traces, labeler=labeler)
    eval_candidates = build_candidates(
        holdout_traces, labeler=labeler, as_eval=True
    )

    combined = dedup_candidates(train_candidates + eval_candidates)
    candidates = aggregate_candidates(
        combined, threshold=_AGGREGATION_THRESHOLD
    )

    # --- Step 4: advance watermark ---
    new_watermark: str | None = _max_ts(traces) if traces else since

    return LearningReflectionResult(
        status="ok",
        candidates=candidates,
        watermark=new_watermark,
        counters={
            "traces_read": traces_read,
            "signals_extracted": signals_extracted,
            "candidates_produced": len(candidates),
        },
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
