from __future__ import annotations

import json
import subprocess
import sys

from magi_agent.evidence.citation_audit import (
    CitationAuditRequest,
    audit_citations,
    public_citation_audit_report,
)
from magi_agent.evidence.source_ledger import LocalResearchSourceLedger


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
            "evidenceType": "SourceInspection",
            "kind": "web_fetch",
            "uri": "https://docs.example.test/private?token=sk-secret",
            "title": "Example Docs",
            "snippets": ["private source says the feature is default-off"],
            "inspected": True,
            "metadata": {"authorization": "Bearer unsafe"},
        }
    )
    return ledger


def test_citation_to_inspected_source_passes_audit_only() -> None:
    result = audit_citations(
        CitationAuditRequest(
            contractId="research-citation-audit",
            turnId="turn-1",
            citedRefs=("src_2",),
            sourceLedger=_ledger(),
        )
    )

    assert result.ok is True
    assert result.verdict.state == "pass"
    assert result.verdict.enforcement == "audit"
    assert result.block_mode is False
    assert result.final_answer_mutated is False
    assert result.user_visible_enforcement_actions == ()
    assert result.audit_items[0].status == "pass"
    assert result.audit_items[0].source_id == "src_2"
    assert result.audit_items[0].failure_code is None
    assert result.verdict.matched_evidence[0].type == "SourceInspection"


def test_citation_to_uninspected_source_returns_audit_failure_not_block() -> None:
    result = audit_citations(
        CitationAuditRequest(
            contractId="research-citation-audit",
            turnId="turn-1",
            citedRefs=("src_1",),
            sourceLedger=_ledger(),
        )
    )

    assert result.ok is False
    assert result.verdict.state == "failed"
    assert result.verdict.enforcement == "audit"
    assert result.block_mode is False
    assert result.final_answer_mutated is False
    assert result.user_visible_enforcement_actions == ()
    assert result.audit_items[0].status == "failure"
    assert result.audit_items[0].failure_code == "EVIDENCE_CONTRACT_FIELD_MISMATCH"
    assert result.verdict.failures[0].code == "EVIDENCE_CONTRACT_FIELD_MISMATCH"


def test_missing_source_ref_returns_audit_missing_not_block() -> None:
    result = audit_citations(
        CitationAuditRequest(
            contractId="research-citation-audit",
            turnId="turn-1",
            citedRefs=("src_99",),
            sourceLedger=_ledger(),
        )
    )

    assert result.ok is False
    assert result.verdict.state == "missing"
    assert result.verdict.enforcement == "audit"
    assert result.block_mode is False
    assert result.final_answer_mutated is False
    assert result.user_visible_enforcement_actions == ()
    assert result.audit_items[0].status == "missing"
    assert result.audit_items[0].failure_code == "EVIDENCE_CONTRACT_MISSING"
    assert result.verdict.failures[0].code == "EVIDENCE_CONTRACT_MISSING"


def test_mixed_inspected_and_uninspected_citations_audit_failure_not_block() -> None:
    result = audit_citations(
        CitationAuditRequest(
            contractId="research-citation-audit",
            turnId="turn-1",
            citedRefs=("src_2", "src_1"),
            sourceLedger=_ledger(),
        )
    )

    assert result.ok is False
    assert result.verdict.state == "failed"
    assert result.verdict.enforcement == "audit"
    assert result.block_mode is False
    assert result.final_answer_mutated is False
    assert result.user_visible_enforcement_actions == ()
    assert [item.status for item in result.audit_items] == ["pass", "failure"]
    assert [item.source_id for item in result.audit_items] == ["src_2", "src_1"]
    assert result.audit_items[0].failure_code is None
    assert result.audit_items[1].failure_code == "EVIDENCE_CONTRACT_FIELD_MISMATCH"
    assert result.verdict.matched_evidence[0].type == "SourceInspection"
    assert result.verdict.failures[0].code == "EVIDENCE_CONTRACT_FIELD_MISMATCH"


def test_mixed_inspected_and_missing_citations_audit_missing_not_block() -> None:
    result = audit_citations(
        CitationAuditRequest(
            contractId="research-citation-audit",
            turnId="turn-1",
            citedRefs=("src_2", "src_99"),
            sourceLedger=_ledger(),
        )
    )

    assert result.ok is False
    assert result.verdict.state == "missing"
    assert result.verdict.enforcement == "audit"
    assert result.block_mode is False
    assert result.final_answer_mutated is False
    assert result.user_visible_enforcement_actions == ()
    assert [item.status for item in result.audit_items] == ["pass", "missing"]
    assert [item.source_id for item in result.audit_items] == ["src_2", "src_99"]
    assert result.audit_items[0].failure_code is None
    assert result.audit_items[1].failure_code == "EVIDENCE_CONTRACT_MISSING"
    assert result.verdict.matched_evidence[0].type == "SourceInspection"
    assert result.verdict.failures[0].code == "EVIDENCE_CONTRACT_MISSING"


def test_public_citation_audit_report_redacts_source_details_and_has_no_enforcement() -> None:
    result = audit_citations(
        CitationAuditRequest(
            contractId="research-citation-audit",
            turnId="turn-1",
            citedRefs=("src_2", "src_99"),
            sourceLedger=_ledger(),
        )
    )

    report = public_citation_audit_report(result)
    dumped = json.dumps(report.model_dump(by_alias=True), sort_keys=True)

    assert report.enforcement == "audit"
    assert report.block_mode is False
    assert report.final_answer_mutated is False
    assert report.user_visible_enforcement_actions == ()
    assert [item.source_id for item in report.audit_items] == ["src_2", "src_99"]
    assert "docs.example.test/private" not in dumped
    assert "private source" not in dumped
    assert "sk-secret" not in dumped
    assert "Bearer unsafe" not in dumped


def test_citation_audit_import_stays_adk_toolhost_fetch_memory_route_free() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

module = importlib.import_module("magi_agent.evidence.citation_audit")
assert hasattr(module, "audit_citations")

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
)
loaded = [
    module_name
    for module_name in sys.modules
    if any(module_name == prefix or module_name.startswith(f"{prefix}.") for prefix in forbidden_prefixes)
]
if loaded:
    raise AssertionError(f"citation audit import loaded forbidden modules: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
