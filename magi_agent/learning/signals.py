"""Deterministic correction-signal extraction — PR3.

Extracts *correction signals* from a ``SessionTrace`` using ONLY the structure
of the transcript.  There are **no model calls** and **no randomness** here:
given the same trace, ``extract_signals`` always returns the same tuple in the
same order.  This is what lets the whole reflection pipeline run end-to-end with
``llm_attached=False`` (the real LLM-backed labeler is deferred to PR7).

Four signal kinds are extracted, each keyed off transcript structure:

``diff``
    The AI's first-pass ``draft_output`` differs (meaningfully) from the sent
    ``final_output``.  Skipped when ``draft_output is None`` or draft == final.
    (Whitespace-only differences survive here as a ``diff`` but are dropped by
    the noise filter in ``labeler.py`` — extraction stays simple/structural.)

``redirect``
    A user turn that immediately follows an assistant turn, where that assistant
    turn was itself preceded by at least one earlier user turn.  Structurally
    this is "assistant produced something, then the user came back to steer".
    We deliberately do NOT regex user prose for sentiment — only turn *adjacency*
    plus the role sequence.  Conservative by design.

``retry``
    The same tool / research is invoked again later in the session — detected
    from repeated tool-call entries in ``turns`` (same tool name appearing 2+
    times).  One signal per distinct tool that repeats.

``acceptance``
    A positive signal: the final output was sent with no edit (``draft_output``
    is None, or draft == final) AND there was no redirect in the session.  This
    validates current behavior rather than correcting it.

Evidence is structured (turn indices, tool names) so downstream labeling /
auditing can point back into the trace without re-parsing prose.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from magi_agent.learning.candidates import SessionTrace


SignalKind = Literal["diff", "redirect", "retry", "acceptance"]

#: Stable extraction order so output ordering is deterministic regardless of
#: which detectors fire.
_SIGNAL_ORDER: tuple[SignalKind, ...] = ("diff", "redirect", "retry", "acceptance")


class Signal(BaseModel):
    """A single deterministic correction signal extracted from a trace.

    ``evidence`` is a structured mapping with refs into the originating trace
    (e.g. ``{"turnIndices": [1, 2]}`` or ``{"tool": "web_search", ...}``).
    Frozen, camelCase-aliased, ``extra="forbid"`` — matching the conventions in
    ``models.py`` / ``candidates.py``.
    """

    model_config = ConfigDict(
        frozen=True,
        populate_by_name=True,
        extra="forbid",
        validate_default=True,
    )

    kind: SignalKind
    session_id: str = Field(alias="sessionId")
    #: Structured references into the trace (turn indices, tool names, ...).
    evidence: dict[str, Any]
    summary: str


# ---------------------------------------------------------------------------
# Turn helpers (pure, structural)
# ---------------------------------------------------------------------------


def _role(turn: dict[str, Any]) -> str:
    """Return a normalized role string for a turn dict."""
    role = turn.get("role")
    return str(role).lower() if role is not None else ""


def _is_assistant(turn: dict[str, Any]) -> bool:
    return _role(turn) in ("assistant", "agent", "model")


def _is_user(turn: dict[str, Any]) -> bool:
    return _role(turn) == "user"


def _tool_name(turn: dict[str, Any]) -> str | None:
    """Return the tool name if *turn* is a tool-call entry, else None.

    Detected structurally: either ``role == "tool"`` or the presence of a
    ``tool`` / ``tool_name`` / ``toolName`` key naming the invoked tool.
    """
    name = turn.get("tool") or turn.get("tool_name") or turn.get("toolName")
    if name:
        return str(name)
    if _role(turn) == "tool":
        # role=tool without an explicit name key — fall back to generic key
        n = turn.get("name")
        return str(n) if n else None
    return None


# ---------------------------------------------------------------------------
# Per-kind detectors
# ---------------------------------------------------------------------------


def _diff_signal(trace: SessionTrace) -> Signal | None:
    draft = trace.draft_output
    if draft is None:
        return None
    if draft == trace.final_output:
        return None
    return Signal(
        kind="diff",
        sessionId=trace.session_id,
        evidence={
            "draftLen": len(draft),
            "finalLen": len(trace.final_output),
        },
        summary="Draft output was edited before the final output was sent.",
    )


def _redirect_signals(trace: SessionTrace) -> list[Signal]:
    """Detect user turns that redirect after an assistant turn.

    Structural rule (conservative): a user turn at index ``i`` is a redirect iff
    the turn at ``i-1`` is an assistant turn AND there is at least one user turn
    before index ``i-1`` (i.e. the assistant had already responded to an
    original request, and the user is now steering).
    """
    out: list[Signal] = []
    turns = trace.turns
    for i in range(1, len(turns)):
        if not _is_user(turns[i]):
            continue
        if not _is_assistant(turns[i - 1]):
            continue
        # require a prior user turn before the assistant response
        if not any(_is_user(turns[j]) for j in range(i - 1)):
            continue
        out.append(
            Signal(
                kind="redirect",
                sessionId=trace.session_id,
                evidence={"turnIndices": [i - 1, i]},
                summary=(
                    "User followed up to steer/correct after an assistant turn."
                ),
            )
        )
    return out


def _retry_signals(trace: SessionTrace) -> list[Signal]:
    """One signal per distinct tool invoked 2+ times in the session."""
    occurrences: dict[str, list[int]] = {}
    for idx, turn in enumerate(trace.turns):
        name = _tool_name(turn)
        if name is None:
            continue
        occurrences.setdefault(name, []).append(idx)

    out: list[Signal] = []
    # sort by tool name for deterministic ordering
    for name in sorted(occurrences):
        indices = occurrences[name]
        if len(indices) < 2:
            continue
        out.append(
            Signal(
                kind="retry",
                sessionId=trace.session_id,
                evidence={"tool": name, "turnIndices": list(indices)},
                summary=f"Tool {name!r} was invoked {len(indices)} times (re-tool/retry).",
            )
        )
    return out


def _acceptance_signal(
    trace: SessionTrace, *, has_redirect: bool
) -> Signal | None:
    """Positive signal: output accepted as-is (no edit, no redirect)."""
    if has_redirect:
        return None
    draft = trace.draft_output
    accepted = draft is None or draft == trace.final_output
    if not accepted:
        return None
    return Signal(
        kind="acceptance",
        sessionId=trace.session_id,
        evidence={"finalLen": len(trace.final_output)},
        summary="Final output was accepted without edits or redirects.",
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_signals(trace: SessionTrace) -> tuple[Signal, ...]:
    """Extract correction signals from *trace*, deterministically.

    Ordering: signals are returned grouped by kind in ``_SIGNAL_ORDER`` (diff,
    redirect, retry, acceptance), and within a kind in the order produced by the
    detector (which is itself stable — turn order or sorted tool name).
    """
    by_kind: dict[SignalKind, list[Signal]] = {k: [] for k in _SIGNAL_ORDER}

    diff = _diff_signal(trace)
    if diff is not None:
        by_kind["diff"].append(diff)

    redirects = _redirect_signals(trace)
    by_kind["redirect"].extend(redirects)

    by_kind["retry"].extend(_retry_signals(trace))

    acceptance = _acceptance_signal(trace, has_redirect=bool(redirects))
    if acceptance is not None:
        by_kind["acceptance"].append(acceptance)

    ordered: list[Signal] = []
    for kind in _SIGNAL_ORDER:
        ordered.extend(by_kind[kind])
    return tuple(ordered)


__all__ = [
    "Signal",
    "SignalKind",
    "extract_signals",
]
