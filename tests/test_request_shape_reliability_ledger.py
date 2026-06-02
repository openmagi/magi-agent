from __future__ import annotations

import pytest

from openmagi_core_agent.runtime.request_shape import RequestShapeLedger


def test_ledger_records_what_model_actually_saw_by_phase_without_raw_payload() -> None:
    ledger = RequestShapeLedger()

    record = ledger.record_model_phase(
        turnId="turn-1",
        phase="source_extraction",
        provider="google",
        model="gemini-3.5-flash",
        modelTier="cheap",
        inputRefs=("source:web:src_1", "summary:source:web:src_1"),
        evidenceRefs=("evidence:web:src_1",),
        rawInput="Authorization: Bearer secret should not be serialized",
    )

    projected = record.public_projection()
    assert projected["modelTier"] == "cheap"
    assert projected["phase"] == "source_extraction"
    assert projected["inputRefs"] == ["source:web:src_1", "summary:source:web:src_1"]
    assert "Authorization" not in str(projected)
    assert record.input_digest.startswith("sha256:")


def test_ledger_records_output_digest_validator_refs_cost_and_fallback_reason() -> None:
    record = RequestShapeLedger().record_model_phase(
        turnId="turn-1",
        phase="final_verification",
        provider="google",
        model="gemini-3.5-flash",
        modelTier="cheap",
        inputRefs=("summary:research:1",),
        evidenceRefs=("evidence:web:src_1",),
        outputText="Final answer draft with sk-live-secret",
        validatorRefs=("validator:research:fact-grounding",),
        validatorStatuses={"validator:research:fact-grounding": "failed"},
        costEstimateUsd=0.004,
        escalationReason="validator_failed_twice",
        fallbackReason="fallback_to_typescript",
    )
    projection = record.public_projection()

    assert "outputText" not in projection
    assert "sk-live-secret" not in str(projection)
    assert projection["outputDigest"].startswith("sha256:")
    assert projection["validatorRefs"] == ["validator:research:fact-grounding"]
    assert projection["validatorStatuses"] == {"validator:research:fact-grounding": "failed"}
    assert projection["costEstimateUsd"] == 0.004
    assert projection["escalationReason"] == "validator_failed_twice"
    assert projection["fallbackReason"] == "fallback_to_typescript"


def test_duplicate_model_phase_records_are_idempotent() -> None:
    ledger = RequestShapeLedger()

    first = ledger.record_model_phase(
        turnId="turn-1",
        phase="source_extraction",
        provider="google",
        model="gemini-3.5-flash",
        modelTier="cheap",
        inputRefs=("source:web:1",),
        rawInput="same",
    )
    second = ledger.record_model_phase(
        turnId="turn-1",
        phase="source_extraction",
        provider="google",
        model="gemini-3.5-flash",
        modelTier="cheap",
        inputRefs=("source:web:1",),
        rawInput="same",
    )

    assert first.record_id == second.record_id
    assert len(ledger.records()) == 1


def test_forged_stronger_tier_for_known_cheap_model_is_rejected() -> None:
    ledger = RequestShapeLedger()

    with pytest.raises(ValueError, match="does not match registry"):
        ledger.record_model_phase(
            turnId="turn-1",
            phase="source_extraction",
            provider="google",
            model="gemini-3.5-flash",
            modelTier="sota",
            inputRefs=("source:web:1",),
        )


def test_private_refs_and_payloads_are_not_serialized() -> None:
    record = RequestShapeLedger().record_model_phase(
        turnId="/Users/kevin/private/turn",
        phase="source_extraction",
        provider="google",
        model="gemini-3.5-flash",
        modelTier="cheap",
        inputRefs=(
            "source:web:1",
            "/Users/kevin/private/raw.txt",
            "source:web:github_pat_unsafeToken12345",
        ),
        evidenceRefs=("evidence:web:1", "Bearer raw-evidence-ref"),
        contextPlanDigest="sha256:" + "b" * 64,
        rawInput="cookie=session Authorization: Bearer unsafe",
    )
    projection = record.public_projection()
    encoded = str(projection)

    assert projection["turnId"].startswith("turn:")
    assert projection["inputRefs"] == ["source:web:1"]
    assert projection["evidenceRefs"] == ["evidence:web:1"]
    assert projection["contextPlanDigest"] == "sha256:" + "b" * 64
    assert "/Users/kevin" not in encoded
    assert "github_pat_" not in encoded
    assert "Bearer raw" not in encoded
