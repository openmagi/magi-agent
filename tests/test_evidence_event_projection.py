from __future__ import annotations

import json
import subprocess
import sys

from magi_agent.evidence.citation_audit import (
    CitationAuditRequest,
    audit_citations,
)
from magi_agent.evidence.event_projection import (
    project_evidence_verdict_rule_event,
    project_source_ledger_events,
    project_verifier_result_rule_event,
)
from runtime_issuance_support import issue_test_runtime_authority
from magi_agent.evidence.source_ledger import LocalResearchSourceLedger
from magi_agent.evidence.types import (
    EvidenceContractFailure,
    EvidenceContractVerdict,
    EvidenceRecord,
    EvidenceRequirement,
    EvidenceSource,
)
from magi_agent.harness.verifier_bus import VerifierResultMetadata
from magi_agent.research.event_projection import (
    project_citation_audit_rule_events,
    project_source_proof_rule_events,
)
from magi_agent.research.source_proof import (
    ResearchSourceOpenReceiptRef,
    ResearchSourceProofRequirement,
    verify_research_source_proof,
)
from magi_agent.transport.sse import InMemorySseWriter


def _digest(char: str = "a") -> str:
    return "sha256:" + (char * 64)


def _runtime_authority(*scopes: str):
    return issue_test_runtime_authority(
        authority_id="authority:test-evidence-event-projection",
        scopes=scopes,
    )


def _payloads(events: list[dict[str, object]] | tuple[dict[str, object], ...]) -> list[dict[str, object]]:
    writer = InMemorySseWriter()
    for event in events:
        writer.agent(event)
    return [
        json.loads(line.removeprefix("data: "))
        for line in writer.body.splitlines()
        if line.startswith("data: ")
    ]


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
            "uri": "search:example docs",
            "title": "Search result",
            "inspected": False,
        }
    )
    ledger.record_source(
        {
            "turnId": "turn-1",
            "toolName": "WebFetch",
            "toolUseId": "toolu_source_1",
            "evidenceType": "SourceInspection",
            "kind": "web_fetch",
            "uri": "https://docs.example.test/private?" + "token=unsafe",
            "title": "Private source title",
            "snippets": ["raw source page says the feature is default-off"],
            "contentHash": _digest("b"),
            "contentType": "text/html",
            "trustTier": "official",
            "inspected": True,
            "metadata": {"authorization": "Bearer " + "unsafe"},
        }
    )
    return ledger


def test_opened_source_record_projects_digest_only_source_inspected_event() -> None:
    events = project_source_ledger_events(_ledger())
    payloads = _payloads(events)

    assert [payload["type"] for payload in payloads] == ["source_inspected"]
    source = payloads[0]["source"]
    assert source["sourceId"] == "src_2"
    assert source["kind"] == "web_fetch"
    assert source["uri"] == "ref:src_2"
    assert source["contentHash"] == _digest("b")
    assert source["trustTier"] == "official"
    assert source["contentType"] == "text/html"

    encoded = json.dumps(payloads, sort_keys=True)
    assert "docs.example.test" not in encoded
    assert "raw source page" not in encoded
    assert "Private source title" not in encoded
    assert "Bearer " not in encoded
    assert "token=unsafe" not in encoded


def test_url_only_source_record_is_omitted_without_receipt_or_evidence_ref() -> None:
    ledger = LocalResearchSourceLedger(
        ledgerId="research-ledger-1",
        sessionId="session-1",
        turnId="turn-1",
    )
    ledger.record_source(
        {
            "turnId": "turn-1",
            "toolName": "WebFetch",
            "evidenceType": "SourceInspection",
            "kind": "web_fetch",
            "uri": "https://docs.example.test/url-only",
            "inspected": True,
        }
    )

    assert project_source_ledger_events(ledger) == ()


def test_source_event_does_not_use_metadata_evidence_id_as_digest_fallback() -> None:
    ledger = LocalResearchSourceLedger(
        ledgerId="research-ledger-1",
        sessionId="session-1",
        turnId="turn-1",
    )
    ledger.record_source(
        {
            "turnId": "turn-1",
            "toolName": "WebFetch",
            "evidenceType": "SourceInspection",
            "kind": "web_fetch",
            "uri": "https://docs.example.test/private",
            "inspected": True,
            "metadata": {"evidenceId": "ghp_" + ("a" * 24)},
        }
    )

    assert project_source_ledger_events(ledger) == ()


