"""Learning reflection executor — PR2 skeleton.

Architecture:
    TranscriptSource ──read_since(watermark)──> SessionTrace tuple
            ▼
      harness/learning_executor  ── env gate ──> disabled no-op
            ├─ LocalFakeTranscriptSource (PR2 only; real source deferred to PR7)
            ├─ deterministic local-fake candidate mapping (no LLM in PR2)
            └─ LearningReflectionResult{status, candidates, watermark, counters}

Env gate: ``MAGI_LEARNING_REFLECTION_ENABLED`` (default OFF).
When off the executor returns ``status="disabled"`` with empty candidates and
**zero work** — no transcript read, no dispatch.

PR3 will replace the trivial deterministic mapping with real signal extraction
and labeling.  PR7 will replace ``LocalFakeTranscriptSource`` with the real
transcript source (reading ``runtime/transcript.py`` / ``commit_boundary``).

No ``Literal[False]`` authority flags are flipped here.
No store writes.  No LLM calls.

Governed by: ``recipe:self-improvement.proposal@1`` (proposalOnly, governed,
requiredPolicyRefs = eval-observation-required + no-direct-mutation).
"""
from __future__ import annotations

import hashlib
import os
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, field_serializer

from magi_agent.learning.candidates import (
    LearningCandidate,
    LocalFakeTranscriptSource,
    SessionTrace,
    TranscriptSource,
)
from magi_agent.learning.models import LearningScope, Provenance


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Env variable that enables reflection (default OFF).
_REFLECTION_ENV_VAR: str = "MAGI_LEARNING_REFLECTION_ENABLED"

_TRUE_STRINGS = frozenset({"1", "true", "yes", "on"})

#: Candidate kind emitted by the local-fake stub.  PR3 will generalise this.
_FAKE_CANDIDATE_KIND: Literal["example"] = "example"


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
       maps each trace to zero-or-one deterministic local-fake candidate stub
       (PR3 replaces this with real signal extraction), and returns
       ``status="ok"`` with the candidates tuple and an advanced watermark.

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

    # --- Step 3: deterministic local-fake candidate mapping (no LLM) ---
    # PR3 REPLACES this trivial stub with real signal extraction + labeling.
    # The skeleton maps each trace to exactly one trivial candidate so the
    # pipeline SHAPE is exercised end-to-end in PR2 tests.
    candidates: list[LearningCandidate] = []
    for trace in traces:
        candidate = _local_fake_candidate_from_trace(trace)
        if candidate is not None:
            candidates.append(candidate)

    # --- Step 4: advance watermark ---
    new_watermark: str | None = _max_ts(traces) if traces else since

    return LearningReflectionResult(
        status="ok",
        candidates=tuple(candidates),
        watermark=new_watermark,
        counters={
            "traces_read": traces_read,
            "candidates_produced": len(candidates),
        },
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _local_fake_candidate_from_trace(
    trace: SessionTrace,
) -> LearningCandidate | None:
    """Map a single trace to a deterministic local-fake candidate stub.

    The stub always produces an ``example`` candidate so the pipeline shape
    is exercised.  PR3 replaces this with real signal extraction.

    Returns ``None`` only for empty traces (zero turns and empty output),
    ensuring the mapping is deterministic and never raises.
    """
    # Trivial guard: skip completely empty traces
    if not trace.turns and not trace.final_output:
        return None

    # Deterministic content derived from the trace — no randomness, no LLM.
    # PR3 will replace this with real extraction logic.
    session_hash = hashlib.sha1(
        trace.session_id.encode("utf-8")
    ).hexdigest()[:12]

    return LearningCandidate(
        kind="example",
        scope=LearningScope(taskKind="general"),
        content={
            "situation": f"Session {trace.session_id} produced output",
            "behavior": trace.final_output[:120] or "(empty)",
        },
        rationale=(
            f"Local-fake stub candidate derived from session {trace.session_id}. "
            "PR3 replaces this with real signal extraction."
        ),
        provenance=Provenance(
            sessionIds=(trace.session_id,),
            derivedBy="reflection",
            createdAt=trace.ts,
        ),
        sourceSignalRef=f"trace:{session_hash}@{trace.ts}",
    )


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
