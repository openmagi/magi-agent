"""Best-effort finalization — first-party never-empty answer mechanism.

Anti-overfitting firewall: this module MUST NOT import from any benchmark
layer. It consumes only the first-party answer-policy seam
(:mod:`magi_agent.research.answer_policy`) and the stdlib. The GAIA layer
(``benchmarks/gaia/``) imports *this* module, never the other way around.

HAL learnings (2026-06-11): HAL's generalist agent never returns an empty
answer — when steps/budget run out, a final forced-synthesis prompt makes the
model commit to a best guess from accumulated context. Any bounded run (a
mission hitting its step budget, a headless run hitting wall-clock, a child
runner exhausting its ledger) benefits from "synthesize a best effort from
what was gathered + label the uncertainty" instead of returning nothing.

Policy gate (no new env var): ``MAGI_ANSWER_POLICY`` via
:func:`magi_agent.research.answer_policy.answer_policy`.

* ``abstain`` (DEFAULT, incl. unset/empty/unknown) — :func:`finalize_answer`
  is a pure pass-through: the candidate is returned byte-identical and the
  model provider is **never** called. Honest "I don't know" behavior is
  unchanged unless an operator opts in.
* ``commit`` — when the candidate is a non-answer, one synthesis call is made
  from the question + (head+tail capped) gathered evidence. All synthesis
  failures fall back to the original candidate; the function never raises.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Callable

from magi_agent.research.answer_policy import AnswerPolicy, answer_policy

# ---------------------------------------------------------------------------
# Configuration / result contracts
# ---------------------------------------------------------------------------

DEFAULT_UNCERTAINTY_LABEL = (
    "(best-effort answer — confidence low; budget was exhausted)"
)


@dataclass(frozen=True)
class BestEffortConfig:
    """Tuning knobs for :func:`finalize_answer` (all caps fail-soft)."""

    max_evidence_chars: int = 24_000
    """Head+tail cap applied to the evidence block in the synthesis prompt."""

    label_uncertainty: bool = True
    """Append :attr:`uncertainty_label` to synthesized answers."""

    uncertainty_label: str = DEFAULT_UNCERTAINTY_LABEL

    max_answer_chars: int = 2_000
    """Cap on the synthesized reply (truncate, never raise)."""


@dataclass(frozen=True)
class FinalAnswer:
    """Result of :func:`finalize_answer` for receipts/telemetry."""

    text: str
    synthesized: bool
    """``True`` only when a synthesis call replaced the candidate."""

    policy: AnswerPolicy
    """The resolved answer policy for this call."""


# ---------------------------------------------------------------------------
# Non-answer detection
# ---------------------------------------------------------------------------

# Generalization of the (benchmark-private) GAIA abstention patterns; the
# phrases are generic model hedging, not GAIA-specific.
_NON_ANSWER_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"unable\s+to\s+(determine|answer|provide|find)", re.IGNORECASE),
    re.compile(r"cannot\s+determine", re.IGNORECASE),
    re.compile(r"can\s+not\s+determine", re.IGNORECASE),
    re.compile(r"not\s+able\s+to\s+(determine|answer|provide)", re.IGNORECASE),
    re.compile(r"insufficient\s+information", re.IGNORECASE),
    re.compile(r"awaiting\b", re.IGNORECASE),
    re.compile(r"i\s+don'?t\s+know", re.IGNORECASE),
    re.compile(r"i\s+do\s+not\s+know", re.IGNORECASE),
]


def is_non_answer(text: str) -> bool:
    """Return ``True`` if *text* looks like an abstention or non-answer.

    Matches common hedging phrases emitted by language models when they
    decline to commit to an answer (e.g. "unable to determine",
    "insufficient information", "I don't know") or an empty/whitespace-only
    string.
    """
    stripped = text.strip()
    if not stripped:
        return True
    return any(pattern.search(stripped) for pattern in _NON_ANSWER_PATTERNS)


# ---------------------------------------------------------------------------
# Synthesis
# ---------------------------------------------------------------------------

_SYNTHESIS_PROMPT_TEMPLATE = (
    "You previously gathered the following evidence about the question below.\n\n"
    "QUESTION:\n{question}\n\n"
    "EVIDENCE:\n{evidence}\n\n"
    "Based on the above, give your single best-guess final answer now. "
    "Output ONLY the answer — no hedging, no explanation, no 'I cannot determine'. "
    "If you are uncertain, still provide your single most probable best guess."
)

_TRUNCATION_MARKER = "\n…[truncated]…\n"

_EMPTY_EVIDENCE_PLACEHOLDER = "(no additional evidence gathered)"


def _cap_head_tail(text: str, cap: int) -> str:
    """Cap *text* to ~*cap* chars keeping head and tail (middle elided)."""
    if cap <= 0 or len(text) <= cap:
        return text
    head = text[: cap * 2 // 3]
    tail = text[-(cap // 3):]
    return f"{head}{_TRUNCATION_MARKER}{tail}"


def finalize_answer(
    question: str,
    candidate: str,
    evidence: str,
    model_provider: Callable[[str], str],
    *,
    env: Mapping[str, str] | None = None,
    config: BestEffortConfig | None = None,
) -> FinalAnswer:
    """Apply the configured answer policy to a run's *candidate* answer.

    Contract:

    1. ``policy == "abstain"`` (DEFAULT) → the candidate is returned
       byte-identical, *model_provider* is never called.
    2. ``policy == "commit"`` and the candidate is a real answer → returned
       unchanged (a real answer is never overwritten).
    3. Otherwise one synthesis call is made from *question* + capped
       *evidence*. On provider exception, non-``str`` reply, or a reply that
       itself abstains, the original candidate is returned (fail-open).
       The function never raises.
    """
    policy = answer_policy(env=env)
    if policy == "abstain":
        return FinalAnswer(text=candidate, synthesized=False, policy="abstain")
    if not is_non_answer(candidate):
        return FinalAnswer(text=candidate, synthesized=False, policy="commit")

    cfg = config if config is not None else BestEffortConfig()
    evidence_block = evidence if evidence.strip() else _EMPTY_EVIDENCE_PLACEHOLDER
    evidence_block = _cap_head_tail(evidence_block, cfg.max_evidence_chars)
    prompt = _SYNTHESIS_PROMPT_TEMPLATE.format(
        question=question, evidence=evidence_block
    )
    try:
        raw = model_provider(prompt)
        if not isinstance(raw, str):
            raise TypeError(f"model_provider returned {type(raw).__name__}, not str")
        reply = raw.strip()
    except Exception:  # noqa: BLE001 — fail-open by design
        return FinalAnswer(text=candidate, synthesized=False, policy="commit")
    if is_non_answer(reply):
        return FinalAnswer(text=candidate, synthesized=False, policy="commit")

    reply = reply[: cfg.max_answer_chars]
    if cfg.label_uncertainty:
        reply = f"{reply}\n{cfg.uncertainty_label}"
    return FinalAnswer(text=reply, synthesized=True, policy="commit")


__all__ = [
    "DEFAULT_UNCERTAINTY_LABEL",
    "BestEffortConfig",
    "FinalAnswer",
    "finalize_answer",
    "is_non_answer",
]