def test_citation_audit_without_runtime_receipt_projects_pending_rule_checks() -> None:
    pass_result = audit_citations(
        CitationAuditRequest(
            contractId="research-citation-audit",
            turnId="turn-1",
            citedRefs=("src_2",),
            sourceLedger=_ledger(),
        )
    )
    fail_result = audit_citations(
        CitationAuditRequest(
            contractId="research-citation-audit",
            turnId="turn-1",
            citedRefs=("src_1", "src_99"),
            sourceLedger=_ledger(),
        )
    )

    payloads = _payloads(
        project_citation_audit_rule_events(pass_result)
        + project_citation_audit_rule_events(fail_result)
    )

    assert [payload["type"] for payload in payloads] == ["rule_check", "rule_check"]
    assert [payload["verdict"] for payload in payloads] == ["pending", "pending"]
    assert payloads[0]["ruleId"] == "claim-citation-gate"
    assert payloads[1]["ruleId"] == "claim-citation-gate"
    assert "evidenceRef" not in payloads[0]
    assert "evidenceRef" not in payloads[1]
    assert "passed=1" in payloads[0]["detail"]
    assert "missing=1" in payloads[1]["detail"]

    encoded = json.dumps(payloads, sort_keys=True)
    assert "docs.example.test" not in encoded
    assert "raw source page" not in encoded
    assert "Bearer " not in encoded


def test_consistent_citation_success_requires_runtime_authority_to_project_ok() -> None:
    pass_result = audit_citations(
        CitationAuditRequest(
            contractId="research-citation-audit",
            turnId="turn-1",
            citedRefs=("src_2",),
            sourceLedger=_ledger(),
        )
    )
    receipt_ref = "receipt:sha256:" + ("1" * 64)

    without_authority = _payloads(
        project_citation_audit_rule_events(pass_result, evidence_refs=(receipt_ref,))
    )[0]
    with_authority = _payloads(
        project_citation_audit_rule_events(
            pass_result,
            evidence_refs=(receipt_ref,),
            runtime_authority=_runtime_authority("citation_rule_check"),
        )
    )[0]

    assert without_authority["type"] == "runtime_trace"
    assert without_authority["reasonCode"] == "public_projection_missing_receipt"
    assert with_authority["ruleId"] == "claim-citation-gate"
    assert with_authority["verdict"] == "ok"
    assert with_authority["evidenceRef"] == receipt_ref
    assert "passed=1" in with_authority["detail"]


def test_receipt_backed_citation_failure_projects_violation_not_pending() -> None:
    fail_result = audit_citations(
        CitationAuditRequest(
            contractId="research-citation-audit",
            turnId="turn-1",
            citedRefs=("src_1", "src_99"),
            sourceLedger=_ledger(),
        )
    )
    receipt_ref = "receipt:sha256:" + ("1" * 64)

    without_authority = _payloads(
        project_citation_audit_rule_events(fail_result, evidence_refs=(receipt_ref,))
    )[0]
    with_authority = _payloads(
        project_citation_audit_rule_events(
            fail_result,
            evidence_refs=(receipt_ref,),
            runtime_authority=_runtime_authority("citation_rule_check"),
        )
    )[0]

    assert without_authority["type"] == "runtime_trace"
    assert without_authority["reasonCode"] == "public_projection_missing_receipt"
    assert with_authority["ruleId"] == "claim-citation-gate"
    assert with_authority["verdict"] == "violation"
    assert with_authority["evidenceRef"] == receipt_ref
    assert "missing=1" in with_authority["detail"]


def test_inconsistent_citation_success_projects_pending_not_ok() -> None:
    pass_result = audit_citations(
        CitationAuditRequest(
            contractId="research-citation-audit",
            turnId="turn-1",
            citedRefs=("src_2",),
            sourceLedger=_ledger(),
        )
    )
    forged = pass_result.model_copy(update={"auditItems": (), "ok": True})

    payload = _payloads(project_citation_audit_rule_events(forged))[0]

    assert payload["ruleId"] == "claim-citation-gate"
    assert payload["verdict"] == "pending"
    assert "evidenceRef" not in payload
    assert "consistency=inconsistent" in payload["detail"]


