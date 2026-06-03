"""Learning candidate models and transcript source protocol — PR2.

A ``LearningCandidate`` is a *proposed-but-not-yet-stored* learning item
produced by the reflection executor.  It reuses PR1 types (``LearningKind``,
``LearningScope``, ``Provenance``) and is intentionally lighter than
``LearningItem``: no DB id, no stats, no approval / eval-observation refs.

``TranscriptSource`` is a read-only async Protocol sufficient for the
reflection executor.  ``LocalFakeTranscriptSource`` is the injected-fixture
implementation used in local-fake / test runs.  Wiring to the REAL transcript
source (``runtime/transcript.py`` / ``commit_boundary``) is explicitly deferred
to PR7.

Env gate: ``MAGI_LEARNING_REFLECTION_ENABLED`` — see ``harness/learning_executor.py``.

No ``Literal[False]`` authority flags are flipped here.  No store writes.
No LLM calls.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator

from magi_agent.learning.models import LearningKind, LearningScope, Provenance


# ---------------------------------------------------------------------------
# Candidate model
# ---------------------------------------------------------------------------


class LearningCandidate(BaseModel):
    """A proposed-but-not-yet-stored learning candidate (PR2 output unit).

    Produced by the reflection executor; consumed (and stored) in PR3+.
    ``kind``/``scope``/``content``/``rationale``/``provenance`` mirror the
    corresponding fields on ``LearningItem`` so promotion to a stored item is
    a straightforward field transfer.  ``source_signal_ref`` records the
    session trace that originated this candidate (opaque ref string).
    """

    model_config = ConfigDict(
        frozen=True,
        populate_by_name=True,
        extra="forbid",
        validate_default=True,
    )

    kind: LearningKind
    scope: LearningScope
    content: dict[str, Any]
    rationale: str
    provenance: Provenance
    #: Opaque reference to the session trace that originated this candidate.
    source_signal_ref: str = Field(alias="sourceSignalRef")


# ---------------------------------------------------------------------------
# Transcript source Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class TranscriptSource(Protocol):
    """Read-only async source of session transcripts.

    The REAL implementation (reading ``runtime/transcript.py`` /
    ``commit_boundary``) is explicitly deferred to PR7.  Only
    ``LocalFakeTranscriptSource`` is wired here.
    """

    async def read_since(
        self, watermark: str | None
    ) -> tuple["SessionTrace", ...]:
        """Return traces with ``ts > watermark`` (or all traces when ``None``)."""
        ...  # pragma: no cover


# ---------------------------------------------------------------------------
# Session trace shape
# ---------------------------------------------------------------------------


class SessionTrace(BaseModel):
    """Minimal session-trace shape sufficient for PR3 signal extraction.

    PR3 will consume ``turns``, ``final_output``, and ``draft_output`` to
    extract learning signals (including the draft-vs-final diff signal).
    The ``ts`` field is an ISO-8601 timestamp string used for watermark-based
    incremental filtering.

    **Timezone requirement**: ``ts`` MUST be normalized to UTC and end with
    ``"Z"`` (e.g. ``"2026-06-03T10:00:00Z"``).  This is enforced by a
    ``field_validator`` so that lexicographic comparison of ``ts`` values is
    sound (all strings share the same UTC offset).  Producers MUST normalize
    to ``Z`` before constructing a ``SessionTrace``.

    TODO(PR7): replace LocalFakeTranscriptSource with a real source that reads
    persisted transcripts from ``runtime/transcript.py`` / ``commit_boundary``.
    """

    model_config = ConfigDict(
        frozen=True,
        populate_by_name=True,
        extra="forbid",
        validate_default=True,
    )

    session_id: str = Field(alias="sessionId")
    #: Sequence of turn dicts — kept as plain dicts so PR3 can parse freely.
    turns: tuple[dict[str, Any], ...]
    final_output: str = Field(alias="finalOutput")
    #: AI's first-pass draft, to be diffed against ``final_output`` in PR3 for
    #: the draft-vs-final diff signal.  ``None`` when no draft was captured.
    #: TODO(PR3): implement diff signal extraction using this field.
    draft_output: str | None = Field(default=None, alias="draftOutput")
    #: ISO-8601 UTC timestamp string (MUST end with "Z"); used for watermark
    #: comparison (lexicographic).  Producers must normalize to "Z".
    ts: str

    @field_validator("ts", mode="after")
    @classmethod
    def _require_utc_z_suffix(cls, value: str) -> str:
        """Enforce that ``ts`` ends with ``"Z"`` for sound lexicographic comparison.

        Rejects any timestamp that uses a numeric UTC offset (e.g. ``+00:00``)
        because mixed-format strings cannot be compared lexicographically.
        Producers must normalize to the ``Z`` form before constructing a trace.
        """
        if not value.endswith("Z"):
            raise ValueError(
                f"SessionTrace.ts must end with 'Z' (UTC); got {value!r}. "
                "Normalize to UTC 'Z' form before constructing a SessionTrace."
            )
        return value


# ---------------------------------------------------------------------------
# Local-fake transcript source
# ---------------------------------------------------------------------------


class LocalFakeTranscriptSource:
    """Injected-fixture transcript source for local-fake / test runs.

    Accepts a tuple of ``SessionTrace`` objects at construction time and
    returns the subset with ``ts > watermark`` from ``read_since``.

    Watermark comparison is purely lexicographic on the ``ts`` string, which
    is correct for ISO-8601 timestamps with the same timezone offset.
    """

    def __init__(self, *, traces: tuple[SessionTrace, ...]) -> None:
        self._traces = traces

    async def read_since(
        self, watermark: str | None
    ) -> tuple[SessionTrace, ...]:
        if watermark is None:
            return self._traces
        return tuple(t for t in self._traces if t.ts > watermark)


__all__ = [
    "LearningCandidate",
    "LocalFakeTranscriptSource",
    "SessionTrace",
    "TranscriptSource",
]
