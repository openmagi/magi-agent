from __future__ import annotations

import json
import subprocess
import sys
from types import SimpleNamespace

import pytest


def _stub_runner_result(
    *,
    status: str = "completed",
    imported_event_count: int = 4,
    rejected_entry_count: int = 0,
    compaction_applied: bool = False,
) -> SimpleNamespace:
    """Duck-typed runner result for the evidence builder.

    ``build_pre_gate8_continuity_canary_evidence`` reads ``status`` and
    ``context_continuity`` attributes off an opaque object; the retired
    runner-session-boundary integration drive was replaced by this stub.
    """

    return SimpleNamespace(
        status=status,
        context_continuity=SimpleNamespace(
            imported_event_count=imported_event_count,
            rejected_entry_count=rejected_entry_count,
            compaction_applied=compaction_applied,
            projection_digest="sha256:" + "1" * 64,
            model_visible_digest="sha256:" + "2" * 64,
            source_transcript_head_digest="sha256:" + "3" * 64,
        ),
    )


def test_pregate8_canary_records_ambiguous_followup_context_as_digest_only_evidence() -> None:
    from magi_agent.gates.pregate8_continuity_canary import (
        build_pre_gate8_continuity_canary_evidence,
    )

    evidence = build_pre_gate8_continuity_canary_evidence(
        _stub_runner_result(),
        adk_session_texts=["Earlier turn: Telegram-to-provisioning handoff recorded."],
        model_visible_message=(
            "[context digest] Telegram-to-provisioning handoff\n"
            "아까 말한 그거 어떻게 하면돼?"
        ),
        expected_antecedent="Telegram-to-provisioning handoff",
        current_followup="아까 말한 그거",
        fallback_status="none",
    )

    assert evidence.status == "pass"
    assert evidence.imported_event_count == 4
    assert evidence.antecedent_present_in_adk_session is True
    assert evidence.antecedent_present_in_model_visible_projection is True
    assert evidence.current_followup_present_in_model_visible_message is True
    assert evidence.projection_digest.startswith("sha256:")
    assert evidence.model_visible_digest.startswith("sha256:")
    assert evidence.source_transcript_head_digest.startswith("sha256:")
    assert evidence.observed_adk_session_digest.startswith("sha256:")
    assert evidence.observed_model_visible_digest.startswith("sha256:")
    assert evidence.response_authority == "none"
    assert evidence.local_only is True
    assert evidence.diagnostic_only is True
    assert evidence.private_payload_rejected is False
    assert evidence.authority_flags.transcript_write_allowed is False

    serialized = json.dumps(
        evidence.model_dump(by_alias=True, mode="json"),
        ensure_ascii=False,
        sort_keys=True,
    )
    assert "Telegram-to-provisioning" not in serialized
    assert "아까 말한 그거" not in serialized


def test_pregate8_canary_compaction_respected_and_forbidden_payload_absent() -> None:
    from magi_agent.gates.pregate8_continuity_canary import (
        build_pre_gate8_continuity_canary_evidence,
    )

    evidence = build_pre_gate8_continuity_canary_evidence(
        _stub_runner_result(compaction_applied=True),
        adk_session_texts=["[compact summary] vague follow-up prompts resolve"],
        model_visible_message=(
            "[compact summary] vague follow-up prompts resolve\n좀전에 말했던거 이어서 해줘"
        ),
        expected_antecedent="vague follow-up prompts resolve",
        current_followup="좀전에 말했던거",
        forbidden_payloads=("Raw pre-boundary detail",),
        require_compaction_applied=True,
        fallback_status="none",
    )

    assert evidence.status == "pass"
    assert evidence.compaction_applied is True
    assert evidence.compaction_boundary_respected is True
    assert evidence.forbidden_payload_observed is False
    assert evidence.reason_codes == (
        "runner_completed",
        "antecedent_present",
        "followup_present",
        "compaction_boundary_respected",
        "forbidden_payload_absent",
        "fallback_none",
    )

    serialized = json.dumps(
        evidence.model_dump(by_alias=True, mode="json"),
        ensure_ascii=False,
        sort_keys=True,
    )
    assert "Raw pre-boundary detail" not in serialized
    assert "vague follow-up prompts resolve" not in serialized


def test_pregate8_canary_records_private_payload_rejection_without_leaking_raw_values() -> None:
    from magi_agent.gates.pregate8_continuity_canary import (
        build_pre_gate8_continuity_canary_evidence,
    )

    evidence = build_pre_gate8_continuity_canary_evidence(
        _stub_runner_result(rejected_entry_count=2),
        adk_session_texts=["Safe user text survives."],
        model_visible_message="continue",
        expected_antecedent="Safe user text survives.",
        current_followup="continue",
        forbidden_payloads=(
            "/workspace/private",
            "REDACT_ME_SECRET_SENTINEL",
        ),
        require_rejected_entries=True,
        fallback_status="none",
    )

    assert evidence.status == "pass"
    assert evidence.rejected_entry_count == 2
    assert evidence.private_payload_rejected is True
    assert evidence.forbidden_payload_observed is False
    assert evidence.antecedent_present_in_adk_session is True

    serialized = json.dumps(
        evidence.model_dump(by_alias=True, mode="json"),
        ensure_ascii=False,
        sort_keys=True,
    )
    assert "Safe user text survives." not in serialized
    assert "/workspace/private" not in serialized
    assert "REDACT_ME_SECRET_SENTINEL" not in serialized