def test_citation_success_requires_matching_source_inspection_evidence() -> None:
    pass_result = audit_citations(
        CitationAuditRequest(
            contractId="research-citation-audit",
            turnId="turn-1",
            citedRefs=("src_2",),
            sourceLedger=_ledger(),
        )
    )
    unrelated_evidence = EvidenceRecord(
        type="SourceInspection",
        status="ok",
        observedAt=1,
        source=EvidenceSource(kind="tool_trace", toolName="WebFetch"),
        fields={"sourceId": "src_99", "sourceIds": ["src_99"], "inspected": True},
        metadata={"publicSafeFields": ["sourceId", "sourceIds", "inspected"]},
    )
    forged = pass_result.model_copy(
        update={
            "verdict": pass_result.verdict.model_copy(
                update={"matchedEvidence": (unrelated_evidence,)}
            )
        }
    )

    payload = _payloads(project_citation_audit_rule_events(forged))[0]

    assert payload["verdict"] == "pending"
    assert "evidenceRef" not in payload
    assert "consistency=inconsistent" in payload["detail"]


def test_unsupported_claim_verdict_projects_rule_violation_without_raw_failure_text() -> None:
    verdict = EvidenceContractVerdict(
        contractId="unsupported-claim",
        ok=False,
        state="failed",
        enforcement="audit",
        missingRequirements=(),
        matchedEvidence=(),
        failures=(
            EvidenceContractFailure(
                code="EVIDENCE_CONTRACT_FIELD_MISMATCH",
                contractId="unsupported-claim",
                requirementType="SourceInspection",
                message=(
                    "unsupported claim used raw source snapshot at "
                    "/Users/kevin/private/source.txt"
                ),
                metadata={"sourceSnapshot": "full source body"},
            ),
        ),
        requirementCoverage=(),
    )

    payload = _payloads([project_evidence_verdict_rule_event(verdict)])[0]

    assert payload["type"] == "runtime_trace"
    assert payload["reasonCode"] == "public_projection_missing_receipt"
    assert payload["detail"] == "rule_check omitted: missing public evidence receipt"
    encoded = json.dumps(payload, sort_keys=True)
    assert "raw source snapshot" not in encoded
    assert "/Users/kevin" not in encoded
    assert "full source body" not in encoded


def test_inconsistent_evidence_success_without_matched_evidence_projects_violation() -> None:
    verdict = EvidenceContractVerdict(
        contractId="source-claim",
        ok=True,
        state="pass",
        enforcement="audit",
        missingRequirements=(),
        matchedEvidence=(),
        failures=(),
        requirementCoverage=("SourceInspection",),
    )

    payload = _payloads([project_evidence_verdict_rule_event(verdict)])[0]

    assert payload["type"] == "runtime_trace"
    assert payload["reasonCode"] == "public_projection_missing_receipt"
    assert payload["detail"] == "rule_check omitted: missing public evidence receipt"


def test_evidence_success_requires_ok_matched_evidence_for_requirement_coverage() -> None:
    verdict = EvidenceContractVerdict(
        contractId="source-claim",
        ok=True,
        state="pass",
        enforcement="audit",
        missingRequirements=(),
        matchedEvidence=(
            EvidenceRecord(
                type="SourceInspection",
                status="unknown",
                observedAt=1,
                source=EvidenceSource(kind="tool_trace", toolName="WebFetch"),
                fields={"sourceId": "src_1", "inspected": False},
                metadata={"publicSafeFields": ["sourceId", "inspected"]},
            ),
        ),
        failures=(),
        requirementCoverage=("SourceInspection",),
    )

    payload = _payloads([project_evidence_verdict_rule_event(verdict)])[0]

    assert payload["type"] == "runtime_trace"
    assert payload["reasonCode"] == "public_projection_missing_receipt"


def test_stale_source_proof_projects_rule_violation_with_span_count_only() -> None:
    receipt = ResearchSourceOpenReceiptRef.issue_runtime_source_ref(
        runtime_authority=_runtime_authority("research_source_proof"),
        source_ref_id="src_1",
        source_kind="web_fetch",
        receipt_kind="opened_snapshot",
        opened=True,
        content_digest=_digest("c"),
        inspected_at="2026-05-26T09:00:00Z",
        span_refs=("span:pricing", "span:terms"),
        redaction_status="redacted",
        public_label="Public docs",
    )
    requirement = ResearchSourceProofRequirement(
        sourceRefId="src_1",
        allowedSourceKinds=("web_fetch",),
        requiredReceiptKinds=("opened_snapshot",),
        requiredSpanRefs=("span:pricing",),
        notBefore="2026-05-26T10:00:00Z",
        notAfter="2026-05-26T13:00:00Z",
    )
    source_verdict = verify_research_source_proof((requirement,), (receipt,))[0]

    payload = _payloads(project_source_proof_rule_events((source_verdict,)))[0]

    assert payload["type"] == "runtime_trace"
    assert payload["reasonCode"] == "public_projection_missing_receipt"
    assert payload["detail"] == "rule_check omitted: missing public evidence receipt"
    encoded = json.dumps(payload, sort_keys=True)
    assert "docs.example.test" not in encoded
    assert "span:pricing" not in encoded
    assert _digest("c") not in encoded


