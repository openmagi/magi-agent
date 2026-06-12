"""Group B.3 — a pack evidence_producer's public ref satisfies the LIVE
required-evidence gate (``harness/verifier_bus.execute_pre_final_verifier_bus``,
the function ``cli/engine.py`` calls). Present -> pass; absent -> block."""
from __future__ import annotations

from pathlib import Path

import magi_agent
from magi_agent.harness.verifier_bus import execute_pre_final_verifier_bus
from magi_agent.packs.registries import load_into_registries

_FIRST_PARTY_ROOT = Path(magi_agent.__file__).parent / "firstparty" / "packs"


def test_pack_producer_ref_satisfies_required_evidence_gate() -> None:
    registries, _ = load_into_registries([_FIRST_PARTY_ROOT / "evidence_gitdiff"])
    spec = registries.evidence_producers.resolve("evidence:gitdiff@1")
    required = (spec.public_ref,)

    # Gate with the producer's contributed ref present -> pass.
    bus_pass = execute_pre_final_verifier_bus(
        required_evidence=required,
        required_validators=(),
        observed_public_refs=(spec.public_ref,),
        evidence_records=(),
        document_coverage_gate_enabled=False,
    )
    assert bus_pass["decision"] == "pass"

    # Gate with the ref absent -> block (proves the gate is real, not cosmetic).
    bus_block = execute_pre_final_verifier_bus(
        required_evidence=required,
        required_validators=(),
        observed_public_refs=(),
        evidence_records=(),
        document_coverage_gate_enabled=False,
    )
    assert bus_block["decision"] == "block"
    assert spec.public_ref in bus_block["missingEvidence"]
