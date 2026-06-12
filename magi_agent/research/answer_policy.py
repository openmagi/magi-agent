"""Answer Policy — configurable commit-vs-abstain seam (first-party, P6).

Anti-overfitting firewall: this module MUST NOT import from any benchmark
scoring layer.  Any PR that adds a benchmarks.gaia import is a violation.

Principle 6 (GAIA learnings — 2026-06-10):
  Whether the agent commits to a best-guess answer or is allowed to abstain
  ("I cannot determine…") is task-dependent.  GAIA forces a commit because
  abstention scores zero; production agents should be allowed to say "I don't
  know" when uncertain.

  This module provides:
    - The ``ANSWER_POLICY_ENV`` constant (``"MAGI_ANSWER_POLICY"``).
    - The ``AnswerPolicy`` type alias: ``"abstain"`` | ``"commit"``.
    - ``answer_policy()`` — reads the env var and returns the resolved policy.
      Default = ``"abstain"`` (production-honest).
    - ``should_force_answer()`` — convenience boolean helper.

  The GAIA benchmark layer sets ``MAGI_ANSWER_POLICY=commit`` in its harness;
  this module does NOT hardcode ``commit`` anywhere — it only provides the
  seam and the production-safe default.

Environment variable: MAGI_ANSWER_POLICY
  values: abstain | commit   (default: abstain when unset or empty)
  Any unknown value also falls back to "abstain".
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Literal

# ---------------------------------------------------------------------------
# Type alias
# ---------------------------------------------------------------------------

AnswerPolicy = Literal["abstain", "commit"]
"""Configurable answer policy.

* ``"abstain"`` — the agent may decline to answer when uncertain (production
  default; honest behaviour).
* ``"commit"``  — the agent must provide a best-guess answer even under
  uncertainty (benchmark harness / evaluation contexts).
"""

# ---------------------------------------------------------------------------
# Environment variable constant
# ---------------------------------------------------------------------------

ANSWER_POLICY_ENV: str = "MAGI_ANSWER_POLICY"

_COMMIT_VALUE: str = "commit"
_ABSTAIN_VALUE: str = "abstain"
_VALID_POLICIES: frozenset[str] = frozenset({_ABSTAIN_VALUE, _COMMIT_VALUE})

# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def answer_policy(
    env: Mapping[str, str] | None = None,
) -> AnswerPolicy:
    """Return the configured :data:`AnswerPolicy`.

    Reads ``MAGI_ANSWER_POLICY`` from *env* (defaults to ``os.environ``).

    Resolution rules:
    * Unset / empty → ``"abstain"`` (production-honest default).
    * ``"commit"`` (case-insensitive) → ``"commit"``.
    * ``"abstain"`` (case-insensitive) → ``"abstain"``.
    * Any other value → ``"abstain"`` (safe fallback).

    Parameters
    ----------
    env:
        Optional explicit env mapping (useful in tests to avoid os.environ
        mutation).  Defaults to ``os.environ``.

    Returns
    -------
    AnswerPolicy
        Either ``"abstain"`` or ``"commit"``.
    """
    source: Mapping[str, str] = os.environ if env is None else env
    raw = (source.get(ANSWER_POLICY_ENV) or "").strip().lower()
    if raw in _VALID_POLICIES:
        return raw  # type: ignore[return-value]
    return _ABSTAIN_VALUE  # type: ignore[return-value]


def should_force_answer(
    env: Mapping[str, str] | None = None,
) -> bool:
    """Return ``True`` iff the answer policy is ``"commit"`` (force a best-guess).

    Convenience wrapper around :func:`answer_policy`.  Returns ``False``
    (allow abstention) by default — production-safe.

    Parameters
    ----------
    env:
        Optional explicit env mapping.  Defaults to ``os.environ``.

    Returns
    -------
    bool
        ``True`` when ``MAGI_ANSWER_POLICY=commit``, ``False`` otherwise.
    """
    return answer_policy(env=env) == _COMMIT_VALUE


__all__ = [
    "ANSWER_POLICY_ENV",
    "AnswerPolicy",
    "answer_policy",
    "should_force_answer",
]