def test_projection_event_counts_are_bounded() -> None:
    ledger = LocalResearchSourceLedger(
        ledgerId="research-ledger-1",
        sessionId="session-1",
        turnId="turn-1",
    )
    for index in range(80):
        ledger.record_source(
            {
                "turnId": "turn-1",
                "toolName": "WebFetch",
                "evidenceType": "SourceInspection",
                "kind": "web_fetch",
                "uri": f"https://docs.example.test/{index}",
                "inspected": True,
                "contentHash": _digest("a" if index % 2 == 0 else "b"),
            }
        )

    source_events = project_source_ledger_events(ledger)

    assert len(source_events) == 50

    requirements = tuple(
        ResearchSourceProofRequirement(
            sourceRefId=f"src_{index}",
            allowedSourceKinds=("web_fetch",),
            requiredReceiptKinds=("opened_snapshot",),
            requiredSpanRefs=("span:pricing",),
        )
        for index in range(1, 81)
    )
    source_verdicts = verify_research_source_proof(requirements, ())

    rule_events = project_source_proof_rule_events(source_verdicts)

    assert len(rule_events) == 50


def test_verifier_result_projects_rule_check_without_live_authority_or_private_text() -> None:
    result = VerifierResultMetadata(
        verifierId="source-claim-link",
        status="failed",
        publicSummary="hidden reasoning at /Users/kevin/private",
        failureMessage="raw prompt with token=unsafe",
    )

    payload = _payloads([project_verifier_result_rule_event(result)])[0]

    assert payload["type"] == "runtime_trace"
    assert payload["reasonCode"] == "public_projection_missing_receipt"
    assert payload["detail"] == "rule_check omitted: missing public evidence receipt"
    encoded = json.dumps(payload, sort_keys=True)
    assert "hidden reasoning" not in encoded
    assert "/Users/kevin" not in encoded
    assert "token=unsafe" not in encoded


def test_verifier_pass_without_evidence_ref_does_not_project_ok() -> None:
    result = VerifierResultMetadata(verifierId="source-claim-link", status="pass")

    without_ref = _payloads([project_verifier_result_rule_event(result)])[0]
    with_ref = _payloads(
        [project_verifier_result_rule_event(result, evidence_refs=[_digest("d")])]
    )[0]

    assert without_ref["verdict"] == "pending"
    assert with_ref["type"] == "runtime_trace"
    assert with_ref["reasonCode"] == "public_projection_missing_receipt"


def test_verifier_result_requires_runtime_authority_to_project_ok() -> None:
    result = VerifierResultMetadata(verifierId="source-claim-link", status="pass")

    payload = _payloads(
        [
            project_verifier_result_rule_event(
                result,
                evidence_refs=[_digest("d")],
                runtime_authority=_runtime_authority("verifier_result_rule_check"),
            )
        ]
    )[0]

    assert payload["type"] == "rule_check"
    assert payload["verdict"] == "ok"
    assert payload["evidenceRef"] == _digest("d")


def test_evidence_event_projection_import_stays_live_authority_free() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

module = importlib.import_module("magi_agent.evidence.event_projection")
assert hasattr(module, "project_source_ledger_events")

forbidden_prefixes = (
    "google.adk",
    "magi_agent.adk_bridge.runner_adapter",
    "magi_agent.browser",
    "magi_agent.fetch",
    "magi_agent.memory",
    "magi_agent.research",
    "magi_agent.routing",
    "magi_agent.search",
    "magi_agent.tools.dispatcher",
    "magi_agent.tools.registry",
    "magi_agent.transport.chat",
    "magi_agent.transport.tools",
)
loaded = [
    name
    for name in sys.modules
    if any(name == prefix or name.startswith(f"{prefix}.") for prefix in forbidden_prefixes)
]
if loaded:
    raise AssertionError(f"event projection import loaded forbidden live modules: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
