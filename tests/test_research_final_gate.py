from __future__ import annotations

import json
import subprocess
import sys

from magi_agent.evidence.research_final_gate import (
    ResearchClaimRef,
    ResearchFinalGateRequest,
    evaluate_research_final_gate,
)
from magi_agent.evidence.source_ledger import LocalResearchSourceLedger
from magi_agent.evidence.citation_audit import (
    CitationAuditItem,
    CitationAuditRequest,
    CitationAuditResult,
    audit_citations,
)


def _ledger() -> LocalResearchSourceLedger:
    ledger = LocalResearchSourceLedger(
        ledgerId="research-ledger-1",
        sessionId="session-1",
        turnId="turn-1",
        agentRole="research",
    )
    ledger.record_source(
        {
            "turnId": "turn-1",
            "toolName": "WebSearch",
            "evidenceType": "WebSearch",
            "kind": "web_search",
            "uri": "search:latest docs",
            "title": "Search result only",
            "inspected": False,
        }
    )
    ledger.record_source(
        {
            "turnId": "turn-1",
            "toolName": "WebFetch",
            "evidenceType": "SourceInspection",
            "kind": "web_fetch",
            "uri": "https://docs.example.test/current",
            "title": "Current Docs",
            "snippets": ["raw private source says the feature is default-off"],
            "inspected": True,
            "metadata": {"safeLabel": "docs"},
        }
    )
    return ledger


def _ledger_with_clock() -> LocalResearchSourceLedger:
    ledger = _ledger()
    ledger.record_source(
        {
            "turnId": "turn-1",
            "toolName": "Clock",
            "evidenceType": "Clock",
            "kind": "clock",
            "uri": "clock://turn-1",
            "title": "Turn time",
            "inspected": True,
        }
    )
    return ledger


def _ledger_with_other_turn_url() -> LocalResearchSourceLedger:
    ledger = _ledger()
    ledger.record_source(
        {
            "turnId": "turn-2",
            "toolName": "WebFetch",
            "evidenceType": "SourceInspection",
            "kind": "web_fetch",
            "uri": "https://other-turn.example.test/source",
            "title": "Other turn source",
            "inspected": True,
        }
    )
    return ledger


def _ledger_with_other_turn_clock() -> LocalResearchSourceLedger:
    ledger = _ledger()
    ledger.record_source(
        {
            "turnId": "turn-2",
            "toolName": "Clock",
            "evidenceType": "Clock",
            "kind": "clock",
            "uri": "clock://turn-2",
            "title": "Other turn time",
            "inspected": True,
        }
    )
    return ledger


def _request(
    *,
    mode: str = "local_block_intent",
    final_answer: str = "The feature is default-off [src_2].",
    claim_refs: tuple[ResearchClaimRef, ...] = (
        ResearchClaimRef(claimId="claim:default", citedRefs=("src_2",)),
    ),
    cited_refs: tuple[str, ...] = ("src_2",),
) -> ResearchFinalGateRequest:
    return ResearchFinalGateRequest(
        contractId="research-final-gate",
        turnId="turn-1",
        mode=mode,
        candidateFinalAnswer=final_answer,
        extractedClaimRefs=claim_refs,
        citedRefs=cited_refs,
        sourceLedger=_ledger(),
    )


def test_supplied_stale_passing_citation_audit_cannot_bypass_current_missing_ref() -> None:
    ledger = _ledger()
    stale_passing_audit = audit_citations(
        CitationAuditRequest(
            contractId="research-final-gate",
            turnId="turn-1",
            citedRefs=("src_2",),
            sourceLedger=ledger,
        )
    )

    result = evaluate_research_final_gate(
        ResearchFinalGateRequest(
            contractId="research-final-gate",
            turnId="turn-1",
            mode="local_block_intent",
            candidateFinalAnswer="The feature is default-off [src_99].",
            extractedClaimRefs=(ResearchClaimRef(claimId="claim:missing", citedRefs=("src_99",)),),
            citedRefs=("src_99",),
            sourceLedger=ledger,
            citationAuditResult=stale_passing_audit,
        )
    )

    assert result.status == "local_block_intent"
    assert "missing_source_ref" in result.reason_codes