def test_pregate8_canary_fails_closed_when_antecedent_is_missing() -> None:
    from magi_agent.gates.pregate8_continuity_canary import (
        build_pre_gate8_continuity_canary_evidence,
    )

    evidence = build_pre_gate8_continuity_canary_evidence(
        _stub_runner_result(),
        adk_session_texts=["Earlier turn: unrelated content."],
        model_visible_message="아까 말한 그거 어떻게 하면돼?",
        expected_antecedent="missing antecedent label",
        current_followup="아까 말한 그거",
        fallback_status="none",
    )

    assert evidence.status == "fail"
    assert "antecedent_missing" in evidence.reason_codes
    serialized = json.dumps(
        evidence.model_dump(by_alias=True, mode="json"),
        ensure_ascii=False,
        sort_keys=True,
    )
    assert "missing antecedent label" not in serialized


def test_pregate8_canary_evidence_cannot_forge_authority_flags() -> None:
    from magi_agent.gates.pregate8_continuity_canary import (
        PreGate8ContinuityCanaryEvidence,
    )

    forged = PreGate8ContinuityCanaryEvidence.model_construct(
        status="pass",
        localOnly=False,
        diagnosticOnly=False,
        responseAuthority="python",
        fallbackStatus="none",
        importedEventCount=1,
        rejectedEntryCount=0,
        compactionApplied=False,
        projectionDigest="sha256:" + "1" * 64,
        modelVisibleDigest="sha256:" + "2" * 64,
        sourceTranscriptHeadDigest="sha256:" + "3" * 64,
        observedAdkSessionDigest="sha256:" + "4" * 64,
        observedModelVisibleDigest="sha256:" + "5" * 64,
        authorityFlags={
            "transcriptWriteAllowed": True,
            "sseWriteAllowed": True,
            "dbWriteAllowed": True,
            "memoryWriteAllowed": True,
            "workspaceMutationAllowed": True,
            "childExecutionAllowed": True,
            "channelDeliveryAllowed": True,
        },
    )

    assert forged.local_only is True
    assert forged.diagnostic_only is True
    assert forged.response_authority == "none"
    assert forged.authority_flags.transcript_write_allowed is False
    assert forged.authority_flags.memory_write_allowed is False
    assert forged.authority_flags.channel_delivery_allowed is False


def test_pregate8_canary_evidence_rejects_raw_text_in_digest_fields() -> None:
    from magi_agent.gates.pregate8_continuity_canary import (
        PreGate8ContinuityCanaryEvidence,
    )

    base = {
        "status": "pass",
        "fallbackStatus": "none",
        "importedEventCount": 1,
        "rejectedEntryCount": 0,
        "compactionApplied": False,
        "projectionDigest": "sha256:" + "1" * 64,
        "modelVisibleDigest": "sha256:" + "2" * 64,
        "sourceTranscriptHeadDigest": "sha256:" + "3" * 64,
        "observedAdkSessionDigest": "sha256:" + "4" * 64,
        "observedModelVisibleDigest": "sha256:" + "5" * 64,
        "antecedentDigest": "sha256:" + "6" * 64,
        "currentFollowupDigest": "sha256:" + "7" * 64,
    }

    for field in (
        "projectionDigest",
        "modelVisibleDigest",
        "sourceTranscriptHeadDigest",
        "observedAdkSessionDigest",
        "observedModelVisibleDigest",
        "antecedentDigest",
        "currentFollowupDigest",
    ):
        with pytest.raises(ValueError, match="sha256 digests"):
            PreGate8ContinuityCanaryEvidence.model_validate(
                base | {field: "raw antecedent or prompt text"}
            )


def test_pregate8_canary_import_boundary_is_pure_local_contract_only() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

module = importlib.import_module("magi_agent.gates.pregate8_continuity_canary")
assert hasattr(module, "build_pre_gate8_continuity_canary_evidence")

forbidden_prefixes = (
    "fastapi",
    "starlette",
    "requests",
    "httpx",
    "urllib",
    "socket",
    "google.adk",
    "google.genai",
    "magi_agent.transport",
    "magi_agent.tools",
    "magi_agent.toolhost",
    "magi_agent.memory",
    "magi_agent.workspace",
    "magi_agent.browser",
    "magi_agent.channels",
)

loaded = tuple(sys.modules)
violations = [
    name
    for name in loaded
    if any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden_prefixes)
]
assert not violations, violations
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
