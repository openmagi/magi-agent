"""Group B.1 — bundled first-party ``evidence_gitdiff`` producer pack registers a
``ProducerSpec`` via the typed ``EvidenceProducerProvideContext`` (D5).

Real-ABI adaptation: the public ref MUST carry a recognized public-ref prefix
(``evidence:``) so it reaches the live ``harness/verifier_bus`` enforce path —
``GitDiff`` alone is NOT a valid public ref (see ``_PUBLIC_REF_PREFIXES``).
"""
from __future__ import annotations

from pathlib import Path

import magi_agent
from magi_agent.packs.registries import load_into_registries

_FIRST_PARTY_ROOT = Path(magi_agent.__file__).parent / "firstparty" / "packs"
# F-4: canonical ref + public_ref both ``evidence:git-diff@1`` (was a
# 3-way drift pre-F-4: ``gitdiff@1`` registration ref / ``gitDiff@1``
# public_ref / ``git-diff`` consumer key).
_REF = "evidence:git-diff@1"


def test_evidence_gitdiff_pack_registers_producer() -> None:
    registries, report = load_into_registries([_FIRST_PARTY_ROOT / "evidence_gitdiff"])
    assert _REF in report.registered
    spec = registries.evidence_producers.resolve(_REF)
    assert spec is not None
    assert spec.evidence_type == "GitDiff"
    assert "tool_host" in spec.producer_surfaces
    assert spec.public_ref == "evidence:git-diff@1"
