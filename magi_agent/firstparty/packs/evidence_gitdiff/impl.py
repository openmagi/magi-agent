"""First-party GitDiff evidence producer (no privilege, typed-ctx only).

Receives ONLY the narrow ``EvidenceProducerProvideContext`` (D5) and registers a
``ProducerSpec``. ``public_ref`` carries the ``evidence:`` prefix so the
contributed ref reaches the live ``harness/verifier_bus`` required-evidence gate.
"""
from __future__ import annotations

from magi_agent.packs.context import EvidenceProducerProvideContext, ProducerSpec


def provide_gitdiff_producer(context: EvidenceProducerProvideContext) -> None:
    context.register(
        "evidence:gitdiff@1",
        ProducerSpec(
            evidence_type="GitDiff",
            public_ref="evidence:gitDiff@1",
            producer_surfaces=("tool_host", "transcript"),
        ),
    )