def test_final_answer_src_refs_are_audited_even_when_metadata_cites_valid_ref() -> None:
    result = evaluate_research_final_gate(
        _request(
            final_answer="The feature is default-off [src_99].",
            claim_refs=(ResearchClaimRef(claimId="claim:metadata", citedRefs=("src_2",)),),
            cited_refs=("src_2",),
        )
    )

    assert result.status == "local_block_intent"
    assert "missing_source_ref" in result.reason_codes
    assert "src_99" in result.cited_refs


def test_malformed_final_answer_source_refs_block_locally() -> None:
    for final_answer in ("See [src_01].", "See [src_0]."):
        result = evaluate_research_final_gate(
            _request(
                final_answer=final_answer,
                claim_refs=(),
                cited_refs=(),
            )
        )

        assert result.status == "local_block_intent"
        assert "malformed_source_ref" in result.reason_codes


def test_factual_final_answer_without_any_source_ref_blocks_locally() -> None:
    result = evaluate_research_final_gate(
        _request(
            final_answer="The latest OpenMagi runtime is production ready.",
            claim_refs=(),
            cited_refs=(),
        )
    )

    assert result.status == "local_block_intent"
    assert "factual_claim_missing_source_ref" in result.reason_codes


def test_common_factual_no_ref_answers_block_locally() -> None:
    factual_answers = (
        "OpenMagi launched on May 24, 2026.",
        "The price: $10.",
        "Version 2.4 ships Monday.",
        "OpenMagi uses Supabase.",
        "The runtime runs on k3s.",
        "Vercel deploys the web app.",
    )

    for final_answer in factual_answers:
        result = evaluate_research_final_gate(
            _request(
                final_answer=final_answer,
                claim_refs=(),
                cited_refs=(),
            )
        )

        assert result.status == "local_block_intent"
        assert "factual_claim_missing_source_ref" in result.reason_codes


def test_public_projection_rejects_private_request_ids_and_sanitizes_supplied_audit() -> None:
    ledger = _ledger()
    stale_audit = audit_citations(
        CitationAuditRequest(
            contractId="research-final-gate",
            turnId="turn-1",
            citedRefs=("src_2",),
            sourceLedger=ledger,
        )
    )
    tampered_payload = stale_audit.model_dump(by_alias=True, mode="python", warnings=False)
    tampered_payload["auditItems"][0]["sourceId"] = "https://private.example.test/path?session=abc"
    tampered_payload["auditItems"][0]["evidenceType"] = "/Users/kevin/private-evidence"
    tampered_audit = type(stale_audit).model_validate(tampered_payload)

    try:
        ResearchFinalGateRequest(
            contractId="/Users/kevin/private",
            turnId="turn-1",
            mode="audit",
            candidateFinalAnswer="Claim [src_2].",
            sourceLedger=ledger,
        )
    except ValueError:
        pass
    else:  # pragma: no cover - assertion clarity
        raise AssertionError("private contract id must be rejected")

    result = evaluate_research_final_gate(
        ResearchFinalGateRequest(
            contractId="research-final-gate",
            turnId="turn-1",
            mode="audit",
            candidateFinalAnswer="Brief note.",
            sourceLedger=ledger,
            citationAuditResult=tampered_audit,
        )
    )
    projection = result.public_projection()

    assert "private.example.test" not in json.dumps(projection, sort_keys=True)
    assert "/Users/kevin/private-evidence" not in json.dumps(projection, sort_keys=True)


