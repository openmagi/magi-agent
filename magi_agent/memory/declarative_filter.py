"""Declarative-only filter for agent memory writes (D2).

Only DECLARATIVE facts — stable preferences, traits, and user-level knowledge —
may be persisted.  Task-state ("PR #123 merged", "phase done", commit SHAs,
"currently doing X") is REJECTED because it is transient and belongs in the
task context, not long-term memory.

Design notes
------------
* Conservative heuristics: false-positive rejection is preferred over leaking
  task-state into long-term memory.
* No LLM calls — pure deterministic regex + keyword rules, suitable for the
  write-boundary hot-path.
* ``is_declarative(fact)`` is the primary public API; ``is_declarative_result``
  exposes the reason for tooling / tests.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Compiled task-state detection patterns
# ---------------------------------------------------------------------------

# PR / issue numbers  (#123, PR #123, issue #456)
_PR_ISSUE_RE = re.compile(
    r"(?:pr|issue|bug|ticket|jira)\s*#\s*\d+|#\s*\d{2,}",
    re.IGNORECASE,
)

# Commit SHAs — see _LONG_SHA_RE / _BARE_SHA_RE below (used via _is_commit_sha_reference)

# Explicit task-state verbs / phrases
_TASK_STATE_VERB_RE = re.compile(
    r"\b(?:"
    r"merged|merge[sd]?|"
    r"done|finished|completed|resolved|closed|"
    r"in\s+progress|in_progress|"
    r"deployed|deploying|deployment|"
    r"rolled\s+out|rolled_out|"
    r"landed|committed|pushed|"
    r"shipped|shipping|"
    r"released|releasing|"
    r"rolling\s+out|"
    r"reverted|rolled\s+back|"
    r"cut\s+a\s+release"
    r")\b",
    re.IGNORECASE,
)

# "currently doing X" / "currently working on X" — task-state phrasing
# (distinguished from "currently prefers X" which is a preference sentence)
_CURRENTLY_DOING_RE = re.compile(
    r"\bcurrently\s+(?:doing|working\s+on|running|executing|migrating|"
    r"processing|deploying|updating|refactoring|implementing|fixing|"
    r"debugging|testing)\b",
    re.IGNORECASE,
)

# ISO 8601 timestamps used as task-state markers
# e.g. "ran at 2026-06-07T12:34:56Z" (distinct from a plain year "2026")
_TIMESTAMP_AS_STATE_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?:[Z]|[+-]\d{2}:?\d{2})?",
)

# Phase / step markers: "Phase 2 done", "Step 3 complete"
_PHASE_STEP_RE = re.compile(
    r"\b(?:phase|step|stage|sprint|iteration)\s+\d+\s+(?:done|complete|finished|merged|"
    r"deployed|landed|in\s+progress|started|running)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Public result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeclarativeFilterResult:
    """Outcome of the declarative-only filter check."""

    accepted: bool
    rejection_reason: str | None


_ACCEPTED = DeclarativeFilterResult(accepted=True, rejection_reason=None)


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def is_declarative_result(fact: str) -> DeclarativeFilterResult:
    """Return a ``DeclarativeFilterResult`` describing the filter decision.

    Parameters
    ----------
    fact:
        The raw fact string the agent wants to persist.

    Returns
    -------
    DeclarativeFilterResult
        ``.accepted=True`` when the fact is considered declarative;
        ``.accepted=False`` with a non-empty ``.rejection_reason`` otherwise.
    """
    stripped = fact.strip()

    if not stripped:
        return DeclarativeFilterResult(
            accepted=False,
            rejection_reason="fact is empty — nothing to persist",
        )

    if _PR_ISSUE_RE.search(stripped):
        return DeclarativeFilterResult(
            accepted=False,
            rejection_reason=(
                "fact contains a PR/issue number — task-state is not a declarative fact"
            ),
        )

    if _PHASE_STEP_RE.search(stripped):
        return DeclarativeFilterResult(
            accepted=False,
            rejection_reason=(
                "fact describes a phase/step completion event — task-state is not "
                "a declarative fact"
            ),
        )

    if _TASK_STATE_VERB_RE.search(stripped):
        return DeclarativeFilterResult(
            accepted=False,
            rejection_reason=(
                "fact contains a task-state verb (merged, done, in progress, deployed …) "
                "— persist task outcomes in task context, not long-term memory"
            ),
        )

    if _CURRENTLY_DOING_RE.search(stripped):
        return DeclarativeFilterResult(
            accepted=False,
            rejection_reason=(
                "fact describes a current task ('currently doing …') — task-state is "
                "not a declarative fact"
            ),
        )

    if _TIMESTAMP_AS_STATE_RE.search(stripped):
        return DeclarativeFilterResult(
            accepted=False,
            rejection_reason=(
                "fact contains a timestamp that appears to anchor a task event — "
                "event timestamps are task-state, not declarative facts"
            ),
        )

    # Conservative commit SHA: only reject if the string looks like a deliberate
    # reference to a SHA (standalone uppercase-safe 40-char hex or 7-char abbreviation
    # with context words like "commit", "sha", "hash")
    if _is_commit_sha_reference(stripped):
        return DeclarativeFilterResult(
            accepted=False,
            rejection_reason=(
                "fact contains what appears to be a commit SHA reference — "
                "commit hashes are task-state, not declarative facts"
            ),
        )

    return _ACCEPTED


def is_declarative(fact: str) -> bool:
    """Return ``True`` when *fact* is a declarative (stable) user fact.

    Conservative: facts that match any task-state heuristic return ``False``.
    """
    return is_declarative_result(fact).accepted


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


_COMMIT_CONTEXT_RE = re.compile(
    r"\b(?:commit|sha|hash|rev|revision|patch)\b",
    re.IGNORECASE,
)
_LONG_SHA_RE = re.compile(r"\b[0-9a-f]{40}\b")
# Standalone hex token: 7–40 chars, all lowercase hex, AND at least one digit.
# Requiring ≥1 digit avoids rejecting common all-letter English words that happen
# to be valid hex (e.g. "facade", "decade", "added") while still catching the
# vast majority of commit SHAs which always contain at least one digit.
_BARE_SHA_RE = re.compile(r"\b(?=[0-9a-f]*[0-9])[0-9a-f]{7,40}\b")


def _is_commit_sha_reference(text: str) -> bool:
    """Return True if the text contains a reference to a commit SHA.

    We accept any of:
    * a full 40-hex-char SHA anywhere in the text, OR
    * a standalone hex token 7–40 chars that contains at least one digit
      (overwhelmingly a commit SHA rather than an English word), OR
    * a 7-char short SHA with a context word (commit/sha/hash/rev/revision/patch)
      — kept for backwards-compatibility; now largely subsumed by the rule above.
    """
    if _LONG_SHA_RE.search(text):
        return True
    if _BARE_SHA_RE.search(text):
        return True
    return False


__all__ = [
    "DeclarativeFilterResult",
    "is_declarative",
    "is_declarative_result",
]
