"""First-party GitDiff evidence producer (no privilege, typed-ctx only).

Receives ONLY the narrow ``EvidenceProducerProvideContext`` (D5) and registers a
``ProducerSpec``. ``public_ref`` carries the ``evidence:`` prefix so the
contributed ref reaches the live ``harness/verifier_bus`` required-evidence gate.
"""
from __future__ import annotations

from magi_agent.packs.context import EvidenceProducerProvideContext, ProducerSpec


def provide_gitdiff_producer(context: EvidenceProducerProvideContext) -> None:
    # F-4: canonical ref + public_ref both spelled ``evidence:git-diff@1``
    # so the producer's contributed public ref reaches the live
    # ``harness/verifier_bus`` required-evidence gate without an alias
    # lookup. Pre-F-4 the registration ref was ``gitdiff@1`` and the
    # public ref was ``gitDiff@1`` while every consumer keyed on
    # ``git-diff`` — a 3-way drift documented as a known inconsistency.
    context.register(
        "evidence:git-diff@1",
        ProducerSpec(
            evidence_type="GitDiff",
            public_ref="evidence:git-diff@1",
            producer_surfaces=("tool_host", "transcript"),
        ),
    )