def test_public_projection_sanitizes_forged_result_payloads() -> None:
    result = evaluate_research_final_gate(_request())
    forged_claim = ResearchClaimRef.model_construct(
        claimId="/Users/kevin/private-claim",
        citedRefs=("/Users/kevin/private-cited-ref",),
        requiresFreshSource="/Users/kevin/private-bool",
        freshSourceRefs=("/Users/kevin/private-fresh-ref",),
    )
    forged_audit = CitationAuditResult.model_construct(
        contract_id="research-final-gate",
        turn_id="turn-1",
        ok="/Users/kevin/private-ok",
        enforcement="/Users/kevin/private-enforcement",
        verdict={},
        audit_items=(
            CitationAuditItem.model_construct(
                source_id="session:abc123",
                status="/Users/kevin/private-status",
                inspected="/Users/kevin/private-inspected",
                evidence_type="/Users/kevin/private-evidence",
                failure_code="/Users/kevin/private-failure",
            ),
        ),
    )
    forged = result.model_copy(
        update={
            "reason_codes": ("/Users/kevin/private-reason",),
            "cited_refs": ("/Users/kevin/private-cited",),
            "output_link_digests": ("https://private.example.test/link",),
            "final_answer_digest": "/Users/kevin/private-answer",
            "extracted_claim_refs": (forged_claim,),
            "citation_audit_result": forged_audit,
        }
    )
    projection = forged.public_projection()
    dumped = json.dumps(projection, sort_keys=True)

    assert "/Users/kevin" not in dumped
    assert "private.example.test" not in dumped
    assert "session:abc123" not in dumped
    assert projection["finalAnswerDigest"].startswith("sha256:")
    assert projection["outputLinkDigests"][0].startswith("sha256:")
    assert projection["claimRefs"][0]["requiresFreshSource"] is False
    assert projection["citationAudit"]["ok"] is False
    assert projection["citationAudit"]["enforcement"] == "audit"
    assert projection["citationAudit"]["auditItems"][0]["status"] == "failure"
    assert projection["citationAudit"]["auditItems"][0]["inspected"] is False


def test_output_links_are_scoped_to_current_turn_source_ledger() -> None:
    result = evaluate_research_final_gate(
        ResearchFinalGateRequest(
            contractId="research-final-gate",
            turnId="turn-1",
            mode="local_block_intent",
            candidateFinalAnswer=(
                "The other turn source says it. "
                "https://other-turn.example.test/source"
            ),
            sourceLedger=_ledger_with_other_turn_url(),
        )
    )

    assert result.status == "local_block_intent"
    assert "output_link_not_in_source_ledger" in result.reason_codes


def test_result_copy_and_construct_cannot_forge_final_answer_blocking_authority() -> None:
    result = evaluate_research_final_gate(_request())
    copied = result.model_copy(update={"final_answer_blocking_enabled": True})
    constructed = type(result).model_construct(
        contractId="research-final-gate",
        turnId="turn-1",
        mode="local_block_intent",
        status="local_block_intent",
        ok=False,
        blockIntent=True,
        approvalRequiredBlockIntent=False,
        finalAnswerBlockingEnabled=True,
        sourceLedger=_ledger(),
        finalAnswerDigest="sha256:" + "0" * 64,
    )

    assert copied.final_answer_blocking_enabled is False
    assert constructed.final_answer_blocking_enabled is False
    assert set(copied.authority_flags.model_dump(by_alias=True).values()) == {False}


def test_clock_fresh_source_ref_does_not_become_citation_audit_failure() -> None:
    result = evaluate_research_final_gate(
        ResearchFinalGateRequest(
            contractId="research-final-gate",
            turnId="turn-1",
            mode="local_block_intent",
            candidateFinalAnswer="Today, the feature is default-off [src_2].",
            extractedClaimRefs=(
                ResearchClaimRef(
                    claimId="claim:current",
                    citedRefs=("src_2",),
                    requiresFreshSource=True,
                    freshSourceRefs=("src_3",),
                ),
            ),
            citedRefs=("src_2",),
            sourceLedger=_ledger_with_clock(),
        )
    )

    assert result.status == "passed"
    assert "uninspected_source_ref" not in result.reason_codes


