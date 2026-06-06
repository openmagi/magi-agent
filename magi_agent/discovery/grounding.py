"""Triple grounding verifier for the discovery orchestrator (TIDE ``D̂ ⊆ D``).

TIDE shapes each prediction as a triple ``(b, D̂, a)`` — a problem description,
the *evidence* ``D̂`` that grounds it, and an action. This module is the
post-pass that checks every ``evidence_id`` in a prediction's ``D̂`` actually
exists in the corpus ``D``, then tags (and, under ``strict`` mode, drops)
predictions that are not fully grounded.

The verifier plugs into ``orchestrator.run_discovery`` via its
``grounding_verifier`` hook WITHOUT editing the orchestrator: the hook contract
is ``(batch, corpus) -> Sequence[DiscoveryPrediction]`` (see
``orchestrator.GroundingVerifier``). :func:`make_grounding_verifier` binds the
mode and returns a callable matching that contract; the corpus is supplied per
call by the orchestrator.

Evidence-machinery reuse
------------------------
The grounding check is a plain set-membership test (``set(evidence_ids) &
corpus.ids()``). The repo's ``EvidenceContractEngine`` (``evidence/contracts``)
is built around ``EvidenceRecord``/``EvidenceRequirement`` with built-in
evidence types, ``observed_at`` timestamps, field matchers and staleness
boundaries — none of which map onto "is this corpus id present." Likewise
``citation_audit`` is bound to a ``LocalResearchSourceLedger`` of inspected web
sources. Forcing either API would mean fabricating synthetic evidence records
per corpus id, contorting a one-line membership check, so this module
implements the check directly. It does, however, deliberately ALIGN with that
module's semantics: the ``grounding_status`` vocabulary mirrors the
grounded / partial / unverifiable spirit of ``evidence.claim_grounding``'s
``SupportStatus``, and — like ``EvidenceEnforcementAuthorityFlags``' permanent
``Literal[False]`` block-authority — ``strict`` drop here is an ORCHESTRATION
choice (a filter inside the discovery loop), not an enforcement block. No
authority flag is flipped.
"""
from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from typing import Literal

from magi_agent.discovery.models import DiscoveryCorpus, DiscoveryPrediction

#: The orchestrator hook contract (see ``orchestrator.GroundingVerifier``).
GroundingVerifier = Callable[
    [Sequence[DiscoveryPrediction], DiscoveryCorpus],
    tuple[DiscoveryPrediction, ...],
]

#: Verifier behaviour. ``audit`` tags every prediction and drops nothing;
#: ``strict`` additionally drops fully-ungrounded predictions.
GroundingMode = Literal["audit", "strict"]

#: Environment flag selecting strict mode when no explicit mode is given.
GROUNDING_STRICT_ENV: str = "MAGI_DISCOVERY_GROUNDING_STRICT"

# Mirror the truthy parsing in ``magi_agent/discovery/gate.py``.
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


def _grounding_status(
    pred: DiscoveryPrediction, corpus_ids: frozenset[str]
) -> Literal["grounded", "partial", "ungrounded"]:
    """Classify a prediction's ``D̂ ⊆ D`` membership against the corpus ids."""
    evidence = set(pred.evidence_ids)
    if not evidence:
        # No grounding at all.
        return "ungrounded"
    present = evidence & corpus_ids
    if not present:
        return "ungrounded"
    if present == evidence:
        return "grounded"
    return "partial"


def verify_grounding(
    batch: Sequence[DiscoveryPrediction],
    corpus: DiscoveryCorpus,
    *,
    mode: GroundingMode,
) -> tuple[DiscoveryPrediction, ...]:
    """Tag (and, in ``strict`` mode, filter) a batch by corpus grounding.

    Each kept prediction is returned with ``grounding_status`` set via
    :meth:`DiscoveryPrediction.model_copy` (the model is frozen). In ``audit``
    mode every prediction is kept and tagged; in ``strict`` mode predictions
    classified ``"ungrounded"`` are dropped while ``"grounded"`` and
    ``"partial"`` are kept and tagged.
    """
    corpus_ids = corpus.ids()
    out: list[DiscoveryPrediction] = []
    for pred in batch:
        status = _grounding_status(pred, corpus_ids)
        if mode == "strict" and status == "ungrounded":
            continue
        out.append(pred.model_copy(update={"grounding_status": status}))
    return tuple(out)


def resolve_grounding_mode(
    mode: GroundingMode | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> GroundingMode:
    """Return the effective mode: explicit ``mode`` wins, else env, else audit.

    When ``mode`` is ``None`` the mode is read from ``MAGI_DISCOVERY_GROUNDING_STRICT``
    (default ``"audit"``; ``"strict"`` only when the flag is truthy), mirroring
    the truthy parsing used by the discovery gate.
    """
    if mode is not None:
        return mode
    resolved = os.environ if env is None else env
    raw = resolved.get(GROUNDING_STRICT_ENV, "0").strip().lower()
    return "strict" if raw in _TRUE_VALUES else "audit"


def make_grounding_verifier(
    *,
    mode: GroundingMode | None = None,
    env: Mapping[str, str] | None = None,
) -> GroundingVerifier:
    """Build a verifier callable matching the orchestrator hook contract.

    The returned callable has signature ``(batch, corpus) -> tuple[...]`` and
    binds the corpus at CALL time (per the hook contract), so a single verifier
    can be reused across rounds and corpora. ``mode`` defaults to env resolution
    (see :func:`resolve_grounding_mode`).
    """
    effective_mode = resolve_grounding_mode(mode, env=env)

    def verifier(
        batch: Sequence[DiscoveryPrediction],
        corpus: DiscoveryCorpus,
    ) -> tuple[DiscoveryPrediction, ...]:
        return verify_grounding(batch, corpus, mode=effective_mode)

    return verifier


__all__ = [
    "GROUNDING_STRICT_ENV",
    "GroundingMode",
    "GroundingVerifier",
    "make_grounding_verifier",
    "resolve_grounding_mode",
    "verify_grounding",
]