def test_fresh_source_refs_are_scoped_to_current_turn() -> None:
    result = evaluate_research_final_gate(
        ResearchFinalGateRequest(
            contractId="research-final-gate",
            turnId="turn-1",
            mode="local_block_intent",
            candidateFinalAnswer="Today, the feature is default-off [src_2].",
            extractedClaimRefs=(
                ResearchClaimRef(
                    claimId="claim:current",
                    citedRefs=("src_2",),
                    requiresFreshSource=True,
                    freshSourceRefs=("src_3",),
                ),
            ),
            citedRefs=("src_2",),
            sourceLedger=_ledger_with_other_turn_clock(),
        )
    )

    assert result.status == "local_block_intent"
    assert "volatile_claim_missing_fresh_source" in result.reason_codes


def test_default_off_skips_without_block_intent_or_authority() -> None:
    result = evaluate_research_final_gate(_request(mode="off"))

    assert result.status == "skipped"
    assert result.ok is True
    assert result.block_intent is False
    assert result.final_answer_blocking_enabled is False
    assert set(result.authority_flags.model_dump(by_alias=True).values()) == {False}
    assert result.public_projection()["reasonCodes"] == ["research_final_gate_off"]


def test_missing_source_ref_returns_local_block_intent() -> None:
    result = evaluate_research_final_gate(
        _request(
            claim_refs=(ResearchClaimRef(claimId="claim:missing", citedRefs=("src_99",)),),
            cited_refs=("src_99",),
        )
    )

    assert result.status == "local_block_intent"
    assert result.ok is False
    assert result.block_intent is True
    assert result.final_answer_blocking_enabled is False
    assert "missing_source_ref" in result.reason_codes
    assert result.authority_flags.final_answer_blocked is False


def test_inspected_source_ref_passes_without_block_intent() -> None:
    result = evaluate_research_final_gate(_request())

    assert result.status == "passed"
    assert result.ok is True
    assert result.block_intent is False
    assert result.reason_codes == ("research_final_gate_passed",)
    assert result.authority_flags.final_answer_blocked is False


def test_each_extracted_claim_requires_own_supporting_citation_ref() -> None:
    result = evaluate_research_final_gate(
        _request(
            final_answer="One claim is supported [src_2].",
            claim_refs=(
                ResearchClaimRef(claimId="claim:supported", citedRefs=("src_2",)),
                ResearchClaimRef(claimId="claim:unsupported", citedRefs=()),
            ),
            cited_refs=("src_2",),
        )
    )

    assert result.status == "local_block_intent"
    assert "unsupported_claim_missing_citation_ref" in result.reason_codes


def test_discovery_only_uninspected_source_ref_fails_locally() -> None:
    result = evaluate_research_final_gate(
        _request(
            final_answer="The search result supports it [src_1].",
            claim_refs=(ResearchClaimRef(claimId="claim:uninspected", citedRefs=("src_1",)),),
            cited_refs=("src_1",),
        )
    )

    assert result.status == "local_block_intent"
    assert result.ok is False
    assert result.block_intent is True
    assert "uninspected_source_ref" in result.reason_codes


def test_audit_mode_reports_failures_without_final_answer_authority_or_block() -> None:
    result = evaluate_research_final_gate(
        _request(
            mode="audit",
            claim_refs=(ResearchClaimRef(claimId="claim:missing", citedRefs=("src_99",)),),
            cited_refs=("src_99",),
        )
    )

    assert result.status == "audit_failed"
    assert result.ok is False
    assert result.block_intent is False
    assert result.approval_required_block_intent is False
    assert result.final_answer_blocking_enabled is False
    assert set(result.authority_flags.model_dump(by_alias=True).values()) == {False}


def test_volatile_current_claim_without_fresh_source_blocks_locally() -> None:
    result = evaluate_research_final_gate(
        _request(
            final_answer="Today, the feature is default-off [src_2].",
            claim_refs=(
                ResearchClaimRef(
                    claimId="claim:current",
                    citedRefs=("src_2",),
                    requiresFreshSource=True,
                ),
            ),
            cited_refs=("src_2",),
        )
    )

    assert result.status == "local_block_intent"
    assert result.ok is False
    assert "volatile_claim_missing_fresh_source" in result.reason_codes


def test_source_looking_url_in_output_not_in_ledger_blocks_locally() -> None:
    result = evaluate_research_final_gate(
        _request(
            final_answer=(
                "The feature is default-off [src_2]. See "
                "https://unlogged.example.test/private?token=sk-secret for details."
            ),
        )
    )

    assert result.status == "local_block_intent"
    assert result.ok is False
    assert "output_link_not_in_source_ledger" in result.reason_codes


def test_public_projection_exposes_safe_refs_reasons_and_digests_only() -> None:
    result = evaluate_research_final_gate(
        _request(
            final_answer=(
                "The feature is default-off [src_2]. "
                "https://private.example.test/path?api_key=sk-secret "
                "hidden_reasoning should never appear."
            ),
            claim_refs=(
                ResearchClaimRef(
                    claimId="claim:private",
                    citedRefs=("src_2",),
                    requiresFreshSource=True,
                ),
            ),
        )
    )

    projection = result.public_projection()
    dumped = json.dumps(projection, sort_keys=True)

    assert projection["claimRefs"][0]["claimId"] == "claim:private"
    assert projection["claimRefs"][0]["citedRefs"] == ["src_2"]
    assert "volatile_claim_missing_fresh_source" in projection["reasonCodes"]
    assert projection["finalAnswerDigest"].startswith("sha256:")
    assert projection["outputLinkDigests"][0].startswith("sha256:")
    assert "The feature is default-off" not in dumped
    assert "raw private source" not in dumped
    assert "docs.example.test/current" not in dumped
    assert "private.example.test" not in dumped
    assert "sk-secret" not in dumped
    assert "api_key" not in dumped
    assert "hidden_reasoning" not in dumped
    assert "/Users/kevin" not in dumped


def test_approval_required_block_mode_is_still_non_production_and_authority_false() -> None:
    result = evaluate_research_final_gate(
        _request(
            mode="approval_required_block",
            claim_refs=(ResearchClaimRef(claimId="claim:missing", citedRefs=("src_99",)),),
            cited_refs=("src_99",),
        )
    )

    assert result.status == "approval_required_block_intent"
    assert result.ok is False
    assert result.block_intent is True
    assert result.approval_required_block_intent is True
    assert result.final_answer_blocking_enabled is False
    assert set(result.authority_flags.model_dump(by_alias=True).values()) == {False}


def test_research_final_gate_import_stays_live_runtime_free() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

module = importlib.import_module("magi_agent.evidence.research_final_gate")
assert hasattr(module, "evaluate_research_final_gate")

forbidden_prefixes = (
    "google.adk",
    "magi_agent.adk_bridge.runner_adapter",
    "magi_agent.tools.dispatcher",
    "magi_agent.tools.registry",
    "magi_agent.runtime",
    "magi_agent.routing",
    "magi_agent.transport.chat",
    "magi_agent.transport.tools",
    "magi_agent.memory",
    "magi_agent.browser",
    "magi_agent.search",
    "magi_agent.fetch",
    "magi_agent.channels",
    "magi_agent.workspace",
)
loaded = [
    module_name
    for module_name in sys.modules
    if any(module_name == prefix or module_name.startswith(f"{prefix}.") for prefix in forbidden_prefixes)
]
if loaded:
    raise AssertionError(f"research final gate import loaded forbidden modules: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
